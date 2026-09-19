"""Preserve existing subscriptions while adding per-topic uniqueness."""
import re

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    rows = await db.execute_query_dict("SELECT sql FROM sqlite_master WHERE type='table' AND name='sub'")
    schema = rows[0]['sql']
    schema, count = re.subn(r'UNIQUE\s*\(\s*"user_id"\s*,\s*"feed_id"\s*\)',
                            'UNIQUE ("user_id", "feed_id", "topic_id")', schema)
    if count != 1:
        raise RuntimeError('Unexpected subscription schema; refusing to alter it')
    schema = re.sub(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?"sub"', 'CREATE TABLE "sub_topics"', schema, count=1)
    # A column must precede table constraints in SQLite.
    start = schema.index('(') + 1
    schema = schema[:start] + '\n"topic_id" INT NOT NULL DEFAULT 0,\n' + schema[start:]
    columns = await db.execute_query_dict('PRAGMA table_info("sub")')
    names = ', '.join('"' + c['name'] + '"' for c in columns)
    indexes = await db.execute_query_dict(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='sub' AND sql IS NOT NULL")
    sequence = await db.execute_query_dict("SELECT seq FROM sqlite_sequence WHERE name='sub'")
    high_watermark = int(sequence[0]['seq']) if sequence else 0
    return '\n'.join([
        schema + ';',
        f'INSERT INTO "sub_topics" ({names}) SELECT {names} FROM "sub";',
        'DROP TABLE "sub";',
        'ALTER TABLE "sub_topics" RENAME TO "sub";',
        f"UPDATE sqlite_sequence SET seq=MAX(seq,{high_watermark}) WHERE name='sub';",
        f"INSERT INTO sqlite_sequence(name,seq) SELECT 'sub',{high_watermark} "
        "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name='sub');",
        *(i['sql'] + ';' for i in indexes),
    ])


async def downgrade(db: BaseDBAsyncClient) -> str:
    raise RuntimeError('Restore a pre-upgrade backup to roll back forum topics without losing subscriptions')
