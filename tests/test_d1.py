import getpass
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from mock_workday.bootstrap import bootstrap, provision_roles
from mock_workday.config import database_url
from mock_workday.db import one, rows, run


def test_T_D1_01_database_environment(monkeypatch):
    monkeypatch.setenv("MW_DB_HOST", "example.local")
    monkeypatch.setenv("MW_DB_PORT", "5544")
    monkeypatch.setenv("MW_DB_NAME", "example")
    url = database_url("mw_app", "synthetic:@/#password")
    assert (url.host, url.port, url.database, url.username, url.password) == (
        "example.local",
        5544,
        "example",
        "mw_app",
        "synthetic:@/#password",
    )
    assert "synthetic" not in str(url)


@pytest.fixture
def master_database(database):
    admin = "mw_d1_admin_" + uuid4().hex[:12]
    name = "mw_d1_" + uuid4().hex[:12]
    super_url = database.owner.url.set(
        username=getpass.getuser(), password=None, database="postgres"
    )
    engine = create_engine(super_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        run(conn, f"CREATE ROLE {admin} LOGIN CREATEROLE NOSUPERUSER NOBYPASSRLS")
        run(conn, f"GRANT mw_owner, mw_app TO {admin} WITH ADMIN OPTION")
        run(conn, f"CREATE DATABASE {name} OWNER {admin}")
    try:
        yield super_url.set(username=admin, database=name), engine
    finally:
        with engine.connect() as conn:
            run(conn, f"DROP DATABASE {name} WITH (FORCE)")
            run(conn, f"DROP ROLE {admin}")
        engine.dispose()


def test_T_D1_02_master_bootstrap_idempotent(master_database):
    admin_url, _ = master_database
    owner_url = admin_url.set(username="mw_owner", password="synthetic-owner:@")
    app_url = admin_url.set(username="mw_app", password="synthetic-app:/")
    bootstrap(app_url, owner_url, admin_url)
    engine = create_engine(owner_url)
    try:
        with engine.connect() as conn:
            before = rows(conn, "SELECT id FROM signing_keys ORDER BY id")
            tenants = rows(conn, "SELECT id,slug FROM tenants ORDER BY slug")
            assert len(tenants) == 2
        bootstrap(app_url, owner_url, admin_url)
        with engine.connect() as conn:
            assert rows(conn, "SELECT id FROM signing_keys ORDER BY id") == before
            assert rows(conn, "SELECT id,slug FROM tenants ORDER BY slug") == tenants
            for role in ("mw_owner", "mw_app"):
                flags = one(
                    conn,
                    "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:role",
                    role=role,
                )
                assert not flags["rolsuper"] and not flags["rolbypassrls"]
    finally:
        engine.dispose()


def test_T_D1_03_create_missing_roles(master_database):
    admin_url, engine = master_database
    owner = "mw_d1_owner_" + uuid4().hex[:12]
    app = "mw_d1_app_" + uuid4().hex[:12]
    owner_url = admin_url.set(username=owner, password="synthetic-owner")
    app_url = admin_url.set(username=app, password="synthetic-app")
    try:
        provision_roles(admin_url, owner_url, app_url)
        provision_roles(
            admin_url, owner_url.set(password="new-synthetic-owner"), app_url
        )
        with engine.connect() as conn:
            for name in (owner, app):
                role = one(
                    conn,
                    "SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:name",
                    name=name,
                )
                assert (
                    role["rolcanlogin"]
                    and not role["rolsuper"]
                    and not role["rolbypassrls"]
                )
    finally:
        # Remove database-local grants before removing these disposable cluster roles.
        cleanup = create_engine(admin_url.set(username=getpass.getuser()))
        with cleanup.begin() as conn:
            for name in (owner, app):
                run(conn, f"DROP OWNED BY {name}")
        cleanup.dispose()
        with engine.connect() as conn:
            for name in (owner, app):
                run(conn, f"DROP ROLE IF EXISTS {name}")
