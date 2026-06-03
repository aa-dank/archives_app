# Archive Search Architecture Plan: Chunked PostgreSQL FTS First, Similarity Later

## Purpose

This document consolidates the prior search research and subsequent design decisions for implementing document-content search in the UCSC PPDO Archives / Business Services database stack.

The immediate implementation target is the `business_services_db` repository, specifically Alembic/database-model changes for a new `file_content_fts_chunks` table and supporting indexes. The goal is to get content keyword search working reliably first.

Semantic similarity / KNN-style functionality remains part of the long-term architecture, but the first implementation phase should use existing `file_contents.minilm_emb` where possible and defer a dedicated embedding chunk table until after FTS is operational.

---

## Repository Context

Target repository:

```text
/home/projects/business_services_db
```

Relevant areas:

```text
alembic/versions/                  # Alembic migrations
python_lib/business_services_db_models/models.py
misc/archive_app_search_research.md
notebooks/analytics.ipynb
reference/ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md
```

A prior implementation attempt tried to add a generated `tsvector` column directly to `file_contents.source_text`. That approach failed because some `source_text` rows generate `tsvector` values larger than PostgreSQL allows.

---

## Background From Prior Search Research

The original search research recommended staying PostgreSQL-native first rather than immediately adding Elasticsearch/OpenSearch/Solr.

That general recommendation still stands.

The archives system already has most of the ingredients of a search product:

- `files`: unique file identity by content hash.
- `file_locations`: one or more physical/server locations for a file.
- `file_contents`: extracted text and existing vector embeddings.
- `projects`, `caans`, `project_caans`: project/building context.
- `file_date_mentions`: possible future date-filtering support.

The key product model remains:

```text
file hash = primary result identity
file locations = display/access metadata
content chunks = retrieval units
```

This is important because a single file hash may appear in multiple server paths. Search results should usually collapse to one result per file/hash, then show one or more useful locations.

The original research identified these long-term retrieval modes:

1. filename/path search
2. content keyword search
3. semantic/vector search
4. eventually hybrid search using rank fusion

This plan keeps that long-term direction. The main change is that content search should not be implemented as one `tsvector` for the entire `source_text` value.

---

## Why the Architecture Changed

The first attempted implementation was:

```sql
ALTER TABLE file_contents
ADD COLUMN search_vector tsvector
GENERATED ALWAYS AS (
    to_tsvector('simple', coalesce(source_text, ''))
) STORED;
```

PostgreSQL rejected this on existing data with:

```text
string is too long for tsvector (... bytes, max 1048575 bytes)
```

Observed corpus characteristics from notebook analysis:

```text
file_contents rows:       ~228,557
table size:               ~4001 MB
p50 source_text chars:    ~1,870
p75 source_text chars:    ~6,465
p90 source_text chars:    ~28,004
p95 source_text chars:    ~73,326
p99 source_text chars:    ~425,348
max source_text chars:    ~61,238,342
```

Estimated chunk counts:

```text
5k chars:   ~1,193,260 chunks
10k chars:  ~672,898 chunks
20k chars:  ~419,206 chunks
50k chars:  ~273,931 chunks
```

Conclusion:

- Most documents are small enough for normal FTS.
- A long tail of very large extracted-text rows makes full-document `tsvector` unsafe.
- Chunking is not a tangent from the research; it is the necessary physical implementation of the same PostgreSQL-native search strategy.

---

## High-Level Long-Term Search Vision

The long-term search system should support:

### 1. Path / filename search

Existing search path, likely still using `file_locations.filename` and `file_locations.file_server_directories`.

Useful for:

- known filenames
- project numbers in paths
- file extensions
- filing-code folder names

### 2. Content keyword search

New PostgreSQL FTS over chunked extracted text.

Useful for:

- exact concepts
- document terms
- OCR text
- specifications
- RFI / submittal / report terms

### 3. Similarity search / KNN

Initially use existing `file_contents.minilm_emb` for file-level similarity.

Later add chunk-level embeddings with a dedicated table.

