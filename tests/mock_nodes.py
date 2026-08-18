import time
import random
import requests
import threading
import sys
from flask import Flask, jsonify, Response

app = Flask(__name__)

# Simulated telemetry state
telemetry_state = {
    "pir": 0,
    "sound": 30,
    "gas": 350,
    "distance_cm": 120.0,
    "humidity": 45.0,
    "temp_c": 24.5,
    "score": 0.1
}

force_anomaly = False

@app.route("/sensors", methods=["GET"])
def get_sensors():
    global force_anomaly
    if force_anomaly:
        telemetry_state["pir"] = 1
        telemetry_state["sound"] = 95
        telemetry_state["gas"] = 800
        telemetry_state["distance_cm"] = 12.0
        telemetry_state["score"] = 0.95
        force_anomaly = False
    else:
        telemetry_state["pir"] = random.choices([0, 1], weights=[95, 5])[0]
        telemetry_state["sound"] = random.randint(25, 38)
        telemetry_state["gas"] = random.randint(320, 360)
        telemetry_state["distance_cm"] = random.uniform(100.0, 150.0)
        telemetry_state["score"] = 0.1 if telemetry_state["pir"] == 0 else 0.5
        
    telemetry_state["temp_c"] = random.uniform(23.8, 25.2)
    telemetry_state["humidity"] = random.uniform(42.0, 48.0)
    
    return jsonify({
        "node_id": "sensor_node_01",
        "pir": telemetry_state["pir"],
        "sound": telemetry_state["sound"],
        "gas": telemetry_state["gas"],
        "distance_cm": telemetry_state["distance_cm"],
        "humidity": telemetry_state["humidity"],
        "temp_c": telemetry_state["temp_c"],
        "score": telemetry_state["score"]
    })

@app.route("/trigger", methods=["GET"])
def get_trigger():
    print("[MOCK CAMERA] Received GET /trigger request!")
    threading.Thread(target=send_dummy_upload, daemon=True).start()
    return "Triggered OK", 200

@app.route("/capture", methods=["GET"])
def get_capture():
    print("[MOCK CAMERA] Received GET /capture request!")
    dummy_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x0c\x01\x01\x00\x02\x11\x03\x11\x00?\x00\xa2\x8a(\xa2\x8a(\xa2\x8a(\xa2\x8a)\xff\xd9'
    return Response(dummy_jpeg, mimetype="image/jpeg")

def send_dummy_upload():
    time.sleep(0.5)
    dummy_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x0c\x01\x01\x00\x02\x11\x03\x11\x00?\x00\xa2\x8a(\xa2\x8a(\xa2\x8a(\xa2\x8a)\xff\xd9'
    try:
        url = "http://127.0.0.1:5000/api/v1/upload"
        headers = {
            "X-Node-ID": "cam_node_01",
            "X-Trigger-Source": "hardware_node"
        }
        res = requests.post(url, data=dummy_jpeg, headers=headers, timeout=3.0)
        print(f"[MOCK CAMERA] Uploaded dummy photo to receiver: {res.status_code}")
    except Exception as e:
        print(f"[MOCK CAMERA] Failed to upload photo: {e}")

def trigger_anomaly_sim():
    global force_anomaly
    force_anomaly = True
    print("[MOCK NODES] Triggering simulated anomaly on next sensors poll!")

def register_nodes_with_receiver():
    time.sleep(2.5)
    try:
        requests.post("http://127.0.0.1:5000/api/v1/nodes/register", json={
            "node_id": "sensor_node_01",
            "node_type": "sensor",
            "ip_address": "127.0.0.1:5001"
        })
        requests.post("http://127.0.0.1:5000/api/v1/nodes/register", json={
            "node_id": "cam_node_01",
            "node_type": "camera",
            "ip_address": "127.0.0.1:5001"
        })
        print("[MOCK NODES] Successfully registered mock sensor_node_01 and cam_node_01 with receiver!")
    except Exception as e:
        print(f"[MOCK NODES] Failed to auto-register nodes: {e}")

if __name__ == "__main__":
    threading.Thread(target=register_nodes_with_receiver, daemon=True).start()
    
    # Run CLI controller
    def CLI():
        time.sleep(3.0)
        print("\n--- Mock Nodes Simulation Controller ---")
        print("Press 'a' and ENTER to inject a telemetry anomaly.")
        print("Press 'q' and ENTER to quit mock server.")
        while True:
            try:
                cmd = sys.stdin.readline().strip()
                if cmd == 'a':
                    trigger_anomaly_sim()
                elif cmd == 'q':
                    break
            except Exception:
                break
    threading.Thread(target=CLI, daemon=True).start()
    
    app.run(host="0.0.0.0", port=5001)
