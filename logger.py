import logging
import json
from database import get_db_connection

# Configure python standard logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] (%(name)s) %(message)s'
)
sys_logger = logging.getLogger("pest_detector")

def log_event(level, module, message, node_id=None, details=None):
    """
    Logs an event to Python's standard output and inserts a structured log 
    entry into the system_logs SQLite table.
    
    Levels: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
    """
    level_upper = level.upper()
    log_msg = f"[{module}] {message}"
    if node_id:
        log_msg = f"[{node_id}] " + log_msg
    if details:
        log_msg += f" | Details: {details}"

    # Log to stdout/stderr using standard logging
    if level_upper == 'DEBUG':
        sys_logger.debug(log_msg)
    elif level_upper == 'INFO':
        sys_logger.info(log_msg)
    elif level_upper == 'WARNING':
        sys_logger.warning(log_msg)
    elif level_upper == 'ERROR':
        sys_logger.error(log_msg)
    elif level_upper == 'CRITICAL':
        sys_logger.critical(log_msg)
    else:
        sys_logger.info(log_msg)

    # Log to SQLite DB
    try:
        details_str = json.dumps(details) if details is not None else None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO system_logs (level, node_id, module, message, details_json)
            VALUES (?, ?, ?, ?, ?)
            """, (level_upper, node_id, module, message, details_str))
            conn.commit()
    except Exception as e:
        print(f"Error logging event to database: {e}")
