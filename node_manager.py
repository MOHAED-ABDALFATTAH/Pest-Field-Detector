from database import get_db_connection
import datetime

def register_node(node_id, node_type, ip_address, mac_address=None):
    """Registers or updates a node. Inserts a new node or updates existing node's IP, type, last_seen, and status."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO nodes (node_id, node_type, ip_address, mac_address, last_seen, status)
        VALUES (?, ?, ?, ?, datetime('now', 'localtime'), 'active')
        ON CONFLICT(node_id) DO UPDATE SET
            node_type = excluded.node_type,
            ip_address = excluded.ip_address,
            mac_address = COALESCE(excluded.mac_address, nodes.mac_address),
            last_seen = datetime('now', 'localtime'),
            status = 'active'
        """, (node_id, node_type, ip_address, mac_address))
        conn.commit()

def update_heartbeat(node_id):
    """Updates the last_seen timestamp and sets the node status back to active."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE nodes 
        SET last_seen = datetime('now', 'localtime'), status = 'active'
        WHERE node_id = ?
        """, (node_id,))
        conn.commit()

def get_node_ip(node_id):
    """Returns the IP address of a given node, or None if the node doesn't exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ip_address FROM nodes WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        return row['ip_address'] if row else None

def get_node(node_id):
    """Fetches details for a single node."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_active_nodes(node_type=None):
    """Retrieves all registered nodes, optionally filtering by node_type."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if node_type:
            cursor.execute("SELECT * FROM nodes WHERE node_type = ?", (node_type,))
        else:
            cursor.execute("SELECT * FROM nodes")
        return [dict(row) for row in cursor.fetchall()]

def mark_stale_nodes_inactive(timeout_seconds=60):
    """Marks nodes as 'inactive' if they haven't updated their heartbeat in the last timeout_seconds."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # SQL math to find difference in seconds using strftime or julianday
        # (julianday('now', 'localtime') - julianday(last_seen)) * 86400 is the difference in seconds
        cursor.execute("""
        UPDATE nodes
        SET status = 'inactive'
        WHERE status = 'active' 
          AND (julianday(datetime('now', 'localtime')) - julianday(last_seen)) * 86400 > ?
        """, (timeout_seconds,))
        conn.commit()