Useful for:

- “find documents like this one”
- users who lack domain vocabulary
- HVAC/equipment searches where good keywords are unknown
- archiving support: “show similar already-filed documents and their filing codes”

### 4. Hybrid search

Eventually combine lexical and semantic results using rank-based fusion, not raw score blending.

Candidate method:

```text
reciprocal rank fusion (RRF)
```

RRF is preferable because FTS ranking scores and vector distances live on different scales.

---

## Phase 1 Scope

Implement only:

```text
file_content_fts_chunks
```

Do not implement `file_content_embedding_chunks` yet.

Do not build full hybrid search yet.

Do not remove or replace `file_contents.source_text`.

Do not try to resurrect the failed full-document generated-column migration.

Phase 1 should provide:

1. database table for FTS chunks
2. generated `tsvector` column on chunk text
3. GIN index on that generated vector
4. supporting indexes for file/hash grouping
5. a backfill approach for existing `file_contents.source_text`
6. a query pattern that returns file/hash-level results, not chunk-level results

---

## Proposed Table: `file_content_fts_chunks`

### Design Notes

- This table is derived from `file_contents.source_text`.
- It should be safe to delete and rebuild.
- The canonical extracted text remains `file_contents.source_text`.
- Each row is one lexical-search chunk.
- Search returns matching chunks internally, then groups back to `file_hash`.
- Use PostgreSQL `simple` text-search config initially.
- Store chunk index to preserve order within a chunk set.
- Store chunk text for search and snippet generation.
- Store a chunk-set timestamp (chunked_at) so multiple versions of chunks can coexist and query logic can select one set.
- Do not add page metadata, chunk-set tables, or explicit chunking-version features in phase 1.
- Treat chunks as derived rows owned by `file_contents`; when the parent `file_contents` row is removed, its chunks should cascade-delete.

Rationale for `simple`:

- The corpus contains construction records, filenames, acronyms, OCR noise, project numbers, equipment identifiers, and technical terms.
- English stemming/stopword behavior may harm exact technical retrieval.
- `simple` is more predictable for first implementation.

### Initial Chunking Choice

Start with:

```text
chunking mode: fixed 20,000-character chunks
char overlap:  none
chunked_at:    chunk-set timestamp assigned once per build (same value on all rows in that set)
```

Reasoning:

- ~419k chunks were estimated for plain 20k-character chunking.
- This row count is reasonable given the broader database already has hundreds of thousands of files and nearly a million file locations.
- No overlap keeps storage/index bloat lower.
- Boundary-loss edge cases are acceptable for the first implementation.
- The largest 1k files may be imperfectly served, but that is acceptable for phase 1.

Overlap can be revisited later if snippet quality or phrase-boundary issues are material.


### SQL DDL Draft

```sql
CREATE TABLE file_content_fts_chunks (
    id bigserial PRIMARY KEY,

    file_hash text NOT NULL
        REFERENCES file_contents(file_hash)
        ON DELETE CASCADE,

    chunk_index integer NOT NULL,
    chunk_text text NOT NULL,

    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(chunk_text, ''))
    ) STORED,

    chunked_at timestamptz NOT NULL,

    CONSTRAINT uq_file_content_fts_chunks_file_hash_chunk_index
        UNIQUE (file_hash, chunk_index, chunked_at),

    CONSTRAINT ck_file_content_fts_chunks_nonempty
        CHECK (length(chunk_text) > 0)
);
```

`file_content_fts_chunks.file_hash` should reference `file_contents.file_hash` with `ON DELETE CASCADE` because chunks are derived from the parent extracted text. If a `file_contents` row is deleted or replaced, its chunks should not remain orphaned.

### Indexes

Use `CREATE INDEX CONCURRENTLY` where practical for production migrations.

```sql
CREATE INDEX CONCURRENTLY idx_file_content_fts_chunks_search_vector
ON file_content_fts_chunks
USING GIN (search_vector);

CREATE INDEX CONCURRENTLY idx_file_content_fts_chunks_chunked_at
ON file_content_fts_chunks (chunked_at);
```

