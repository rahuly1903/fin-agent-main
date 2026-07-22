import os
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/fin_agent")

try:
    connection_pool = psycopg2.pool.SimpleConnectionPool(
        1, 20,
        dsn=DATABASE_URL
    )
    if connection_pool:
        print("Connection pool created successfully")
except Exception as e:
    print(f"Error creating connection pool: {e}")

@contextmanager
def get_db_connection():
    conn = connection_pool.getconn()
    try:
        yield conn
    finally:
        connection_pool.putconn(conn)

def execute_query(query, params=None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            conn.commit()

def fetch_all(query, params=None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

def fetch_one(query, params=None):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()
