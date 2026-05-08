import sqlite3
import os
import time

DB_DIR = "TenshiVoice_Data"
DB_PATH = os.path.join(DB_DIR, "messages.db")

def init_db():
    if not os.path.exists(DB_DIR):
        os.makedirs(DB_DIR)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create messages table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp REAL NOT NULL,
        is_snapchat INTEGER DEFAULT 0
    )
    ''')
    
    # Create filters table (for auto-delete rules)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS filters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_user TEXT NOT NULL UNIQUE,
        action TEXT NOT NULL
    )
    ''')
    
    conn.commit()
    conn.close()

def save_message(channel_id, sender, content, is_snapchat=False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = time.time()
    cursor.execute('''
    INSERT INTO messages (channel_id, sender, content, timestamp, is_snapchat)
    VALUES (?, ?, ?, ?, ?)
    ''', (channel_id, sender, content, timestamp, 1 if is_snapchat else 0))
    msg_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return msg_id

def get_messages(channel_id, limit=50):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    SELECT id, sender, content, timestamp, is_snapchat 
    FROM messages 
    WHERE channel_id = ? 
    ORDER BY timestamp ASC
    ''', (channel_id,))
    rows = cursor.fetchall()
    
    # If any message is a snapchat message, delete it after fetching
    snapchat_ids = [row[0] for row in rows if row[4] == 1]
    if snapchat_ids:
        placeholders = ','.join('?' * len(snapchat_ids))
        cursor.execute(f'DELETE FROM messages WHERE id IN ({placeholders})', snapchat_ids)
        conn.commit()
        
    conn.close()
    
    messages = []
    for row in rows:
        messages.append({
            "id": row[0],
            "sender": row[1],
            "content": row[2],
            "timestamp": row[3],
            "is_snapchat": bool(row[4])
        })
    return messages

def delete_messages_from_user(username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM messages WHERE sender = ?', (username,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def delete_all_messages_older_than(days):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cutoff = time.time() - (days * 86400)
    cursor.execute('DELETE FROM messages WHERE timestamp < ?', (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def add_filter_rule(target_user, action="delete"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO filters (target_user, action) VALUES (?, ?)
    ''', (target_user, action))
    conn.commit()
    conn.close()

def get_filter_rules():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT target_user, action FROM filters')
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def is_filtered(sender):
    rules = get_filter_rules()
    if sender in rules and rules[sender] == "delete":
        return True
    return False

# Initialize DB on import
init_db()
