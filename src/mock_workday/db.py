from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text


def rows(conn, sql, **params):
    return list(run(conn, sql, **params).mappings())


def one(conn, sql, **params):
    return run(conn, sql, **params).mappings().first()


def run(conn, sql, **params):
    if ":tid" in sql and "tid" not in params:
        params["tid"] = conn.info["tenant_id"]
    return conn.execute(text(sql), params)


class Database:
    def __init__(self, app_url, owner_url=None):
        self.app = create_engine(app_url)
        self.owner = create_engine(owner_url) if owner_url is not None else None

    @contextmanager
    def tenant_tx(self, tenant_id, *, owner=False):
        engine = self.owner if owner else self.app
        with engine.begin() as conn:
            run(
                conn,
                "SELECT set_config('app.tenant_id', :tid, true)",
                tid=str(tenant_id),
            )
            conn.info["tenant_id"] = tenant_id
            try:
                yield conn
            finally:
                conn.info.pop("tenant_id", None)

    def install(self):
        with self.owner.begin() as conn:
            conn.exec_driver_sql(Path(__file__).with_name("schema.sql").read_text())

    def close(self):
        self.app.dispose()
        if self.owner is not None:
            self.owner.dispose()


def advisory_lock(conn, key):
    run(conn, "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))", key=key)
