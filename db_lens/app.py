import argparse
import sqlite3
import os
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Static, TextArea, Tree

from .database import Database, Result
from .adapters import connect, display_bytes


class DBLens(App):
    TITLE = 'DB Lens'
    CSS = '''
    Screen { background: #101216; }
    #connection { height: 3; }
    Input { width: 1fr; }
    Button { min-width: 12; }
    #workspace { height: 1fr; }
    Tree { width: 26; background: #171a20; border-right: solid #343a44; }
    #main { padding: 0 1; }
    #heading { height: 2; color: #c9cdd4; }
    DataTable { height: 1fr; background: #101216; }
    TextArea { height: 8; border: solid #343a44; }
    #actions { height: 3; }
    #status { height: 2; padding: 0 1; color: #c9cdd4; }
    '''
    BINDINGS = [('ctrl+r', 'run_query', 'Run SQL'),
                ('ctrl+s', 'schema', 'Schema'), ('ctrl+q', 'quit', 'Quit')]

    def __init__(self, path: str | None = None):
        super().__init__()
        self.initial_path = path
        self.database: Database | None = None
        self.selected_table: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='connection'):
            yield Input(placeholder='SQLite path or PostgreSQL / MySQL / Redis URL', id='path')
            yield Button('Open', id='open')
        with Horizontal(id='workspace'):
            yield Tree('Tables & views', id='tables')
            with Vertical(id='main'):
                yield Static('Open a SQLite database to begin', id='heading', markup=False)
                yield DataTable(id='grid', zebra_stripes=True)
                yield TextArea('SELECT * FROM sqlite_schema;', id='sql')
                with Horizontal(id='actions'):
                    yield Button('Run', id='run')
                    yield Button('Schema', id='schema')
                    yield Button('Browse', id='browse')
        yield Static('Browse mode • up to 500 results', id='status', markup=False)
        yield Footer()

    def on_mount(self) -> None:
        if self.initial_path:
            self.query_one('#path', Input).value = self.initial_path
            self.open_database()

    def status(self, message: str) -> None:
        self.query_one('#status', Static).update(message)

    def open_database(self) -> None:
        candidate = None
        try:
            path = self.query_one('#path', Input).value.strip()
            self.query_one('#path', Input).password = '://' in path
            candidate = connect(path)
            tables = candidate.tables()
        except Exception as error:
            if candidate:
                candidate.close()
            self.status('Could not open connection. Check drivers, address, credentials, and permissions.' if '://' in path else f'Could not open database: {error}')
            return
        if self.database:
            self.database.close()
        self.database = candidate
        self.selected_table = None
        tree = self.query_one('#tables', Tree)
        tree.clear()
        tree.root.expand()
        for name in tables:
            tree.root.add_leaf(Text(str(display_bytes(name))), data=name)
        self.query_one('#grid', DataTable).clear(columns=True)
        self.query_one('#heading', Static).update(str(candidate.path))
        redis_mode = getattr(candidate, 'kind', 'sql') == 'redis'
        tree.root.set_label('Redis keys' if redis_mode else 'Tables & views')
        self.query_one('#sql', TextArea).load_text('SCAN *' if redis_mode else 'SELECT 1;')
        self.query_one('#schema', Button).label = 'Metadata' if redis_mode else 'Schema'
        self.query_one('#path', Input).password = '://' in path
        capped = ' • catalog capped; filter with SCAN or SQL' if getattr(candidate, 'catalog_truncated', False) else ''
        self.status(f'{len(tables)} objects loaded' + capped)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == 'path':
            event.input.password = '://' in event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.open_database()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {'open': self.open_database, 'run': self.action_run_query,
                   'schema': self.action_schema, 'browse': self.browse}
        actions[event.button.id]()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.selected_table = event.node.data
            self.browse()

    def display_result(self, result: Result, title: str) -> None:
        grid = self.query_one('#grid', DataTable)
        grid.clear(columns=True)
        grid.add_columns(*(Text(column) for column in result.columns))
        for row in result.rows:
            grid.add_row(*(Text('NULL' if value is None else
                               f'<{len(value)} bytes>' if isinstance(value, bytes)
                               else str(value)[:2000]) for value in row))
        self.query_one('#heading', Static).update(title)
        self.status(f'{len(result.rows)} rows shown' +
                    (' • partial preview: row, byte, or scan budget reached' if result.truncated else '') + ' • browse mode')

    def execute(self, sql: str, title: str) -> None:
        if not self.database:
            self.status('Open a database first.')
            return
        try:
            result = self.database.query(sql)
            self.display_result(result, title)
            if getattr(self.database, 'kind', 'sql') == 'redis' and sql.strip().upper().startswith('SCAN '):
                tree = self.query_one('#tables', Tree)
                tree.clear()
                tree.root.expand()
                self.selected_table = None
                for key in self.database.keys:
                    tree.root.add_leaf(Text(str(display_bytes(key))), data=key)
        except Exception as error:
            self.status(f'Query error: {error}')

    def browse(self) -> None:
        if self.database and self.selected_table is not None:
            try:
                self.display_result(self.database.browse(self.selected_table),
                                    str(display_bytes(self.selected_table)))
            except Exception as error:
                self.status(f'Browse error: {error}')
        else:
            self.status('Select a table, view, or key first.')

    def action_run_query(self) -> None:
        self.execute(self.query_one('#sql', TextArea).text, 'Query results')

    def action_schema(self) -> None:
        if self.database and self.selected_table is not None:
            try:
                self.display_result(self.database.schema(self.selected_table),
                                    f'Schema / metadata: {display_bytes(self.selected_table)}')
            except Exception as error:
                self.status(f'Schema error: {error}')
        else:
            self.status('Select a table or view first.')

    def on_unmount(self) -> None:
        if self.database:
            self.database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='SQLite, PostgreSQL, MySQL, and Redis terminal browser')
    parser.add_argument('database', nargs='?', help='SQLite file path; prefer --env for remote connection URLs')
    parser.add_argument('--env', metavar='VARIABLE', help='Read a connection URL from this environment variable')
    args = parser.parse_args()
    if args.env and args.database:
        parser.error('Use a path or --env, not both.')
    target = os.environ.get(args.env) if args.env else args.database
    if args.env and not target:
        parser.error('The requested environment variable is empty or unset.')
    DBLens(target).run()


if __name__ == '__main__':
    main()