Do not add separate btree indexes on `file_hash` or `(file_hash, chunk_index)` in phase 1. The `UNIQUE (file_hash, chunk_index, chunked_at)` constraint already creates a btree index that supports lookup by leading `file_hash`.
```

Including `chunked_at` in the uniqueness constraint allows multiple chunk sets per file hash over time. Query logic should explicitly choose one chunk set (typically the latest `chunked_at` per `file_hash`) to avoid mixing historical and current chunks.
---

## Alembic Implementation Notes

The failed migration should not be extended blindly. Prefer a new migration that supersedes the approach.

Important Alembic detail:

```text
CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
```

Use:

```python
with op.get_context().autocommit_block():
    op.execute("""
        CREATE INDEX CONCURRENTLY ...
    """)
```

Potential migration sequence:

1. create `file_content_fts_chunks` table
2. create non-concurrent constraints as part of table creation
3. create indexes concurrently in autocommit blocks
4. do not backfill inside the schema migration if the backfill may be long-running

Recommended: schema migration only creates the table/indexes. Backfill should be a separate script/task.

---

## Python Model Draft

Add to:

```text
python_lib/business_services_db_models/models.py
```

Approximate SQLAlchemy model:

```python
class FileContentFtsChunkModel(Base):
    __tablename__ = "file_content_fts_chunks"

    id = Column(BigInteger, primary_key=True)
    file_hash = Column(
        Text,
        ForeignKey("file_contents.file_hash", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)

    # Generated column is defined in Alembic DDL.
    # It may be omitted from the ORM or mapped read-only depending on repo conventions.
    # search_vector = Column(...)

    chunked_at = Column(DateTime(timezone=True), nullable=False)
```

Adjust imports and base class conventions to match the existing model package.

Also add uniqueness/check/index metadata in the model if this repo keeps SQLAlchemy metadata aligned with database DDL.

---

## Backfill Strategy

Backfill should be script/task based, not a giant Alembic data migration.

Pseudo-flow:

```text
for each file_contents row with non-empty source_text:
    set chunked_at_value = current timestamp (once per file_hash rebuild)
    split source_text into fixed 20k-char chunks
    insert rows into file_content_fts_chunks with chunked_at=chunked_at_value
    commit in batches
```

Important properties:

- batch commits
- resumable
- logs progress
- can skip already-chunked files
- can rebuild a single file_hash
- can identify stale chunks by comparing `chunked_at` to the source content/extraction update time if such parent metadata exists
- can insert a new chunk set without deleting historical chunk sets
- can optionally prune old chunk sets on a retention policy
- does not store page metadata or multiple explicit chunk versions in phase 1

Basic fallback chunking function:

```python
def iter_text_chunks(text: str, chunk_size: int = 20_000):
    if not text:
        return
    start = 0
    index = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end]
        if chunk.strip():
            yield {
                "chunk_index": index,
                "chunk_text": chunk,
            }
        start = end
        index += 1
```

Future improvements: 
- Improve chunk boundary logic with paragraph/newline splitting if fixed-width chunks produce poor snippets. Do not block phase 1 on that.
- Add paginated chunk boundary logic for documents with multiple pages. Logic example:
1. preserve page boundaries when page metadata exists
2. combine small adjacent pages until the chunk approaches ~20k chars
3. if one page exceeds the hard max, split that page internally
4. prefer paragraph/newline split points before hard character cuts
5. if no page metadata exists, look for page-break markers such as 
6. if no page signal exists, fall back to fixed character chunks

---

## Phase 1 Search Query Shape

Search chunks first, limit early, then group back to files.

When multiple chunk sets exist for a file hash, constrain the search to one chunk set per file hash (usually `max(chunked_at)` for each `file_hash`) before ranking/limiting.

Do not join to `file_locations` before limiting and grouping, or duplicate locations may distort ranking.

Example query:

```sql
WITH q AS (
    SELECT websearch_to_tsquery('simple', :query_text) AS query
),
matching_chunks AS (
    SELECT
        c.file_hash,
        c.id AS chunk_id,
        c.chunk_index,
        ts_rank_cd(c.search_vector, q.query) AS chunk_rank
    FROM file_content_fts_chunks c, q
    WHERE c.search_vector @@ q.query
    ORDER BY chunk_rank DESC
    LIMIT 1000
),
file_scores AS (
    SELECT
        file_hash,
        max(chunk_rank) AS best_chunk_rank,
        count(*) AS matching_chunks
    FROM matching_chunks
    GROUP BY file_hash
)
SELECT
    file_hash,
    best_chunk_rank,
    matching_chunks
