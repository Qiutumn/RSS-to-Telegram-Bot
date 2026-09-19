from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return '''
        ALTER TABLE "sub" ADD "topic_id" INT NOT NULL DEFAULT 0;
        ALTER TABLE "sub" DROP CONSTRAINT "uid_sub_user_id_029239";
        ALTER TABLE "sub" ADD CONSTRAINT "uid_sub_user_feed_topic"
            UNIQUE ("user_id", "feed_id", "topic_id");
    '''


async def downgrade(db: BaseDBAsyncClient) -> str:
    raise RuntimeError('Restore a pre-upgrade backup to roll back forum topics without losing subscriptions')
