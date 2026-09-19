import asyncio
import importlib.util
from pathlib import Path
import re
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telethon import events
from telethon.tl import types
from telethon.errors import BadRequestError
from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from src import db, env
from src.command.inner import sub as subscriptions, utils
from src.command.utils import execute_in_topic, get_callback_tail, parse_command_get_sub_or_user_and_param
from src.monitor._notifier import Notifier
from src.parsing.message import MessageDispatcher, Message, MEDIA_GROUP
from src.topics import current_topic_id, message_topic_id, topic_filter, topic_scope
from src.topics import REMOTE_TARGET_PATTERN, CALLBACK_TARGET_PATTERN, subscription_target

CHAT = -1001234567890


def message(topic=42, reply=50):
    return types.Message(id=99, peer_id=types.PeerChannel(1234567890), message='/list',
                         reply_to=types.MessageReplyHeader(
                             reply_to_msg_id=reply, reply_to_top_id=topic, forum_topic=True))


class TopicContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_and_opml_matchers(self):
        pattern = re.compile(r'/sub(?:\s+' + REMOTE_TARGET_PATTERN + r')?')
        match = pattern.match('/sub -1001234567890:42 https://example.test/rss')
        self.assertEqual(match['target'], str(CHAT))
        self.assertEqual(match['topic'], '42')
        self.assertEqual(pattern.match('/sub @example:84')['topic'], '84')
        self.assertEqual(pattern.match('/sub -1001234567890:bad')['topic'], 'bad')
        self.assertIsNotNone(re.match(r'.*?(?:' + REMOTE_TARGET_PATTERN + r')?', ''))
        callback = re.fullmatch(r'get_list_page\|\d+' + CALLBACK_TARGET_PATTERN,
                                'get_list_page|2%1234567890:42')
        self.assertEqual(callback['topic'], '42')
        self.assertEqual(subscription_target(SimpleNamespace(user_id=CHAT, topic_id=42)), f'{CHAT}:42')

    async def test_extract_topic_and_general(self):
        self.assertEqual(message_topic_id(message()), 42)
        self.assertEqual(message_topic_id(message(topic=None, reply=42)), 42)
        self.assertEqual(message_topic_id(message(topic=1)), 0)
        self.assertEqual(message_topic_id(SimpleNamespace(reply_to=None)), 0)

    async def test_concurrent_scopes_do_not_leak(self):
        async def task(topic):
            with topic_scope(CHAT, topic):
                await asyncio.sleep(0)
                self.assertEqual(current_topic_id(CHAT), topic)
                self.assertEqual(topic_filter(CHAT + 1), {})
        await asyncio.gather(task(42), task(84))
        self.assertEqual(topic_filter(CHAT), {})

    async def test_event_response_and_exception_restore(self):
        event = events.NewMessage.Event(message())
        event.pattern_match = None
        original = AsyncMock()
        event.respond = original
        async def command(event):
            self.assertEqual(current_topic_id(CHAT), 42)
            await event.respond('ok')
            raise ValueError('expected')
        with self.assertRaises(ValueError):
            await execute_in_topic(event, CHAT, command)
        original.assert_awaited_once_with('ok', reply_to=42)
        self.assertIs(event.respond, original)
        self.assertEqual(topic_filter(CHAT), {})

    async def test_callback_uses_source_message_topic(self):
        event = MagicMock(spec=events.CallbackQuery.Event)
        event.pattern_match = None
        event.is_group = True
        event.get_message = AsyncMock(return_value=message(topic=84))
        event.respond = AsyncMock()
        async def command(event):
            self.assertEqual(current_topic_id(CHAT), 84)
            await event.respond('ok')
        await execute_in_topic(event, CHAT, command)
        event.respond.assert_awaited_once_with('ok', reply_to=84)

    async def test_missing_callback_message_fails_closed(self):
        event = MagicMock(spec=events.CallbackQuery.Event)
        event.pattern_match = None
        event.is_group = True
        event.get_message = AsyncMock(return_value=None)
        event.answer = AsyncMock()
        command = AsyncMock()
        await execute_in_topic(event, CHAT, command)
        command.assert_not_awaited()
        event.answer.assert_awaited_once()

    async def test_remote_command_and_callback_tail(self):
        event = SimpleNamespace(pattern_match=re.match(
            r'(?P<target>-100\d+):(?P<topic>\d+)', f'{CHAT}:42'),
            message=None, respond=AsyncMock(), is_private=True, chat=SimpleNamespace(id=123456))
        bot = MagicMock()
        bot.get_entity = AsyncMock(return_value=SimpleNamespace(forum=True))
        bot.get_messages = AsyncMock(return_value=SimpleNamespace(
            action=types.MessageActionTopicCreate('Test', 0)))
        async def command(event, **kwargs):
            self.assertEqual(current_topic_id(CHAT), 42)
            self.assertEqual(get_callback_tail(event, CHAT), '%1234567890:42')
        with patch.object(env, 'bot', bot):
            await execute_in_topic(event, CHAT, command, chat_id=CHAT)

    async def test_invalid_remote_topic_rejected(self):
        event = SimpleNamespace(pattern_match=re.fullmatch(REMOTE_TARGET_PATTERN, f'{CHAT}:bad'),
                                message=None, respond=AsyncMock(), is_group=False, chat_id=123456)
        command = AsyncMock()
        await execute_in_topic(event, CHAT, command)
        command.assert_not_awaited()
        event.respond.assert_awaited_once()


class DatabaseTopicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env.loop = asyncio.get_running_loop()
        await db.init()
        await db.User.create(id=CHAT, state=1)
        self.feed = await db.Feed.create(link='https://example.test/rss', title='Feed')

    async def asyncTearDown(self):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
        await db.close()

    async def add(self, topic):
        with topic_scope(CHAT, topic):
            result = await subscriptions.sub(CHAT, self.feed.link, lang='en')
        self.assertIsNotNone(result['sub'], result)
        return result['sub']

    async def test_same_feed_multiple_topics_and_duplicate_rejected(self):
        first, second = await self.add(42), await self.add(84)
        self.assertNotEqual(first.id, second.id)
        with topic_scope(CHAT, 42):
            result = await subscriptions.sub(CHAT, self.feed.link, lang='en')
            self.assertIsNone(result['sub'])
        with self.assertRaises(IntegrityError):
            await db.Sub.create(user_id=CHAT, feed=self.feed, topic_id=42)

    async def test_list_export_unsub_and_settings_isolated(self):
        first, second = await self.add(42), await self.add(84)
        with topic_scope(CHAT, 42):
            page = await utils.get_sub_list_by_page(CHAT, 1, 99)
            self.assertEqual([s.id for s in page[2]], [first.id])
            self.assertEqual((await subscriptions.export_opml(CHAT)).count(b'<outline '), 1)
            chosen, _ = await parse_command_get_sub_or_user_and_param(f'/set_title {second.id} bad', CHAT)
            self.assertIsNone(chosen)
            result = await subscriptions.unsub(CHAT, sub_id=second.id, lang='en')
            self.assertIsNone(result['sub'])
            await subscriptions.unsub_all(CHAT, lang='en')
        self.assertEqual(await db.Sub.all().values_list('id', flat=True), [second.id])

    async def test_pause_and_limit_are_scoped_correctly(self):
        await self.add(42)
        second = await self.add(84)
        await db.User.filter(id=CHAT).update(sub_limit=2)
        with topic_scope(CHAT, 42):
            limited, count, _, _ = await utils.check_sub_limit(CHAT)
            self.assertTrue(limited)
            self.assertEqual(count, 2)
            await utils.activate_or_deactivate_all_subs(CHAT, activate=False)
        await second.refresh_from_db()
        self.assertEqual(second.state, 1)

    async def test_url_migration_preserves_sibling_topics(self):
        await self.add(42)
        await self.add(84)
        destination = await db.Feed.create(link='https://example.test/new', title='New')
        await db.Sub.create(user_id=CHAT, feed=destination, topic_id=42)
        await subscriptions.migrate_to_new_url(self.feed, destination.link)
        self.assertEqual(sorted(await db.Sub.all().values_list('topic_id', flat=True)), [42, 84])

    async def test_topic_error_pauses_only_that_destination(self):
        first, second = await self.add(42), await self.add(84)
        bot = MagicMock()
        bot.get_input_entity = AsyncMock(return_value=types.InputPeerChannel(1234567890, 0))
        leave = AsyncMock()
        notifier = Notifier(self.feed, [first, second], on_blocked_cb=leave)
        with patch.object(env, 'bot', bot), patch.object(
                MessageDispatcher, 'send_messages', AsyncMock(side_effect=BadRequestError(None, 'TOPIC_CLOSED'))):
            await notifier._send(first, 'test')
        await first.refresh_from_db()
        await second.refresh_from_db()
        self.assertEqual((first.state, second.state), (0, 1))
        leave.assert_not_awaited()

    async def test_opml_import_creates_subscriptions_in_current_topic(self):
        with topic_scope(CHAT, 42):
            result = await subscriptions.subs(CHAT, [(self.feed.link, 'Imported title')], lang='en')
        self.assertEqual(result['success_count'], 1)
        row = await db.Sub.get(user_id=CHAT, topic_id=42)
        self.assertEqual(row.title, 'Imported title')


class SendingTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_split_chunks_target_same_topic(self):
        dispatcher = MessageDispatcher(CHAT, html='test', topic_id=42)
        dispatcher.messages = [SimpleNamespace(send=AsyncMock(return_value=message())) for _ in range(3)]
        await dispatcher.send_messages()
        for part in dispatcher.messages:
            part.send.assert_awaited_once_with(reply_to=42)

    async def test_general_preserves_original_reply_chain(self):
        dispatcher = MessageDispatcher(CHAT, html='test')
        first = message(topic=1)
        dispatcher.messages = [SimpleNamespace(send=AsyncMock(return_value=first)),
                               SimpleNamespace(send=AsyncMock(return_value=None))]
        await dispatcher.send_messages()
        dispatcher.messages[0].send.assert_awaited_once_with(reply_to=None)
        dispatcher.messages[1].send.assert_awaited_once_with(reply_to=first)

    async def test_text_and_single_media_topic(self):
        bot = AsyncMock()
        bot.get_input_entity.return_value = types.InputPeerChannel(1234567890, 0)
        bot._file_to_media.return_value = (None, types.InputMediaEmpty(), None)
        bot._get_response_message = MagicMock(return_value=None)
        with patch.object(env, 'bot', bot):
            await Message(CHAT, 'text', topic_id=42).send(reply_to=42)
            await Message(CHAT, 'caption', media=types.InputMediaEmpty(), topic_id=84).send(reply_to=84)
        requests = [c.args[0] for c in bot.call_args_list]
        self.assertEqual([r.reply_to.reply_to_msg_id for r in requests], [42, 84])
        self.assertEqual([r.reply_to.top_msg_id for r in requests], [42, 84])

    async def test_album_contains_topic_root(self):
        bot = AsyncMock()
        bot._file_to_media.return_value = (None, types.InputMediaEmpty(), None)
        bot.get_input_entity.return_value = types.InputPeerChannel(1234567890, 0)
        bot._get_response_message = MagicMock(return_value=[])
        album = Message(CHAT, 'album', media=['one', 'two'], media_type=MEDIA_GROUP, topic_id=42)
        with patch.object(env, 'bot', bot):
            await album.send(reply_to=42)
        request = bot.call_args.args[0]
        self.assertEqual(request.reply_to.reply_to_msg_id, 42)
        self.assertEqual(request.reply_to.top_msg_id, 42)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_upgrade_preserves_existing_subscriptions(self):
        await self.check_upgrade('sqlite', 'sqlite://:memory:')

    @unittest.skipUnless(os.environ.get('TEST_POSTGRES_URL'), 'Optional disposable PostgreSQL server')
    async def test_postgres_upgrade_preserves_existing_subscriptions(self):
        await self.check_upgrade('pgsql', os.environ['TEST_POSTGRES_URL'])

    async def check_upgrade(self, backend, db_url):
        connection_name = f'migration_{backend}'
        await Tortoise.init(config={
            'connections': {connection_name: db_url},
            'apps': {'models': {'models': ['src.db.models'], 'default_connection': connection_name}},
        })
        connection = Tortoise.get_connection(connection_name)
        folder = Path(__file__).resolve().parents[1] / f'src/db/migrations_{backend}/models'
        try:
            for path in sorted(folder.glob('*.py')):
                spec = importlib.util.spec_from_file_location('migration', path)
                migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration)
                if path.name.startswith('5_'):
                    await connection.execute_script('''
                        INSERT INTO "user" (id) VALUES (123);
                        INSERT INTO feed (id,link,title) VALUES (1,'https://example.test/rss','Feed');
                        INSERT INTO sub (user_id,feed_id,title) VALUES (123,1,'Custom');
                    ''')
                    if backend == 'sqlite':
                        await connection.execute_script("UPDATE sqlite_sequence SET seq=99 WHERE name='sub';")
                await connection.execute_script(await migration.upgrade(connection))
            row = await db.Sub.get(id=1)
            self.assertEqual((row.user_id, row.feed_id, row.topic_id, row.title), (123, 1, 0, 'Custom'))
            sibling = await db.Sub.create(user_id=123, feed_id=1, topic_id=42)
            self.assertGreater(sibling.id, 99 if backend == 'sqlite' else 1)
            with self.assertRaises(IntegrityError):
                await db.Sub.create(user_id=123, feed_id=1, topic_id=0)
            await db.User.filter(id=123).delete()
            self.assertEqual(await db.Sub.all().count(), 0)
        finally:
            await Tortoise.close_connections()
