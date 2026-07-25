import sqlite3
import os
from datetime import datetime, timezone, timedelta

DB_FILE = os.path.join(os.path.dirname(__file__), "carelink_cgm.db")
DATABASE_URL = os.environ.get("DATABASE_URL")
DEFAULT_MONGO_URI = "mongodb+srv://youngtunchou:nightscout12345@cluster0.pippenm.mongodb.net/?appName=Cluster0"
MONGO_URI = os.environ.get("MONGO_URI") or os.environ.get("MONGO_CONNECTION") or DEFAULT_MONGO_URI

# Determine which database engine to use
IS_MONGO = bool(MONGO_URI)
IS_POSTGRES = bool(DATABASE_URL) and not IS_MONGO

# MongoDB Setup
mongo_client = None
mongo_db = None

def get_mongo_db():
    global mongo_client, mongo_db
    if mongo_db is None:
        from pymongo import MongoClient
        mongo_client = MongoClient(MONGO_URI)
        try:
            mongo_db = mongo_client.get_default_database()
        except:
            mongo_db = None
        if mongo_db is None:
            mongo_db = mongo_client["nightscout"]
    return mongo_db

# SQL Setup
def get_sql_connection():
    if IS_POSTGRES:
        import psycopg2
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        return conn
    else:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def get_sql_cursor(conn):
    if IS_POSTGRES:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    else:
        return conn.cursor()

