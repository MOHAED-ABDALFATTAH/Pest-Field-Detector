import threading
import time
import math
import requests
from database import get_db_connection
from logger import log_event
import node_manager
import datetime

# Global subscriber list for real-time telemetry updates
_subscribers = []
_subscribers_lock = threading.Lock()

def subscribe(callback):
    """Register a callback function for real-time telemetry events."""
    with _subscribers_lock:
        _subscribers.append(callback)

def unsubscribe(callback):
    """Unregister a callback function."""
    with _subscribers_lock:
        if callback in _subscribers:
            _subscribers.remove(callback)

def notify_subscribers(data):
    """Send telemetry update to all registered callbacks."""
    with _subscribers_lock:
        for callback in _subscribers:
            try:
                callback(data)
            except Exception as e:
                print(f"Error executing telemetry subscriber callback: {e}")

def calculate_z_score(node_id, new_score):
    """
    Retrieves the last 50 fusion scores for the given node_id, 
    calculates rolling mean and standard deviation, and returns
    (z_score, is_anomaly) where is_anomaly is 1 if z_score > 2.5.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT fusion_score FROM sensor_logs 
            WHERE node_id = ? 
            ORDER BY id DESC LIMIT 50
            """, (node_id,))
            rows = cursor.fetchall()
            
        scores = [row['fusion_score'] for row in rows]
        
        # We need a minimum number of readings (e.g. 5) to establish a baseline
        if len(scores) < 5:
            return 0.0, 0
            
        mean = sum(scores) / len(scores)
        variance = sum((x - mean) ** 2 for x in scores) / len(scores)
        std_dev = math.sqrt(variance)
        
        # Guard against zero variance division
        if std_dev < 0.01:
            std_dev = 0.01
            
        z_score = abs(new_score - mean) / std_dev
        is_anomaly = 1 if z_score > 2.5 else 0
        return z_score, is_anomaly
    except Exception as e:
        log_event("ERROR", "AnomalyDetector", f"Error calculating Z-score for node {node_id}: {e}", node_id=node_id)
        return 0.0, 0

def poll_node_sensors(node):
    """Polls a single node's /sensors endpoint and processes the result."""
    node_id = node['node_id']
    ip_address = node['ip_address']
    
    url = f"http://{ip_address}/sensors"
    try:
        response = requests.get(url, timeout=3.0)
        if response.status_code == 200:
            data = response.json()
            
            # Extract metrics
            pir = data.get("pir", 0)
            sound = data.get("sound", 0)
            gas = data.get("gas", 0)
            distance_cm = data.get("distance_cm", -1.0)
            humidity = data.get("humidity", 0.0)
            temp_c = data.get("temp_c", 0.0)
            fusion_score = data.get("score", 0.0)
            
            # Update node heartbeat to keep it active
            node_manager.update_heartbeat(node_id)
            
            # Run rolling anomaly detection on fusion score
            z_score, is_anomaly = calculate_z_score(node_id, fusion_score)
            
            # Save telemetry to sensor_logs
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO sensor_logs (node_id, timestamp, pir, sound, gas, distance_cm, humidity, temp_c, fusion_score, is_anomaly)
                VALUES (?, datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?, ?, ?)
                """, (node_id, pir, sound, gas, distance_cm, humidity, temp_c, fusion_score, is_anomaly))
                conn.commit()
                sensor_log_id = cursor.lastrowid
                
            # Log event if anomaly detected
            if is_anomaly:
                log_event("WARNING", "AnomalyDetector", 
                          f"Anomaly detected! Score={fusion_score:.2f} (Z-Score={z_score:.2f})", 
                          node_id=node_id, 
                          details={"pir": pir, "sound": sound, "gas": gas, "distance": distance_cm, "z_score": z_score})
            
            # Prepare telemetry dictionary for subscribers
            telemetry_data = {
                "id": sensor_log_id,
                "node_id": node_id,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pir": pir,
                "sound": sound,
                "gas": gas,
                "distance_cm": distance_cm,
                "humidity": humidity,
                "temp_c": temp_c,
                "fusion_score": fusion_score,
                "is_anomaly": is_anomaly
            }
            
            notify_subscribers(telemetry_data)
        else:
            log_event("WARNING", "AnomalyDetector", f"Polling failed with status code {response.status_code}", node_id=node_id)
    except requests.exceptions.RequestException as e:
        # Avoid flood of connection logs, log at debug/info, node will naturally go inactive
        log_event("DEBUG", "AnomalyDetector", f"Connection error polling node: {e}", node_id=node_id)

def poller_loop(poll_interval=5.0):
    """Periodically queries registered sensor nodes and executes the anomaly engine."""
    log_event("INFO", "AnomalyDetector", "Background sensor poller thread started.")
    while True:
        try:
            # Clean up stale nodes (no heartbeat for 60 seconds)
            node_manager.mark_stale_nodes_inactive(timeout_seconds=60)
            
            # Get registered sensor or hybrid nodes
            nodes = node_manager.get_active_nodes()
            sensor_nodes = [n for n in nodes if n['node_type'] in ('sensor', 'hybrid') and n['status'] == 'active']
            
            threads = []
            for node in sensor_nodes:
                t = threading.Thread(target=poll_node_sensors, args=(node,), daemon=True)
                t.start()
                threads.append(t)
                
            for t in threads:
                t.join()
                
        except Exception as e:
            log_event("ERROR", "AnomalyDetector", f"Error in poller loop: {e}")
            
        time.sleep(poll_interval)

def start_poller(poll_interval=5.0):
    """Starts the background sensor poller thread."""
    t = threading.Thread(target=poller_loop, args=(poll_interval,), name="SensorPoller", daemon=True)
    t.start()
    return t
