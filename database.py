import sqlite3
import contextlib
import os

DB_PATH = os.environ.get("PEST_DB_PATH", "pest_detector.db")

@contextlib.contextmanager
def get_db_connection():
    """Context manager for acquiring a thread-safe database connection."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
    finally:
        conn.close()

def init_db():
    """Initialize database schema, tables, and indexes."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Node Registry Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL CHECK(node_type IN ('sensor', 'camera', 'hybrid')),
            ip_address TEXT NOT NULL,
            mac_address TEXT,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive', 'degraded'))
        );
        """)
        
        # 2. Sensor Telemetry Logs
        cursor.execute("""
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
        """)
        
        # 3. Image Captures & Predictions
        cursor.execute("""
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
        """)
        
        # 4. System Event Logs
        cursor.execute("""
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
        """)
        
        # 5. Create Optimizing Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sensor_node_time ON sensor_logs(node_id, timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_capture_node_time ON capture_logs(node_id, timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_system_level_time ON system_logs(level, timestamp);")
        
        conn.commit()
    print("Database initialized successfully.")
