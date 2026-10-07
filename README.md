# DB Lens

A lightweight Python terminal database browser, built with Textual.

## Run

Requires Python 3.10 or newer. Extract this folder, then from inside it:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m db_lens.app sample.db
```

On Windows, activate with `.venv\Scripts\activate` instead.

To open your own database:

```sh
python -m db_lens.app /path/to/database.sqlite
```

Or start without a path and enter one in the top bar. No database server is
needed for SQLite. PostgreSQL, MySQL, and Redis are also supported with optional drivers; see below.

## Controls

- Click a table/view, or navigate the sidebar with arrows and Enter.
- Browse shows its first 500 rows; use SQL WHERE/ORDER BY/LIMIT/OFFSET for subsets.
- Schema shows column names, declared types, nullability, defaults, and primary keys.
- Edit SQL in the bottom panel; Ctrl+R or Run SQL executes one statement.
- Ctrl+S shows schema; Ctrl+Q quits. Tab moves between controls.

Databases open in read-only mode. Writes and attach operations are blocked.
Queries have a three-second SQLite execution budget; results are capped at 500
rows. Cells display up to 2,000 characters; binary values show their byte count.
NULL is explicitly displayed. SQL errors appear in the status bar.

The query currently executes on the UI thread, so a slow query can pause the UI
until the execution budget expires. No paging, cell editing, exports, or saved credentials are included yet. Schema currently
covers columns; separate index and foreign-key panels are future work.

## Structure

- `db_lens/app.py`: terminal UI and event handlers.
- `db_lens/database.py`: database protocol, results model, SQLite adapter.
- `tests/test_app.py`: database safety/results checks and headless UI workflow.
- `sample.db`: fictional inventory data for trying the interface.

Remote adapters connect to existing servers; the TUI does not replace a database engine.

## Test

```sh
python -m pip install -e '.[all,test]'
python -m unittest discover -s tests -v
```

Tested with Textual 6.12.0 and Python 3.12.

## Version 0.2: PostgreSQL, MySQL, and Redis

Install all adapters (or select `postgres`, `mysql`, or `redis` instead of `all`):

```sh
python -m pip install -e '.[all]'
```

Connections accept these URL formats:

| Engine | Example |
| --- | --- |
| SQLite | `/path/to/database.sqlite` |
| PostgreSQL | `postgresql://user:password@localhost:5432/database?sslmode=require` |
| MySQL | `mysql://user:password@localhost:3306/database` |
| Redis | `redis://user:password@localhost:6379/0` |
| Redis TLS | `rediss://user:password@host:6379/0` |

URL-encode special characters in usernames/passwords. Remote connection URLs
are masked after opening and never saved. Prefer an environment variable over
passing a credential-bearing URL as a command-line argument:

```sh
read -r -s -p 'Connection URL: ' DB_LENS_URL; echo
export DB_LENS_URL
python -m db_lens.app --env DB_LENS_URL
unset DB_LENS_URL
```

For MySQL TLS, append `?ssl_ca=/path/to/ca.pem`; optional `ssl_cert` and `ssl_key`
are supported. PostgreSQL accepts libpq TLS options such as `sslmode=verify-full`
and `sslrootcert`. Remote engines require an existing server.

PostgreSQL/MySQL list schema-qualified tables/views and column definitions.
The remote SQL editor accepts a single SELECT, including SELECT-based CTEs and
set operations. Writes, SELECT INTO, transaction controls, and locking queries
are rejected. Each query uses a read-only transaction and rolls back afterward.
Use a database account restricted to read access: stored functions can have
side effects that SQL parsing and transaction settings cannot universally prevent.
PostgreSQL has a 3-second statement timeout; MySQL has 5-second socket read/write
timeouts (these do not cancel server execution). Catalogs and results show at
most 500 entries. Remote user queries are wrapped in an outer LIMIT 501 before execution,
so at most 501 rows are transported. MySQL also uses an unbuffered cursor
and a MAX_EXECUTION_TIME hint (support depends on the server/version). Schema panels do not yet expose remote indexes/keys.

Redis uses SCAN rather than KEYS, with duplicate removal and a bounded scan
budget. The sidebar may be a partial preview; use `SCAN prefix:*` to narrow it.
SCAN replaces the sidebar results; it does not provide a stable snapshot or
pagination. Select a key to inspect strings, hashes, lists, sets, sorted sets,
or streams. Metadata shows key type and TTL (-1: no expiry; -2: missing).
Text is decoded as UTF-8; non-text bytes appear as a hexadecimal preview.
String previews fetch up to 2,000 bytes. Collection previews show up to 500
entries, though individual collection members can be large. Module-specific
Redis types are identified but have no value inspector yet.

The Redis command panel accepts only `SCAN pattern`, `GET key` (type-aware
inspection), `TYPE key`, and `TTL key`. Quote arguments containing spaces.
No arbitrary Redis command execution is provided. Use a Redis ACL account with
only PING, SCAN, TYPE, TTL, STRLEN, GETRANGE, LRANGE, ZRANGE, XRANGE, HSCAN,
and SSCAN permissions. Redis Cluster and Sentinel discovery are not supported.

All connection/query work currently runs on the UI thread; a slow server can
pause interaction. Driver socket/statement timeouts limit most waits, but
PostgreSQL socket transport has no universal total deadline in this release.

Tests:

```sh
python -m pip install -e '.[all,test]'
python -m unittest discover -s tests -v
```

SQLite and headless UI tests run locally. PostgreSQL/MySQL adapter tests mock
the drivers; Redis tests use fakeredis. Live PostgreSQL/MySQL/Redis servers were
not available during validation, so deployment-specific authentication, TLS,
and server-version behavior still need live smoke testing.
