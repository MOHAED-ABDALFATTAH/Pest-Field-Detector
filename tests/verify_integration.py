import subprocess
import time
import requests
import sys
import sqlite3
import os

# Resolve paths dynamically relative to this script
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RECEIVER_SCRIPT = os.path.join(BASE_DIR, "laptop_receiver.py")
MOCK_NODES_SCRIPT = os.path.join(BASE_DIR, "tests", "mock_nodes.py")
DB_PATH = os.path.join(BASE_DIR, "pest_detector.db")

# Clean database for validation run
if os.path.exists(DB_PATH):
    try:
        os.remove(DB_PATH)
        print("Cleaned up old database for clean verification.")
    except Exception as e:
        print(f"Could not remove old database: {e}")

print("Starting verification suite...")

# 1. Start Laptop Receiver
print(f"Starting laptop receiver: {RECEIVER_SCRIPT}")
receiver_process = subprocess.Popen(
    [sys.executable, RECEIVER_SCRIPT],
    env={**os.environ, "PEST_DB_PATH": DB_PATH},
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# 2. Start Mock Nodes
print(f"Starting mock node emulator: {MOCK_NODES_SCRIPT}")
mock_process = subprocess.Popen(
    [sys.executable, MOCK_NODES_SCRIPT],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

try:
    print("Waiting 5 seconds for systems initialization and node auto-registrations...")
    time.sleep(5.0)

    # 3. Test Node Registration
    print("\n--- [Test 1: Node Registry Dynamic IP Mapping] ---")
    res = requests.get("http://127.0.0.1:5000/api/v1/nodes")
    nodes = res.json()
    print(f"Registered nodes list: {nodes}")
    assert len(nodes) >= 2, "Test failed: Nodes did not register properly"
    print("Registry test: SUCCESS")

    # 4. Wait for normal poller logs
    print("\n--- [Test 2: Sensor Telemetry Polling] ---")
    time.sleep(6.0) # allow polling loops to run
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sensor_logs")
    count = cursor.fetchone()[0]
    print(f"Recorded sensor logs count: {count}")
    assert count > 0, "Test failed: No telemetry logs collected by poller"
    print("Polling test: SUCCESS")

    # 5. Inject Telemetry Anomaly
    print("\n--- [Test 3: Anomaly Engine & Z-Score Calibrator] ---")
    while count < 5:
        print(f"Calibrating baseline... (current count = {count}/5)")
        time.sleep(5.0)
        cursor.execute("SELECT COUNT(*) FROM sensor_logs")
        count = cursor.fetchone()[0]
        
    print("Injecting extreme values on mock sensor node...")
    mock_process.stdin.write("a\n")
    mock_process.stdin.flush()
    print("Anomaly injected into mock sensor node state!")
    
    # Wait for poller to query the anomaly
    print("Waiting for poller to query the anomaly...")
    time.sleep(8.0)
    
    cursor.execute("SELECT * FROM sensor_logs WHERE is_anomaly = 1")
    anomaly_logs = cursor.fetchall()
    print(f"Anomaly records found: {anomaly_logs}")
    assert len(anomaly_logs) > 0, "Test failed: Anomaly not detected/flagged by Z-score math"
    
    cursor.execute("SELECT * FROM system_logs WHERE level = 'WARNING'")
    warning_logs = cursor.fetchall()
    print(f"System logs warning count: {len(warning_logs)}")
    assert len(warning_logs) > 0, "Test failed: No warning log entry recorded for the anomaly"
    print("Anomaly detection test: SUCCESS")

    # 6. Proxy Trigger and Capture Test
    print("\n--- [Test 4: Camera Proxy Remote Command Proxying] ---")
    print("Commanding proxy capture via receiver...")
    res = requests.get("http://127.0.0.1:5000/api/v1/camera/capture?node_id=cam_node_01")
    print(f"Capture response status: {res.status_code}, header content-type: {res.headers.get('content-type')}")
    assert res.status_code == 200, "Test failed: Capture proxy returned error"
    assert res.headers.get('content-type') == 'image/jpeg', "Test failed: Did not return JPEG stream"
    
    # Wait for inference worker to process manual upload in background
    print("Waiting for background inference task to complete...")
    time.sleep(3.0)
    
    cursor.execute("SELECT * FROM capture_logs")
    capture_logs = cursor.fetchall()
    print(f"Captured images database record: {capture_logs}")
    assert len(capture_logs) > 0, "Test failed: No capture logs written for camera uploads"
    print("Camera proxy and ingestion worker tests: SUCCESS")

    # 7. System Event Logs Test
    print("\n--- [Test 5: Centralized Logging Registry] ---")
    res = requests.get("http://127.0.0.1:5000/api/v1/logs?limit=10")
    logs = res.json()
    print(f"Recent system events logs: {logs}")
    assert len(logs) > 0, "Test failed: System logs endpoint returned empty"
    print("System logs registry test: SUCCESS")

    print("\n=============================================")
    print("ALL TESTS PASSED SUCCESSFULLY! SYSTEM INTEGRATION READY.")
    print("=============================================")

except Exception as ex:
    print(f"\nAssertion/Execution error during verification: {ex}")
    sys.exit(1)

finally:
    # Cleanup background processes
    print("Stopping emulated nodes...")
    mock_process.terminate()
    mock_process.wait()
    
    print("Stopping laptop receiver server...")
    receiver_process.terminate()
    receiver_process.wait()
    
    print("Verification completed.")
