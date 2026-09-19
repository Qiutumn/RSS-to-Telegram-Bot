"""Forum destinations and task-local command scope.

Stored topic 0 denotes General (Telegram topic 1), or a non-forum chat.
The monitor uses persisted destinations; only command queries use this scope.
"""
from contextvars import ContextVar
from contextlib import contextmanager


REMOTE_TARGET_PATTERN = r'(?P<target>@\w{4,}|(-100|\+)\d+)(?::(?P<topic>[^\s]+))?(?=\s|$)'
CALLBACK_TARGET_PATTERN = r'(%(?P<target>\+?\d+)(?::(?P<topic>\d+))?)?'


_scope = ContextVar('subscription_topic_scope', default=None)
TOPIC_ERRORS = frozenset({'TOPIC_CLOSED', 'TOPIC_DELETED', 'REPLY_MESSAGE_ID_INVALID'})


def normalize_topic_id(topic_id: int) -> int:
    if not 0 <= topic_id <= 2147483647:
        raise ValueError('Invalid topic ID')
    return 0 if topic_id == 1 else topic_id


def message_topic_id(message) -> int:
    reply = getattr(message, 'reply_to', None)
    if reply and getattr(reply, 'forum_topic', False):
        return normalize_topic_id(reply.reply_to_top_id or reply.reply_to_msg_id)
    return 0


@contextmanager
def topic_scope(chat_id: int, topic_id: int):
    token = _scope.set((chat_id, normalize_topic_id(topic_id)))
    try:
        yield
    finally:
        _scope.reset(token)


def topic_filter(chat_id: int) -> dict:
    scope = _scope.get()
    return {'topic_id': scope[1]} if scope is not None and scope[0] == chat_id else {}


def current_topic_id(chat_id: int) -> int:
    return topic_filter(chat_id).get('topic_id', 0)


def subscription_target(sub) -> str:
    return f"{sub.user_id}:{sub.topic_id}" if sub.topic_id else str(sub.user_id)
