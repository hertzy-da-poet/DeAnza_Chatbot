"""
Purpose:
Manage PostgreSQL connection pooling, create vector tables, GIN indexes for fast full-text search, and `crawl_cache` table for hash tracking.

I/O:
*get_db(): Context Manager that checks out and returns connection to the pool
*init_db(): Sets up the database extensions, tables, and indexes
"""

import os
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://deanza:deanza@127.0.0.1:5432/deanza")

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    id             SERIAL PRIMARY KEY,
    source_type    TEXT NOT NULL,
    source_url     TEXT,
    doc_id         TEXT,
    title          TEXT,
    chunk_text     TEXT NOT NULL,
    embedding      vector(1536),
    metadata       JSONB,
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED,
    created_at     TIMESTAMPTZ DEFAULT now()
);

-- Index for dense vector cosine similarity
CREATE INDEX IF NOT EXISTS idx_chunks_hnsw 
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Index for fast keyword search (GIN)
CREATE INDEX IF NOT EXISTS idx_chunks_tsv 
    ON chunks USING gin (tsv);

-- Index for exact metadata lookups
CREATE INDEX IF NOT EXISTS idx_chunks_source_code 
    ON chunks ((metadata->>'code')) WHERE source_type = 'course';

CREATE TABLE IF NOT EXISTS crawl_cache (
    source_type   TEXT NOT NULL,
    doc_id        TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    updated_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (source_type, doc_id)
);
"""

_pool = None

def get_pool():
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn = 2,
            maxconn = 20,
            dsn = DATABASE_URL,
            cursor_factory = RealDictCursor,
        )
    return _pool

@contextmanager
def get_db():
    p = get_pool()
    conn = p.getconn()
    try: 
        yield conn
    finally:
        p.putconn(conn)

def init_db():
    with get_db() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
    print ("Database intialized with pgvector, GIN index, and crawl_cache")