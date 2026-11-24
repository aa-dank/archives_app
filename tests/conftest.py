"""Shared disposable schemas for file lifecycle tests."""

import pytest
from sqlalchemy import text

from archives_application import db


@pytest.fixture
def create_file_content_tables():
    """Create SQLite equivalents without PostgreSQL vector/JSONB/FTS types.

    Deliberately omit ON DELETE CASCADE so tests require explicit, correctly
    ordered cleanup of every dependent table. Call inside the test app context.
    """
    def create():
        definitions = (
            """CREATE TABLE file_contents (
                file_hash TEXT PRIMARY KEY REFERENCES files(hash), source_text TEXT,
                text_length INTEGER, updated_at TIMESTAMP, minilm_model TEXT,
                minilm_emb TEXT, source_metadata TEXT DEFAULT '{}')""",
            """CREATE TABLE file_content_fts_chunks (
                id INTEGER PRIMARY KEY, file_hash TEXT NOT NULL REFERENCES file_contents(file_hash),
                chunk_index INTEGER, chunk_text TEXT, chunked_at TIMESTAMP)""",
            """CREATE TABLE file_date_mentions (
                file_hash TEXT REFERENCES files(hash), mention_date DATE, granularity TEXT,
                mentions_count INTEGER DEFAULT 1, extractor TEXT, extracted_at TIMESTAMP,
                PRIMARY KEY (file_hash, mention_date, granularity))""",
            """CREATE TABLE file_content_failures (
                file_hash TEXT PRIMARY KEY REFERENCES files(hash), stage TEXT, error TEXT,
                attempts INTEGER DEFAULT 1, last_failed_at TIMESTAMP,
                source_metadata TEXT DEFAULT '{}')""",
        )
        for definition in definitions:
            db.session.execute(text(definition))
        db.session.commit()
    return create
