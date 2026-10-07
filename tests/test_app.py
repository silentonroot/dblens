import sqlite3
import tempfile
import unittest
from pathlib import Path
from textual.widgets import DataTable, TextArea, Tree
from db_lens.database import SQLiteDatabase
from db_lens.app import DBLens

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sample.db'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE "odd""table" (id INTEGER, value TEXT)')
            db.executemany('INSERT INTO "odd""table" VALUES (?, ?)', [(i, '[red]hello') for i in range(505)])
        self.db = SQLiteDatabase(str(self.path))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_results_schema_and_read_only(self):
        self.assertEqual(self.db.tables(), ['odd"table'])
        result = self.db.query('SELECT * FROM "odd""table"')
        self.assertEqual(len(result.rows), 500)
        self.assertTrue(result.truncated)
        self.assertEqual(self.db.schema('odd"table').rows[0][1], 'id')
        for sql in ['DELETE FROM "odd""table"', 'DROP TABLE "odd""table"', "ATTACH ':memory:' AS extra", 'PRAGMA user_version=5']:
            with self.assertRaises(sqlite3.Error):
                self.db.query(sql)
        self.assertEqual(self.db.query('SELECT count(*) FROM "odd""table"').rows[0][0], 505)
        with self.assertRaises(sqlite3.Error):
            self.db.query('SELECT 1; SELECT 2;')

class UITests(unittest.IsolatedAsyncioTestCase):
    async def test_browse_query_schema_and_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE items (id INTEGER, label TEXT)')
                db.execute("INSERT INTO items VALUES (1, '[red]literal')")
            app = DBLens(str(path))
            async with app.run_test(size=(110, 34)) as pilot:
                await pilot.pause()
                tree = app.query_one(Tree)
                tree.select_node(tree.root.children[0])
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count, 1)
                await pilot.click('#schema')
                self.assertEqual(app.query_one(DataTable).row_count, 2)
                app.query_one(TextArea).load_text('SELECT label FROM items')
                await pilot.press('ctrl+r')
                await pilot.pause()
                self.assertEqual(app.query_one(DataTable).row_count, 1)
                app.query_one(TextArea).load_text('SELECT * FROM missing')
                await pilot.click('#run')
                self.assertIn('Query error', str(app.query_one('#status').render()))

if __name__ == '__main__':
    unittest.main()
