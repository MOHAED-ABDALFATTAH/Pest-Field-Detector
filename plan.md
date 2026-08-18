# System Architecture, Requirements & Implementation Plan

## Executive Summary & System Architecture

This specification outlines the architecture and execution strategy for expanding the IoT sensor-camera edge platform into a multi-node system. The architecture features node identification, asynchronous job queuing, thread-safe SQLite storage, local sensor fusion, real-time anomaly detection, and a unified web dashboard.

```
                  +-------------------------------------------------+
                  |                EDGE NODES LAYER                 |
                  +-------------------------------------------------+
                  | [Sensor Node 1]   [Sensor Node 2]  ... [Node N] |
                  | [Cam Node 1]      [Cam Node 2]     ... [Node N] |
                  +-------------------------------------------------+
                                      |           |
               HTTP POST /upload      |           | HTTP GET /sensors (Polled)
               (With node_id)         v           v
          +---------------------------------------------------------------+
          |                        BACKEND SERVER                         |
          |  +---------------------------------------------------------+  |
          |  |                   API Layer (Flask)                     |  |
          |  | - /api/v1/upload, /api/v1/camera/trigger, /capture     |  |
          |  +---------------------------------------------------------+  |
          |                               |                               |
          |                               v                               |
          |  +---------------------------------------------------------+  |
          |  |                 Async Task Queue (Worker)               |  |
          |  | [Job Queue: Ingestion -> Inference -> Anomaly Engine]   |  |
          |  +---------------------------------------------------------+  |
          |                               |                               |
          |                               v                               |
          |  +---------------------------------------------------------+  |
          |  |                   Database Layer (SQLite)               |  |
          |  | - sensor_logs, capture_logs, system_logs, nodes           |  |
          |  +---------------------------------------------------------+  |
          +---------------------------------------------------------------+
                                          ^
                                          | WebSocket / REST API
                                          v
                  +-------------------------------------------------+
                  |             WEB DASHBOARD FRONTEND              |
                  | - Multi-Node Selector & Telemetry Stream        |
                  | - Anomaly Historical Overlay Charts            |
                  | - Manual Camera Trigger & Capture Console       |
                  | - Centralized System Event Logs                 |
                  +-------------------------------------------------+
```

---

## Technical Requirements Specification

### Functional Requirements
* **Node Identity & Discovery**: Each sensor and camera node transmits a unique `node_id` in telemetry payloads, image metadata headers, and registration heartbeats.
* **Asynchronous Detection Queue**: Image ingestion and PyTorch `WaveletEnhancedResNet` inference execute asynchronously off the main thread via a queue worker, preventing HTTP server blocking during burst captures.
* **Camera Proxy Operations**: Backend proxy endpoints trigger or capture single image frames from specific camera nodes using target `node_id` IP mapping lookup.
* **Data Isolation & Statistical Analytics**: Sensor metrics and image captures are indexed by `node_id` for per-node baseline calibration, rolling Z-score anomaly calculations, and isolated stream visualization.
* **Database & Centralized Logging**: SQLite engine configured with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`), structured system logs, and capture metadata referencing telemetry state.

---

## Database Schema (Multi-Node SQLite DDL)

```sql
-- Node Registry Table
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL CHECK(node_type IN ('sensor', 'camera', 'hybrid')),
    ip_address TEXT NOT NULL,
    mac_address TEXT,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'degraded'))
);

-- Sensor Telemetry Logs (Indexed by Node)
CREATE TABLE IF NOT EXISTS sensor_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    pir INTEGER NOT NULL,
    sound INTEGER NOT NULL,
    gas INTEGER NOT NULL,
    distance_cm REAL NOT NULL,
    humidity REAL NOT NULL,
    temp_c REAL NOT NULL,
    fusion_score REAL NOT NULL,
    is_anomaly INTEGER DEFAULT 0,
    FOREIGN KEY(node_id) REFERENCES nodes(node_id)
);

-- Image Captures & Model Predictions
CREATE TABLE IF NOT EXISTS capture_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    file_path TEXT NOT NULL,
    trigger_source TEXT CHECK(trigger_source IN ('hardware_node', 'manual_dashboard', 'backend_api')),
    prediction INTEGER,
    sensor_log_id INTEGER,
    processing_status TEXT DEFAULT 'pending' CHECK(processing_status IN ('pending', 'processing', 'completed', 'failed')),
    FOREIGN KEY(node_id) REFERENCES nodes(node_id),
    FOREIGN KEY(sensor_log_id) REFERENCES sensor_logs(id)
);

-- System Event Logs
CREATE TABLE IF NOT EXISTS system_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL CHECK(level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    node_id TEXT,
    module TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT,
    FOREIGN KEY(node_id) REFERENCES nodes(node_id)
);

