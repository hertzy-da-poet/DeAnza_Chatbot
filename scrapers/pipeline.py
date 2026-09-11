import hashlib
import json
import os
from typing import Dict, List, Tuple
from psycopg2.extras import execute_values
from core.db import get_db, init_db
from core.chunking import chunk_course, chunk_page, chunk_section
from core.embed import embed_batch
from scrapers.catalog import scrape_catalog
from scrapers.schedule import scrape_schedule
from scrapers.pages import scrape_catalog_pages
from scrapers.deanza_web import scrape_deanza_web


def compute_hash(text: str):
    """Hash text to compare differences (same text produces same hash)."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def run_pipeline():
    init_db()
    os.makedirs("data", exist_ok=True)
    print("\n------ Start Scraping ---------")
    courses = scrape_catalog()
    pages = scrape_catalog_pages()
    schedule = scrape_schedule()
    deanza_web_pages = scrape_deanza_web()

    assert len(courses) > 100, f"Error: Scraped courses count too low ({len(courses)})"
    assert len(pages) > 10, f"Error: Scraped page count too low ({len(pages)})"

    # Save local JSON snapshots
    with open("data/catalog_courses.json", "w", encoding="utf-8") as f:
        json.dump(courses, f, indent=2)
    with open("data/catalog_pages.json", "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2)
    with open("data/schedule.json", "w", encoding="utf-8") as f:
        json.dump(schedule, f, indent=2)
    with open("data/deanza_web_pages.json", "w", encoding="utf-8") as f:
        json.dump(deanza_web_pages, f, indent=2)
    print("Saved snapshots to data/ folder.")

    # Convert all sources to Chunks
    raw_chunks = [chunk_course(c) for c in courses]
    for p in pages:
        raw_chunks.extend(chunk_page(p))
    for s in schedule:
        raw_chunks.append(chunk_section(s))
    for dp in deanza_web_pages:
        raw_chunks.extend(chunk_page(dp))

    unique_chunks = {}
    for ch in raw_chunks:
        unique_chunks[(ch.source_type, ch.doc_id)] = ch
    all_chunks = list(unique_chunks.values())
    print(f"Total generated chunks: {len(all_chunks)}")


    #Check if the new hash text is the same with the existing text (avoid embedding if it stays the same)
    print ("\n------ Checking for Data Differences -------")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT source_type, doc_id, content_hash FROM crawl_cache")
            cached = {(r["source_type"], r["doc_id"]): r["content_hash"] for r in cur.fetchall()}

        changed_chunks = []

        for ch in all_chunks:
            key = (ch.source_type, ch.doc_id)
            current_hash = compute_hash(ch.chunk_text)
            if cached.get(key) != current_hash:
                changed_chunks.append((ch, current_hash))

        print (f"Chunks requireing re-embedding / update: {len(changed_chunks)} of {len(all_chunks)}")

        #If text stay the same, leave it
        if not changed_chunks:
            print (f"All content is up-to-date. Zero embeddings needed.")
            return

        #Embed and Update batches of 30
        BATCH_SIZE = 30
        with conn.cursor() as cur:
            for i in range(0, len(changed_chunks), BATCH_SIZE):
                batch_items = changed_chunks[i: i + BATCH_SIZE]
                chunks_batch = [item[0] for item in batch_items]
                hashes_batch = [item[1] for item in batch_items]

                #Delete Existing rows
                for ch in chunks_batch:
                    cur.execute("DELETE FROM chunks WHERE source_type = %s AND doc_id = %s", (ch.source_type, ch.doc_id))

                #Batch Embedding
                embeddings = embed_batch([c.chunk_text for c in chunks_batch])

                records = []
                cache_records = []
                for ch, emb, h in zip(chunks_batch, embeddings, hashes_batch):
                    records.append((
                        ch.source_type,
                        ch.source_url,
                        ch.doc_id,
                        ch.title,
                        ch.chunk_text,
                        emb,
                        json.dumps(ch.metadata.model_dump())
                    ))
                    cache_records.append((
                        ch.source_type,
                        ch.doc_id,
                        h
                    ))
                unique_cache = list({(r[0], r[1]): r for r in cache_records}.values())
                execute_values(
                    cur,
                    """
                    INSERT INTO chunks (source_type, source_url, doc_id, title, chunk_text, embedding, metadata)
                    VALUES %s
                    """,
                    records,
                )
                execute_values(
                    cur,
                    """
                    INSERT INTO crawl_cache (source_type, doc_id, content_hash)
                    VALUES %s
                    ON CONFLICT (source_type, doc_id) DO UPDATE SET content_hash = EXCLUDED.content_hash, updated_at = now()
                    """,
                    unique_cache,
                )
                conn.commit()
                print(f"Updated {min(i + BATCH_SIZE,  len(changed_chunks))}/{len(changed_chunks)} chunks.")
    print ("Pipeline completed successfully")

if __name__ == "__main__":
    run_pipeline()