FROM file_scores
ORDER BY best_chunk_rank DESC, matching_chunks DESC
LIMIT 50;
```

Then join the top file hashes to:

```text
files
file_locations
```

for display.

### Snippets

Use `ts_headline` only after narrowing candidate chunks.

Example:

```sql
SELECT ts_headline(
    'simple',
    c.chunk_text,
    websearch_to_tsquery('simple', :query_text),
    'MaxFragments=2, MinWords=8, MaxWords=24'
)
FROM file_content_fts_chunks c
WHERE c.id = :best_chunk_id;
```

Avoid calling `ts_headline` across a huge candidate set before ranking/limiting.

---

## Result Display Model

Search should return file-level results.

Each result should include:

```text
file_hash
file_id
filename
best/current/preferred location(s)
content snippet
score/rank metadata
matching chunk count
```

Avoid displaying chunks as primary results. Chunks are retrieval evidence, not user-facing archive objects.

If a file has multiple locations, show one primary location plus an affordance for additional locations.

---

## Similarity / KNN Roadmap

Similarity is part of the long-term vision and should influence the schema direction, but it is not phase 1.

### Near Term

Use existing:

```text
file_contents.minilm_emb
```

for broad file-level similarity.

Example use cases:

- “find files similar to this file”
- “use this example HVAC document to find related documents”
- archiving helper: “show similarly filed examples”

Example SQL shape:

```sql
SELECT
    fc.file_hash,
    1 - (fc.minilm_emb <=> anchor.minilm_emb) AS cosine_similarity
FROM file_contents fc
JOIN file_contents anchor
  ON anchor.file_hash = :anchor_file_hash
WHERE fc.file_hash <> :anchor_file_hash
  AND fc.minilm_emb IS NOT NULL
  AND anchor.minilm_emb IS NOT NULL
ORDER BY fc.minilm_emb <=> anchor.minilm_emb
LIMIT 50;
```

### Later

Add:

```text
file_content_embedding_chunks
```

This should probably be separate from `file_content_fts_chunks` because embedding retrieval wants smaller chunks than FTS.

Likely embedding chunk size:

```text
2k–4k characters or ~512–1024 tokens
10–20% overlap
```

This future table should include:

```text
file_hash
chunk_index
char_start
char_end
chunk_text
minilm_model
minilm_emb vector(384)
mpnet_model optional
mpnet_emb vector(768) optional
chunking_version
embedded_at
```

Potential index:

```sql
CREATE INDEX CONCURRENTLY ...
USING hnsw (minilm_emb vector_cosine_ops)
WHERE minilm_emb IS NOT NULL;
```

or continue using ivfflat if the existing production pgvector setup prefers it.

---

## Archiving Assistance Vision

Similarity can support archiving workflows.

When a user is archiving a new document:

1. extract text
2. compute or approximate embedding
3. find similar existing files
4. inspect their file locations / filing-code folders
5. show likely filing-code precedents

Example output concept:

```text
Similar already-filed documents:

1. AHU startup checklist.pdf
   location: .../G23 - Commissioning/...
   similarity: 0.84

2. HVAC equipment submittal.pdf
   location: .../H - Submittals/...
   similarity: 0.81

3. Mechanical RFI response.pdf
   location: .../G12 - Request for Information/...
   similarity: 0.78