-- Index Optimizations
CREATE INDEX IF NOT EXISTS idx_sensor_node_time ON sensor_logs(node_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_capture_node_time ON capture_logs(node_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_system_level_time ON system_logs(level, timestamp);
```

---

## REST API Specifications

| Route | Method | Headers / Parameters | Description |
| :--- | :--- | :--- | :--- |
| `/api/v1/nodes/register` | `POST` | Body: `{"node_id": "cam_01", "node_type": "camera", "ip_address": "10.30.50.213"}` | Registers or updates a node's dynamic IP address. |
| `/api/v1/upload` | `POST` | `X-Node-ID: cam_01`, `X-Trigger-Source: hardware_node` | Ingests JPEG frame into async queue; returns HTTP 202. |
| `/api/v1/camera/trigger` | `POST` | Query Param: `node_id=cam_01` | Proxies an HTTP GET request to camera node `/trigger`. |
| `/api/v1/camera/capture` | `GET` | Query Param: `node_id=cam_01` | Commands camera node `/capture` frame and streams back raw JPEG. |
| `/api/v1/sensors/history` | `GET` | Query Params: `node_id`, `start`, `end`, `limit` | Returns historical telemetry data with anomaly flags. |
| `/api/v1/sensors/live` | `GET / WS` | Query Param: `node_id` | Streams live telemetry updates for active dashboard. |
| `/api/v1/logs` | `GET` | Query Params: `level`, `node_id`, `limit` | Queries structured system event records. |

---

## Firmware Protocol Updates

### ESP32 Sensor Node (`esp32_sensor_node_fusion.ino`)
* Declare global node identifier: `const char* NODE_ID = "sensor_node_01";`
* Include `"node_id"` key inside JSON formatted responses in `handleSensors()`.
* Update `triggerCamera()` to request camera address dynamically from backend registry or broadcast routing.

### ESP32-CAM Node (`esp32cam_trigger_push.ino`)
* Declare global node identifier: `const char* NODE_ID = "cam_node_01";`
* Attach custom HTTP request headers in `handleTrigger()` when executing POST uploads:
  * `X-Node-ID: cam_node_01`
  * `X-Trigger-Source: hardware_node`

---

## Detailed Step-by-Step Implementation Roadmap

### Phase 1: Storage Infrastructure & Node Registry
* **Step 1.1**: Build `database.py` managing SQLite connections with thread pooling and WAL mode initialization.
* **Step 1.2**: Implement `node_manager.py` handling registration, heartbeat monitoring, and IP dynamic lookup.
* **Step 1.3**: Add structured logger writing events to `system_logs` alongside stdout formatting.

### Phase 2: Asynchronous Task Queue & Inference Worker
* **Step 2.1**: Implement a task worker using `concurrent.futures.ThreadPoolExecutor` or `queue.Queue`.
* **Step 2.2**: Update Flask `/upload` route to save raw payload to temporary queue and immediately respond with `202 Accepted`.
* **Step 2.3**: Task worker processes JPEG, executes `WaveletEnhancedResNet` PyTorch inference, stores file, and logs record in `capture_logs`.

### Phase 3: Sensor Polling & Rolling Anomaly Engine
* **Step 3.1**: Create background thread polling `/sensors` endpoint for registered active sensor nodes.
* **Step 3.2**: Implement per-node statistical baseline engine calculating rolling mean and standard deviation ($Z = \frac{|x - \mu|}{\sigma} > 2.5$).
* **Step 3.3**: Persist telemetry into `sensor_logs`, marking detected anomaly flags.

### Phase 4: Web Dashboard Interface
* **Step 4.1**: Build HTML/JS dashboard frontend featuring dynamic node selection dropdowns.
* **Step 4.2**: Render dynamic telemetry charts (Chart.js) highlighting anomaly data points in visual red highlights.
* **Step 4.3**: Integrate live single-frame capture control panel and structured event log viewer.

---

## Operational Risk Matrix & Countermeasures

| Identified Risk | Severity | Technical Countermeasure |
| :--- | :--- | :--- |
| **SQLite DB Locking** | High | Enable WAL mode (`PRAGMA journal_mode=WAL;`), set connection timeout to 10.0s, and route all write operations through atomic task worker threads. |
| **Inference Queue Overflow** | Critical | Enforce queue size bounds (e.g., max 50 pending captures). Drop overflowing items and record `CRITICAL` log entries in `system_logs`. |
| **Dynamic IP Drift** | Medium | Nodes execute auto-registration POST request on boot and send heartbeat pings every 60 seconds to maintain routing state. |
| **Unresponsive Node Timeouts** | Medium | All outgoing backend proxy requests to edge nodes must specify explicit connection timeouts ($3.0\text{s}$) to prevent worker thread blocking. |
