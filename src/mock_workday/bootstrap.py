import psycopg
from psycopg import sql

from .config import ADMIN_URL, APP_URL, OWNER_URL
from .db import Database, one
from .seed import seed


def provision_roles(admin_url, owner_url, app_url):
    with psycopg.connect(
        **admin_url.translate_connect_args(username="user", database="dbname")
    ) as conn:
        for url in (owner_url, app_url):
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (url.username,)
            ).fetchone()
            if not exists:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS").format(
                        sql.Identifier(url.username)
                    )
                )
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(url.username), sql.Literal(url.password)
                )
            )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(owner_url.database),
                sql.Identifier(owner_url.username),
                sql.Identifier(app_url.username),
            )
        )
        # Avoid re-granting through an inherited grantor on subsequent runs.
        granted = conn.execute(
            "SELECT has_schema_privilege(%s, 'public', 'USAGE WITH GRANT OPTION') "
            "AND has_schema_privilege(%s, 'public', 'CREATE WITH GRANT OPTION')",
            (owner_url.username, owner_url.username),
        ).fetchone()[0]
        if not granted:
            conn.execute(
                sql.SQL(
                    "GRANT USAGE, CREATE ON SCHEMA public TO {} WITH GRANT OPTION"
                ).format(sql.Identifier(owner_url.username))
            )


def bootstrap(app_url=APP_URL, owner_url=OWNER_URL, admin_url=ADMIN_URL):
    if admin_url is not None:
        provision_roles(admin_url, owner_url, app_url)
    db = Database(app_url, owner_url)
    try:
        with db.owner.connect() as conn:
            installed = (
                one(conn, "SELECT to_regclass('public.tenants') AS name")["name"]
                is not None
            )
        if not installed:
            db.install()
            seed(db)
    finally:
        db.close()


def main():
    bootstrap()


if __name__ == "__main__":
    main()
