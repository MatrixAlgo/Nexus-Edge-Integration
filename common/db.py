"""SQLite helpers: WAL + synchronous=FULL (durable commit), one connection per thread, explicit transactions."""
import contextlib, sqlite3, threading


class Database:
    def __init__(self, path, schema_sql=None):
        self.path = path
        self._local = threading.local()
        if schema_sql:
            c = self.conn()
            c.executescript(schema_sql)

    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.c = c
        return c

    @contextlib.contextmanager
    def tx(self):
        """BEGIN IMMEDIATE ... COMMIT; rolls back on any exception. Commit is durable (synchronous=FULL)."""
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def q(self, sql, args=()):
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def q1(self, sql, args=()):
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def scalar(self, sql, args=()):
        r = self.conn().execute(sql, args).fetchone()
        return r[0] if r else None

    def close(self):
        c = getattr(self._local, "c", None)
        if c is not None:
            c.close()
            self._local.c = None
