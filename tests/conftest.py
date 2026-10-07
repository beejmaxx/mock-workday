import getpass
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from mock_workday.app import create_apps
from mock_workday.db import Database
from mock_workday.ids import seed_id
from mock_workday.seed import seed


def pg_binary(name):
    brew = Path("/opt/homebrew/opt/postgresql@17/bin") / name
    path = shutil.which(name) or (str(brew) if brew.exists() else None)
    if not path:
        pytest.fail(
            f"{name} is required (PostgreSQL 16+). Install PostgreSQL and add its bin directory to PATH."
        )
    return path


@pytest.fixture(scope="session")
def database(tmp_path_factory):
    root = tmp_path_factory.mktemp("postgres")
    data = root / "data"
    subprocess.run(
        [
            pg_binary("initdb"),
            "-D",
            str(data),
            "--auth=trust",
            "--no-locale",
            "-E",
            "UTF8",
        ],
        check=True,
        capture_output=True,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    subprocess.run(
        [
            pg_binary("pg_ctl"),
            "-D",
            str(data),
            "-l",
            str(root / "postgres.log"),
            "-o",
            f"-p {port} -h 127.0.0.1 -k ''",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    db = None
    try:
        bootstrap = create_engine(
            f"postgresql+psycopg://{getpass.getuser()}@127.0.0.1:{port}/postgres",
            isolation_level="AUTOCOMMIT",
        )
        with bootstrap.connect() as conn:
            conn.exec_driver_sql("CREATE ROLE mw_owner LOGIN NOSUPERUSER NOBYPASSRLS")
            conn.exec_driver_sql("CREATE ROLE mw_app LOGIN NOSUPERUSER NOBYPASSRLS")
            conn.exec_driver_sql("CREATE DATABASE mock_workday OWNER mw_owner")
        bootstrap.dispose()
        db = Database(
            f"postgresql+psycopg://mw_app@127.0.0.1:{port}/mock_workday",
            f"postgresql+psycopg://mw_owner@127.0.0.1:{port}/mock_workday",
        )
        db.install()
        yield db
    finally:
        if db:
            db.close()
        subprocess.run(
            [pg_binary("pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
            check=True,
            capture_output=True,
        )


@pytest.fixture(autouse=True)
def local_storage(monkeypatch):
    # A developer's deployment environment must never turn pytest into live AWS work.
    monkeypatch.delenv("MW_TENANT_STORAGE", raising=False)
    monkeypatch.delenv("MW_TENANT_DATA_ROLE_ARN", raising=False)


@pytest.fixture
def env(database):
    seed(database)
    public, admin = create_apps(database, test_admin=True)
    with (
        TestClient(public, base_url="http://acme.mockworkday.local") as client,
        TestClient(admin) as private,
    ):
        yield Lab(database, client, private, public.state.service)


class Lab:
    def __init__(self, db, client, admin, service):
        self.db, self.client, self.admin, self.service = db, client, admin, service

    def id(self, kind, ref, slug="acme"):
        return seed_id(slug, kind, ref).hex

    def login(self, user="alice", slug="acme"):
        response = self.client.post(
            "/oauth2/token",
            data={"grant_type": "password", "username": user, "password": "pw-" + user},
            headers={"Host": slug + ".mockworkday.local"},
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def isu(self, client="directory-sync"):
        response = self.client.post(
            "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client,
                "client_secret": "secret-" + client,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def headers(self, token):
        return {"Authorization": "Bearer " + token}

    def get(self, path, token, **kwargs):
        return self.client.get(path, headers=self.headers(token), **kwargs)

    def worker(self, name, suffix=""):
        return "/api/v1/workers/" + self.id("workers", name) + suffix

    def grant(self, user="carol", client="hr-assistant", scopes=None, ttl=3600):
        token = self.login(user)
        response = self.client.post(
            "/api/v1/delegation-grants",
            headers=self.headers(token),
            json={
                "client_id": client,
                "scopes": scopes
                if scopes is not None
                else ["staffing", "compensation", "documents"],
                "ttl_seconds": ttl,
            },
        )
        assert response.status_code == 201, response.text
        return token, response.json()

    def exchange(self, grant, client="hr-assistant"):
        return self.client.post(
            "/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": client,
                "client_secret": "secret-" + client,
                "grant_id": grant["id"],
            },
        )

    def delegated(self, user="carol", client="hr-assistant", scopes=None):
        _, grant = self.grant(user, client, scopes)
        response = self.exchange(grant, client)
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def advance(self, seconds):
        response = self.admin.post("/admin/clock", json={"advance_seconds": seconds})
        assert response.status_code == 200, response.text

    def revoke_role(self, role, org, pos):
        response = self.admin.post(
            "/admin/role-assignments/"
            + self.id("role_assignments", f"{role}:{org}:{pos}")
            + "/revoke",
            json={"slug": "acme"},
        )
        assert response.status_code == 200, response.text
