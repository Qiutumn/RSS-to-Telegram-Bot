"""Run in the application image, with no Telegram credentials or network needed."""
import asyncio
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(TOKEN='123456:offline-test', MANAGER='123456', NO_UVLOOP='1',
                  DATABASE_URL='sqlite:/tmp/rsstt-test-config/unused.sqlite3', MULTIPROCESSING='0')
sys.argv = ['tests', '-c', '/tmp/rsstt-test-config']
asyncio.set_event_loop(asyncio.new_event_loop())
from src import env  # noqa: E402: configure before unittest parses arguments

env.DATABASE_URL = 'sqlite://:memory:'
initial_loop = env.loop
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
initial_loop.close()
sys.exit(not result.wasSuccessful())
