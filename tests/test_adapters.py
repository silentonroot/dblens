import unittest
from unittest.mock import MagicMock, patch
import fakeredis
from db_lens.adapters import RedisDatabase, PostgreSQLDatabase, MySQLDatabase, validate_select, connect
from db_lens.app import DBLens
from textual.widgets import Tree, DataTable, TextArea

class SQLTests(unittest.TestCase):
    def test_select_guard(self):
        for dialect in ['postgres', 'mysql']:
            for sql in ['SELECT 1', 'WITH x AS (SELECT 1) SELECT * FROM x', 'SELECT 1 UNION SELECT 2']:
                validate_select(sql, dialect)
            for sql in ['DELETE FROM x', 'SELECT 1; SELECT 2', 'SELECT * INTO new_table FROM x', 'SELECT * FROM x FOR UPDATE', 'WITH x AS (DELETE FROM foo RETURNING *) SELECT * FROM x']:
                with self.assertRaises(ValueError): validate_select(sql, dialect)

    def test_postgres_rollback_and_metadata_parameters(self):
        with patch('psycopg.connect') as factory:
            db = PostgreSQLDatabase('postgresql://u:p@localhost/demo')
            conn = factory.return_value
            cursor = conn.cursor.return_value.__enter__.return_value
            cursor.description = [('schema',), ('table',)]
            cursor.fetchmany.return_value = [('public', 'odd".name')]
            names = db.tables()
            self.assertEqual(names, ['"public"."odd"".name"'])
            db.schema(names[0])
            self.assertEqual(cursor.execute.call_args.args[1], ('public', 'odd".name'))
            cursor.execute.side_effect = RuntimeError('error')
            with self.assertRaises(RuntimeError): db.query('SELECT 1')
            self.assertEqual(conn.rollback.call_count, 3)
            self.assertTrue(conn.read_only)

    def test_mysql_readonly_and_url(self):
        with patch('pymysql.connect') as factory:
            db = MySQLDatabase('mysql://u:p%40ss@localhost:3307/demo')
            self.assertEqual(factory.call_args.kwargs['password'], 'p@ss')
            self.assertEqual(factory.call_args.kwargs['port'], 3307)
            cur = factory.return_value.cursor.return_value.__enter__.return_value
            cur.description = [('x',)]
            cur.fetchmany.return_value = [(1,)]
            self.assertEqual(db.query('SELECT 1').rows, [(1,)])
            self.assertEqual(cur.execute.call_args_list[0].args[0], 'START TRANSACTION READ ONLY')
            factory.return_value.rollback.assert_called_once()

class RedisTests(unittest.TestCase):
    def setUp(self):
        self.db = RedisDatabase.__new__(RedisDatabase)
        self.db.client = fakeredis.FakeRedis()
        self.db.path = 'Redis connection'
        self.db.client.set('text', 'hello')
        self.db.client.set(b'\xffkey', b'\xffvalue')
        self.db.client.hset('hash', mapping={'a':'one'})
        self.db.client.rpush('list', 'one', 'two')
        self.db.client.sadd('set', 'one', 'two')
        self.db.client.zadd('sorted', {'one':1.5})
        self.db.client.xadd('stream', {'field':'value'})

    def test_types_metadata_binary_and_commands(self):
        self.assertEqual(len(self.db.tables()), 7)
        for key in ['text', b'\xffkey', 'hash', 'list', 'set', 'sorted', 'stream']:
            self.assertTrue(self.db.browse(key).rows)
        self.assertEqual(self.db.browse(b'\xffkey').rows, [('0xff76616c7565',)])
        self.assertEqual(self.db.schema('text').rows[0][2], -1)
        self.assertEqual(self.db.query('TYPE text').rows, [('string',)])
        self.assertEqual(self.db.query('SCAN h*').rows, [('hash',)])
        with self.assertRaises(ValueError): self.db.query('DEL text')
        self.assertEqual(self.db.client.get('text'), b'hello')
        self.db.client.set('big', 'x' * 3000)
        self.assertTrue(self.db.browse('big').truncated)
        self.assertEqual(len(self.db.browse('big').rows[0][0]), 2000)
        self.db.client.rpush('long', *range(510))
        self.assertEqual(len(self.db.browse('long').rows), 500)
        self.assertTrue(self.db.browse('long').truncated)

    def test_scan_empty_batches_and_duplicates(self):
        self.db.client = MagicMock()
        self.db.client.scan.side_effect = [(5, []), (6, [b'a', b'a']), (0, [b'b'])]
        self.assertEqual(self.db.scan(), [b'a', b'b'])
        self.assertFalse(self.db.catalog_truncated)

class RedisUITests(unittest.IsolatedAsyncioTestCase):
    async def test_key_sidebar_and_metadata(self):
        with patch('redis.Redis.from_url', return_value=fakeredis.FakeRedis()) as factory:
            factory.return_value.set('hello', 'world')
            app = DBLens('redis://localhost/0')
            async with app.run_test(size=(110,34)) as pilot:
                tree = app.query_one(Tree)
                tree.select_node(tree.root.children[0])
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count, 1)
                await pilot.click('#schema')
                self.assertEqual(len(app.query_one(DataTable).columns), 3)
                app.query_one(TextArea).load_text('SCAN missing*')
                await pilot.click('#run')
                self.assertEqual(len(tree.root.children), 0)
