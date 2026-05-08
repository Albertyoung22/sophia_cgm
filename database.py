import sqlite3
import os
from datetime import datetime

DB_FILE = 'cgm_data.db'

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # 血糖紀錄表
    c.execute('''
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sgv INTEGER,
            direction TEXT,
            dateString TEXT,
            device TEXT,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 治療/照護日誌表 (打針、吃糖、運動、換感測器等)
    c.execute('''
        CREATE TABLE IF NOT EXISTS treatments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eventType TEXT,
            carbs REAL DEFAULT 0,
            insulin REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            enteredBy TEXT
        )
    ''')
    
    # 設備狀態表 (電量、閉環預測等)
    c.execute('''
        CREATE TABLE IF NOT EXISTS devicestatus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT,
            uploaderBattery INTEGER,
            pumpBattery INTEGER,
            iob REAL,
            cob REAL,
            created_at TEXT
        )
    ''')
    
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully.")