def init_db():
    if IS_MONGO:
        db = get_mongo_db()
        db.entries.create_index([("date", -1)])
        db.entries.create_index([("dateString", -1)])
        print("[Database] MongoDB initialized (indexes created).")
    else:
        conn = get_sql_connection()
        cursor = get_sql_cursor(conn)
        if IS_POSTGRES:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS entries (
                    id SERIAL PRIMARY KEY,
                    sgv INTEGER NOT NULL,
                    direction VARCHAR(50) NOT NULL,
                    dateString VARCHAR(100) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    device VARCHAR(100) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp ON entries (timestamp DESC);
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sgv INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    dateString TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    device TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_timestamp ON entries (timestamp DESC);
            ''')
        conn.commit()
        conn.close()
        print(f"[Database] SQL initialized (PostgreSQL: {IS_POSTGRES}).")

def save_entry(sgv, direction, date_string, timestamp, device="Medtronic CareLink"):
    if IS_MONGO:
        try:
            db = get_mongo_db()
            if db.entries.find_one({"date": timestamp}):
                return False
            doc = {
                "sgv": sgv,
                "direction": direction,
                "dateString": date_string,
                "date": timestamp,
                "device": device,
                "type": "sgv"
            }
            db.entries.insert_one(doc)
            print(f"[MongoDB Saved] BG: {sgv} mg/dL ({direction})")
            return True
        except Exception as e:
            print(f"[MongoDB Save Error] {e}")
            return False
    else:
        conn = get_sql_connection()
        cursor = get_sql_cursor(conn)
        placeholder = "%s" if IS_POSTGRES else "?"
        try:
            cursor.execute(f'SELECT id FROM entries WHERE timestamp = {placeholder}', (timestamp,))
            if cursor.fetchone():
                conn.close()
                return False

            cursor.execute(f'''
                INSERT INTO entries (sgv, direction, dateString, timestamp, device)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
            ''', (sgv, direction, date_string, timestamp, device))
            conn.commit()
            print(f"[SQL Saved] BG: {sgv} mg/dL ({direction})")
            return True
        except Exception as e:
            print(f"[SQL Save Error] {e}")
            return False
        finally:
            conn.close()

def get_recent_entries(limit=288):
    if IS_MONGO:
        try:
            db = get_mongo_db()
            cursor = db.entries.find().sort("date", -1).limit(limit)
            rows = list(cursor)
            results = []
            for r in rows:
                results.append({
                    "sgv": r.get("sgv"),
                    "direction": r.get("direction"),
                    "dateString": r.get("dateString"),
                    "timestamp": r.get("date"),
                    "device": r.get("device")
                })
            results.reverse()
            return results
        except Exception as e:
            print(f"[MongoDB Get Error] {e}")
            return []
    else:
        conn = get_sql_connection()
        cursor = get_sql_cursor(conn)
        placeholder = "%s" if IS_POSTGRES else "?"
        try:
            cursor.execute(f'''
                SELECT sgv, direction, dateString, timestamp, device
                FROM entries
                ORDER BY timestamp DESC
                LIMIT {placeholder}
            ''', (limit,))
            rows = cursor.fetchall()
            results = [dict(row) for row in rows]
            results.reverse()
            return results
        except Exception as e:
            print(f"[SQL Get Error] {e}")
            return []
        finally:
            conn.close()

def get_latest_entry():
    if IS_MONGO:
        try:
            db = get_mongo_db()
            r = db.entries.find_one(sort=[("date", -1)])
            if r:
                return {
                    "sgv": r.get("sgv"),
                    "direction": r.get("direction"),
                    "dateString": r.get("dateString"),
                    "timestamp": r.get("date"),
                    "device": r.get("device")
                }
            return None
        except Exception as e:
            print(f"[MongoDB Get Latest Error] {e}")
            return None
    else:
        conn = get_sql_connection()
        cursor = get_sql_cursor(conn)
        try:
            cursor.execute('''
                SELECT sgv, direction, dateString, timestamp, device
                FROM entries
                ORDER BY timestamp DESC
                LIMIT 1
            ''')
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            print(f"[SQL Get Latest Error] {e}")
            return None
        finally:
            conn.close()

def get_nightscout_entries(limit=10):
    if IS_MONGO:
        try:
            db = get_mongo_db()
            cursor = db.entries.find().sort("date", -1).limit(limit)
            rows = list(cursor)
            results = []
            for r in rows:
                results.append({
                    "_id": str(r.get("date", "")),
                    "sgv": r.get("sgv"),
                    "date": r.get("date"),
                    "dateString": r.get("dateString"),
                    "direction": r.get("direction"),
                    "device": r.get("device"),
                    "type": "sgv"
                })
            return results
        except Exception as e:
            print(f"[MongoDB Get Nightscout Error] {e}")
            return []
    else:
        conn = get_sql_connection()
        cursor = get_sql_cursor(conn)
        placeholder = "%s" if IS_POSTGRES else "?"
        try:
            cursor.execute(f'''
                SELECT id, sgv, direction, dateString, timestamp, device
                FROM entries
                ORDER BY timestamp DESC
                LIMIT {placeholder}
            ''', (limit,))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                results.append({
                    "_id": str(row['timestamp']),
                    "sgv": row['sgv'],
                    "date": row['timestamp'],
                    "dateString": row['dateString'],
                    "direction": row['direction'],
                    "device": row['device'],
                    "type": "sgv"
                })
            return results
        except Exception as e:
            print(f"[SQL Get Nightscout Error] {e}")
            return []
        finally:
            conn.close()

def get_daily_stats(hours=24):
    if IS_MONGO:
        try:
            db = get_mongo_db()
            cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
            cursor = db.entries.find({"date": {"$gte": cutoff_ts}})
            rows = list(cursor)
            if not rows:
                return None
            vals = [r.get('sgv') for r in rows if r.get('sgv')]
        except Exception as e:
            print(f"[MongoDB Stats Error] {e}")
            return None
    else:
        conn = get_sql_connection()
        cursor = get_sql_cursor(conn)
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
        placeholder = "%s" if IS_POSTGRES else "?"
        try:
            cursor.execute(f'''
                SELECT sgv FROM entries WHERE timestamp >= {placeholder}
            ''', (cutoff_ts,))
            rows = cursor.fetchall()
            if not rows:
                return None
            vals = [r['sgv'] for r in rows]
        except Exception as e:
            print(f"[SQL Stats Error] {e}")
            return None
        finally:
            conn.close()

    if not vals:
        return None

    avg = sum(vals) / len(vals)
    in_range = len([v for v in vals if 70 <= v <= 180])
    high = len([v for v in vals if v > 180])
    low = len([v for v in vals if v < 70])
    tir = (in_range / len(vals)) * 100
    
    gmi = 3.31 + (0.02392 * avg)

    return {
        "avg": round(avg),
        "tir": round(tir, 1),
        "high": round((high / len(vals)) * 100, 1),
        "low": round((low / len(vals)) * 100, 1),
        "gmi": round(gmi, 1),
        "count": len(vals),
        "min": min(vals),
        "max": max(vals)
    }

if __name__ == '__main__':
    init_db()
