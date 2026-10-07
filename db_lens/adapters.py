"""Optional remote adapters. Import drivers only when their engine is selected."""
import shlex
import time
from urllib.parse import urlsplit, unquote, parse_qs
from .database import Result, SQLiteDatabase


def validate_select(sql, dialect):
    import sqlglot
    from sqlglot import exp
    statements = sqlglot.parse(sql, read=dialect)
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError('Only a single SELECT query is supported.')
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                 exp.Command, exp.Into, exp.Lock, exp.Transaction)
    if any(isinstance(node, forbidden) for node in statements[0].walk()):
        raise ValueError('Writes, locks, and transaction commands are disabled.')


def url_options(url, scheme):
    parts = urlsplit(url)
    if parts.scheme != scheme or not parts.hostname or not parts.path.strip('/'):
        raise ValueError(f'Expected {scheme}://user:password@host:port/database')
    return parts, {k: values[-1] for k, values in parse_qs(parts.query).items()}


class SQLDatabase:
    kind = 'sql'
    limit = 500

    def _execute(self, sql, params=None):
        try:
            with self.connection.cursor() as cursor:
                if self.dialect == 'mysql':
                    cursor.execute('START TRANSACTION READ ONLY')
                cursor.execute(sql, params)
                rows = cursor.fetchmany(self.limit + 1)
                return Result([col[0] for col in cursor.description or []],
                              list(rows[:self.limit]), len(rows) > self.limit)
        finally:
            self.connection.rollback()

    def query(self, sql):
        validate_select(sql, self.dialect)
        import sqlglot
        sql = sqlglot.parse_one(sql, read=self.dialect).sql(dialect=self.dialect)
        hint = ' /*+ MAX_EXECUTION_TIME(3000) */' if self.dialect == 'mysql' else ''
        return self._execute(f'SELECT{hint} * FROM ({sql}\n) AS db_lens_preview LIMIT 501')

    def tables(self):
        if self.dialect == 'postgres':
            result = self._execute("SELECT table_schema, table_name FROM information_schema.tables "
                                   "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                                   "ORDER BY table_schema, table_name LIMIT 501")
        else:
            result = self._execute('SELECT table_schema, table_name FROM information_schema.tables '
                                   'WHERE table_schema = DATABASE() ORDER BY table_name LIMIT 501')
        # Preserve schema/table boundaries even when identifiers contain dots.
        self.objects = {self.qualified(row): tuple(row) for row in result.rows}
        self.catalog_truncated = result.truncated
        return list(self.objects)

    def qualified(self, names):
        quote = '"' if self.dialect == 'postgres' else '`'
        return '.'.join(quote + name.replace(quote, quote * 2) + quote for name in names)

    def browse(self, name):
        return self.query(f'SELECT * FROM {self.qualified(self.objects[name])} LIMIT 501')

    def schema(self, name):
        schema, table = self.objects[name]
        return self._execute('SELECT column_name, data_type, is_nullable, column_default '
                             'FROM information_schema.columns WHERE table_schema = %s '
                             'AND table_name = %s ORDER BY ordinal_position', (schema, table))

    def close(self):
        self.connection.close()


class PostgreSQLDatabase(SQLDatabase):
    dialect = 'postgres'

    def __init__(self, url):
        import psycopg
        self.path = 'PostgreSQL connection'
        self.connection = psycopg.connect(url, connect_timeout=5,
                                         options='-c statement_timeout=3000 -c default_transaction_read_only=on')
        self.connection.read_only = True


class MySQLDatabase(SQLDatabase):
    dialect = 'mysql'

    def __init__(self, url):
        import pymysql
        parts, options = url_options(url, 'mysql')
        unknown = set(options) - {'ssl_ca', 'ssl_cert', 'ssl_key'}
        if unknown:
            raise ValueError('Unsupported MySQL connection option.')
        self.path = 'MySQL connection'
        ssl = None
        if options:
            if not options.get('ssl_ca'):
                raise ValueError('TLS options require ssl_ca for certificate verification.')
            ssl = {'ca': options['ssl_ca'], 'check_hostname': True}
            for key in ('cert', 'key'):
                if options.get('ssl_' + key):
                    ssl[key] = options['ssl_' + key]
        self.connection = pymysql.connect(host=parts.hostname, port=parts.port or 3306,
            user=unquote(parts.username or ''), password=unquote(parts.password or ''),
            database=unquote(parts.path[1:]), charset='utf8mb4', connect_timeout=5,
            read_timeout=5, write_timeout=5, autocommit=False, ssl=ssl,
            cursorclass=pymysql.cursors.SSCursor)


def display_bytes(value):
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return '0x' + value[:1000].hex()
    return value


class RedisDatabase:
    kind = 'redis'
    limit = 500

    def __init__(self, url):
        import redis
        self.path = 'Redis connection'
        self.client = redis.Redis.from_url(url, decode_responses=False,
            socket_connect_timeout=5, socket_timeout=5)
        try:
            self.client.ping()
        except Exception:
            self.client.close()
            raise

    def scan(self, pattern='*'):
        cursor, seen, keys = 0, set(), []
        deadline = time.monotonic() + 3
        # SCAN may return duplicates or an empty batch with a nonzero cursor.
        for _ in range(100):
            cursor, batch = self.client.scan(cursor=cursor, match=pattern, count=100)
            for key in batch:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
            if cursor == 0 or len(keys) >= self.limit or time.monotonic() > deadline:
                break
        self.catalog_truncated = cursor != 0 or len(keys) > self.limit
        self.keys = keys[:self.limit]
        return self.keys

    def tables(self):
        return self.scan()

    def schema(self, key):
        return Result(['key', 'type', 'TTL seconds'],
                      [(display_bytes(key), display_bytes(self.client.type(key)), self.client.ttl(key))])

    def browse(self, key):
        kind = display_bytes(self.client.type(key))
        cap = self.limit
        if kind == 'none':
            return Result(['message'], [('Key expired or was deleted.',)])
        if kind == 'string':
            size = self.client.strlen(key)
            return Result(['value'], [(display_bytes(self.client.getrange(key, 0, 1999)),)], size > 2000)
        if kind == 'list':
            values = self.client.lrange(key, 0, cap)
            return Result(['index', 'value'], [(i, display_bytes(v)) for i, v in enumerate(values[:cap])], len(values) > cap)
        if kind == 'zset':
            values = self.client.zrange(key, 0, cap, withscores=True)
            return Result(['member', 'score'], [(display_bytes(v), s) for v, s in values[:cap]], len(values) > cap)
        if kind == 'stream':
            values = self.client.xrange(key, count=cap + 1)
            return Result(['entry ID', 'fields'], [(display_bytes(k), repr({display_bytes(f): display_bytes(v) for f, v in fields.items()})) for k, fields in values[:cap]], len(values) > cap)
        if kind in ('hash', 'set'):
            cursor, seen, rows = 0, set(), []
            deadline = time.monotonic() + 3
            for _ in range(100):
                if kind == 'hash':
                    cursor, batch = self.client.hscan(key, cursor=cursor, count=100)
                    entries = batch.items()
                else:
                    cursor, batch = self.client.sscan(key, cursor=cursor, count=100)
                    entries = ((v,) for v in batch)
                for item in entries:
                    identity = item[0]
                    if identity not in seen:
                        seen.add(identity)
                        rows.append(tuple(display_bytes(v) for v in item))
                if cursor == 0 or len(rows) > cap or time.monotonic() > deadline:
                    break
            return Result(['field', 'value'] if kind == 'hash' else ['member'], rows[:cap], cursor != 0 or len(rows) > cap)
        return Result(['message'], [(f'Value inspection not supported for type {kind}',)])

    def query(self, command):
        args = shlex.split(command)
        if not args:
            raise ValueError('Enter SCAN pattern, GET key, TYPE key, or TTL key.')
        name = args[0].upper()
        if len(args) != 2:
            raise ValueError('Expected one argument. Quote keys or patterns containing spaces.')
        if name == 'SCAN':
            keys = self.scan(args[1])
            return Result(['key'], [(display_bytes(k),) for k in keys], self.catalog_truncated)
        if name == 'GET':
            return self.browse(args[1])
        if name == 'TYPE':
            return Result(['type'], [(display_bytes(self.client.type(args[1])),)])
        if name == 'TTL':
            return Result(['TTL seconds'], [(self.client.ttl(args[1]),)])
        raise ValueError('Supported read commands: SCAN, GET, TYPE, TTL.')

    def close(self):
        self.client.close()


def connect(target):
    if target.startswith(('postgresql://', 'postgres://')):
        return PostgreSQLDatabase(target)
    if target.startswith('mysql://'):
        return MySQLDatabase(target)
    if target.startswith(('redis://', 'rediss://')):
        return RedisDatabase(target)
    if '://' in target:
        raise ValueError('Unsupported connection scheme.')
    return SQLiteDatabase(target)
