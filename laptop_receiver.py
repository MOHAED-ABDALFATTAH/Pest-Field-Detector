import os
import json
import requests
import queue
from flask import Flask, request, jsonify, Response, send_from_directory, render_template

import database
import node_manager
from logger import log_event
import inference_worker
import anomaly_detector
from flask_cors import CORS

app = Flask(__name__)
SAVE_DIR = "captures"
os.makedirs(SAVE_DIR, exist_ok=True)
CORS(app, resources={r"/api/*": {"origins": "http://127.0.0.1:5500"}})
# Ensure templates directory exists and initialize DB on boot
database.init_db()
anomaly_detector.start_poller(poll_interval=50.0)

# -------------------------------------------------------------------
# 1. RETRO-COMPATIBILITY & REGISTRATION ENDPOINTS
# -------------------------------------------------------------------

@app.route("/api/v1/nodes/register", methods=["POST"])
def register_node():
    """API endpoint to register/update edge nodes."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        node_id = data.get("node_id")
        ip_address = data.get("ip_address") or request.remote_addr
        node_type = data.get("node_type")
        mac_address = data.get("mac_address")
        
        if not node_id:
            return jsonify({"error": "Missing node_id"}), 400
            
        # Infer node type if missing
        if not node_type:
            if node_id.startswith("cam"):
                node_type = "camera"
            elif node_id.startswith("sensor"):
                node_type = "sensor"
            else:
                node_type = "hybrid"
                
        node_manager.register_node(node_id, node_type, ip_address, mac_address)
        log_event("INFO", "Registry", f"Registered/Updated node {node_id} ({node_type}) at {ip_address}", node_id=node_id)
        return jsonify({"status": "registered", "node_id": node_id, "ip_address": ip_address}), 200
    except Exception as e:
        log_event("ERROR", "Registry", f"Failed to register node: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/v1/nodes", methods=["GET"])
def list_nodes():
    """Retrieve all active or inactive registered nodes."""
    try:
        nodes = node_manager.get_active_nodes()
        return jsonify(nodes), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -------------------------------------------------------------------
# 2. IMAGE UPLOAD & INGESTION QUEUE
# -------------------------------------------------------------------

@app.route("/upload", methods=["POST"])
@app.route("/api/v1/upload", methods=["POST"])
def upload():
    """Ingests JPEG binary payload, writes pending capture log, and enqueues task."""
    try:
        # Extract headers with fallback for older client firmware
        node_id = request.headers.get("X-Node-ID")
        if not node_id:
            node_id = request.args.get("node_id", "unknown_cam")
            
        trigger_source = request.headers.get("X-Trigger-Source", "hardware_node")
        image_bytes = request.data
        
        if not image_bytes:
            log_event("WARNING", "Ingest", "Empty upload payload received", node_id=node_id)
            return "Empty Payload", 400

        # Auto-register camera node if we haven't seen it yet
        camera_ip = request.remote_addr
        if node_id != "unknown_cam":
            node_manager.register_node(node_id, "camera", camera_ip)

        # Look for a linked sensor log from the last 15 seconds to associate the context
        sensor_log_id = None
        try:
            with database.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                SELECT id FROM sensor_logs 
                WHERE (julianday(datetime('now', 'localtime')) - julianday(timestamp)) * 86400 < 15.0
                ORDER BY id DESC LIMIT 1
                """)
                row = cursor.fetchone()
                if row:
                    sensor_log_id = row['id']
        except Exception as db_err:
            log_event("DEBUG", "Ingest", f"Could not lookup linked sensor log: {db_err}")

        # Enqueue job
        log_id = inference_worker.enqueue_capture_job(node_id, image_bytes, trigger_source, sensor_log_id)
        
        if log_id is None:
            return jsonify({"status": "error", "message": "Inference queue full. Discarded."}), 503
            
        return jsonify({"status": "accepted", "log_id": log_id}), 202
    except Exception as e:
        log_event("ERROR", "Ingest", f"Exception during upload ingestion: {e}")
        return jsonify({"error": str(e)}), 500

# -------------------------------------------------------------------
# 3. CAMERA PROXY OPERATIONS
# -------------------------------------------------------------------

@app.route("/api/v1/camera/trigger", methods=["POST"])
def trigger_camera():
    """Proxies an HTTP request to command camera node /trigger."""
    node_id = request.args.get("node_id") or request.json.get("node_id") if request.is_json else None
    if not node_id:
        return jsonify({"error": "node_id parameter is required"}), 400
        
    ip_address = node_manager.get_node_ip(node_id)
    if not ip_address:
        return jsonify({"error": f"Node '{node_id}' not found in registry"}), 404
        
    try:
        url = f"http://{ip_address}/trigger"
        log_event("INFO", "Proxy", f"Sending remote trigger to {node_id} at {url}")
        
        # Strictly set a 3.0s timeout to prevent thread blockages
        response = requests.get(url, timeout=3.0)
        
        if response.status_code == 200:
            log_event("INFO", "Proxy", f"Camera {node_id} successfully triggered: {response.text}", node_id=node_id)
            return jsonify({"status": "triggered", "response": response.text}), 200
        else:
            log_event("ERROR", "Proxy", f"Camera responded with status code {response.status_code}", node_id=node_id)
            return jsonify({"error": f"Camera trigger failed with code {response.status_code}"}), 502
    except requests.exceptions.RequestException as e:
        log_event("ERROR", "Proxy", f"Failed to contact camera node: {e}", node_id=node_id)
        return jsonify({"error": f"Camera connection timeout or failure: {e}"}), 504