Likely filing code precedents:
- G23 - Commissioning: 4 similar examples
- H - Submittals: 3 similar examples
- G12 - Request for Information: 2 similar examples
```

Do not auto-file based only on similarity. Treat similarity as precedent/evidence.

---

## Things Not To Do In Phase 1

Do not:

- add a generated `tsvector` over full `file_contents.source_text`
- use `ILIKE '%term%'` as primary content search
- drop `file_contents.source_text`
- build Elasticsearch/OpenSearch/Solr yet
- build hybrid RRF before basic FTS works
- build embedding chunk table before FTS chunk table is proven
- display chunks as if they were files
- join all file locations before grouping search results by file hash

---

## Validation / Benchmarks

After migration and backfill, measure:

```text
row count in file_content_fts_chunks
size of table
size of GIN index
backfill runtime
p50/p95 query latency on realistic searches
quality of snippets
duplicate result behavior for files with many locations
```

Useful SQL:

```sql
SELECT count(*) FROM file_content_fts_chunks;

SELECT
    pg_size_pretty(pg_relation_size('file_content_fts_chunks')) AS table_size,
    pg_size_pretty(pg_total_relation_size('file_content_fts_chunks')) AS total_size;

SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE tablename = 'file_content_fts_chunks';
```

Check that search uses the GIN index:

```sql
EXPLAIN ANALYZE
SELECT id
FROM file_content_fts_chunks
WHERE search_vector @@ websearch_to_tsquery('simple', 'hvac equipment')
LIMIT 50;
```

Expected plan should include a bitmap/index scan using the GIN index, not a full sequential scan.

---

## Implementation Checklist For Copilot Agent

### Migration

- [ ] inspect current Alembic head state
- [ ] do not recreate the failed full-`source_text` generated-vector approach
- [ ] create new Alembic migration for `file_content_fts_chunks`
- [ ] add table DDL
- [ ] add constraints
- [ ] add GIN index on `search_vector`
- [ ] rely on the unique constraint's implicit btree index for `(file_hash, chunk_index, chunked_at)`
- [ ] use `autocommit_block()` for concurrent index creation
- [ ] add downgrade that drops indexes/table safely

### Models

- [ ] add `FileContentFtsChunkModel` to model package
- [ ] align FK target with actual `file_contents.file_hash` type/name
- [ ] preserve generated column DDL in migration even if ORM omits `search_vector`

### Backfill

- [ ] create script or management task for chunk backfill
- [ ] make it resumable
- [ ] batch inserts
- [ ] log progress
- [ ] support rebuilding one file hash
- [ ] support inserting a new chunk set for one file hash without deleting historical rows
- [ ] record `chunked_at` on inserted chunks
- [ ] rely on `ON DELETE CASCADE` from `file_contents` to remove derived chunks when parent content is deleted

### Search Query

- [ ] create SQL query/helper for content FTS
- [ ] use `websearch_to_tsquery('simple', query)`
- [ ] rank chunks with `ts_rank_cd`
- [ ] limit matching chunks before joining locations
- [ ] group results by `file_hash`
- [ ] retrieve best snippet from best chunk using `ts_headline`
- [ ] join to files/file_locations after ranking

### App Integration Later

- [ ] expose content search in Archives App UI
- [ ] support path/name and content toggles
- [ ] eventually add semantic similarity mode using existing `file_contents.minilm_emb`
- [ ] eventually add hybrid ranking

---

## Bottom Line

The original research direction remains valid:

```text
PostgreSQL-native search first
pgvector similarity as a parallel capability
hybrid search later
external search engine only if PostgreSQL-native search proves inadequate
```

The implementation detail changed because the real corpus has very large extracted-text outliers. A full-document generated `tsvector` is not safe.

The revised phase-1 architecture is:

```text
file_contents
  canonical source_text and existing file-level embeddings

file_content_fts_chunks
  derived fixed-width chunks targeting 20k characters
  generated tsvector
  GIN index
  grouped back to file_hash for results
```

This gets reliable content keyword search stood up without blocking future semantic similarity work.