@app.route("/api/v1/camera/capture", methods=["GET"])
def capture_camera():
    """Queries camera node /capture and streams raw JPEG back while enqueuing it in the background."""
    node_id = request.args.get("node_id")
    if not node_id:
        return jsonify({"error": "node_id parameter is required"}), 400
        
    ip_address = node_manager.get_node_ip(node_id)
    if not ip_address:
        return jsonify({"error": f"Node '{node_id}' not found in registry"}), 404
        
    try:
        url = f"http://{ip_address}/capture"
        log_event("INFO", "Proxy", f"Requesting immediate manual capture from {node_id} at {url}")
        
        # 3.0s strict timeout
        response = requests.get(url, timeout=3.0)
        
        if response.status_code == 200:
            image_bytes = response.content
            
            # Enqueue the capture in the background as a manual dashboard trigger
            inference_worker.enqueue_capture_job(node_id, image_bytes, "manual_dashboard")
            
            # Stream the raw JPEG response back
            return Response(image_bytes, mimetype="image/jpeg")
        else:
            log_event("ERROR", "Proxy", f"Camera capture responded with code {response.status_code}", node_id=node_id)
            return jsonify({"error": f"Camera capture returned code {response.status_code}"}), 502
    except requests.exceptions.RequestException as e:
        log_event("ERROR", "Proxy", f"Failed to fetch image capture from node: {e}", node_id=node_id)
        return jsonify({"error": f"Camera connection failed: {e}"}), 504

# -------------------------------------------------------------------
# 4. TELEMETRY & SYSTEM LOG QUERIES
# -------------------------------------------------------------------

@app.route("/api/v1/sensors/history", methods=["GET"])
def get_sensor_history():
    """Queries historical telemetry data with optional filters."""
    node_id = request.args.get("node_id")
    start = request.args.get("start")
    end = request.args.get("end")
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
        
    try:
        with database.get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM sensor_logs WHERE 1=1"
            params = []
            if node_id:
                query += " AND node_id = ?"
                params.append(node_id)
            if start:
                query += " AND timestamp >= ?"
                params.append(start)
            if end:
                query += " AND timestamp <= ?"
                params.append(end)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            # Return list of dicts. We reverse it so it goes oldest -> newest for chart plotting
            result = [dict(row) for row in rows]
            result.reverse()
            return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/v1/sensors/live", methods=["GET"])
def live_sensor_stream():
    """Streams live telemetry updates via Server-Sent Events (SSE)."""
    node_id = request.args.get("node_id")
    
    def event_stream():
        q = queue.Queue(maxsize=100)
        
        # Telemetry subscriber callback
        def callback(data):
            if not node_id or data["node_id"] == node_id:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass # Discard oldest or skip if client is slow
                    
        # Subscribe to anomaly detector updates
        anomaly_detector.subscribe(callback)
        
        try:
            # Yield initial blank data message to establish stream connection
            yield "data: {}\n\n"
            while True:
                try:
                    data = q.get(timeout=8.0) # Yield keepalive ping if idle
                    yield f"data: {json.dumps(data)}\n\n"
                except queue.Empty:
                    yield "data: {}\n\n"
        finally:
            anomaly_detector.unsubscribe(callback)
            
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/api/v1/logs", methods=["GET"])
def get_system_logs():
    """Retrieves recent logs written to the SQLite event registry."""
    node_id = request.args.get("node_id")
    level = request.args.get("level")
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
        
    try:
        with database.get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM system_logs WHERE 1=1"
            params = []
            if node_id:
                query += " AND node_id = ?"
                params.append(node_id)
            if level:
                query += " AND level = ?"
                params.append(level.upper())
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            result = []
            for row in rows:
                capture = dict(row)
                capture["prediction_name"] = inference_worker.get_class_name(capture.get("prediction"))
                result.append(capture)
            return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/v1/captures", methods=["GET"])
def get_captures():
    """Retrieves recent model image detection outcomes."""
    node_id = request.args.get("node_id")
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        limit = 20
        
    try:
        with database.get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM capture_logs"
            params = []
            if node_id:
                query += " WHERE node_id = ?"
                params.append(node_id)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return jsonify([dict(row) for row in rows]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/captures/<path:filename>", methods=["GET"])
def serve_capture(filename):
    """Serves JPEG images saved in the captures directory."""
    return send_from_directory(SAVE_DIR, filename)

# -------------------------------------------------------------------
# 5. FRONTEND DASHBOARD PAGE
# -------------------------------------------------------------------

@app.route("/")
def dashboard():
    """Main route displaying the monitoring console UI."""
    return render_template("dashboard.html")

if __name__ == "__main__":
    # Allow binding to all interfaces on port 5000
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
