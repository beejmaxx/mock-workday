from datetime import date

import pytest

from mock_workday.bulk_seed import TENANTS, load, manifest
from mock_workday.db import one, run
from mock_workday.ids import seed_id

pytestmark = pytest.mark.bulk


def test_T_M3_SEED_01_02_full_dataset_manifest_and_rerun(env):
    before = env.get(env.worker("Bob"), env.login("bob")).json()
    total_bytes = 0
    for slug, (workers, orgs, positions) in sorted(TENANTS.items()):
        expected = manifest(slug)
        assert expected == manifest(slug)
        assert expected["counts"]["workers"] == workers
        assert expected["counts"]["organizations"] == orgs
        assert expected["counts"]["positions"] == positions
        assert expected["counts"]["bp_events"] == workers * 3
        assert expected["counts"]["documents"] == workers * 4
        assert (
            sum(d["fixture"] == "injection" for d in expected["documents"])
            >= workers * 0.04
        )
        assert all(4096 <= d["bytes"] <= 16384 for d in expected["documents"])
        total_bytes += expected["document_bytes"]
        assert load(env.db, slug) == expected
        tid = seed_id(slug, "tenant", slug)
        with env.db.tenant_tx(tid) as conn:
            assert (
                one(conn, "SELECT count(*) AS n FROM workers WHERE tenant_id=:tid")["n"]
                == workers
            )
            assert (
                one(
                    conn,
                    "SELECT count(DISTINCT password_hash) AS n FROM accounts WHERE tenant_id=:tid",
                )["n"]
                == 1
            )
            assert (
                one(
                    conn,
                    "SELECT sum(octet_length(content)) AS n FROM documents WHERE tenant_id=:tid",
                )["n"]
                == expected["document_bytes"]
            )
            for day in (date(2024, 1, 1), date(2025, 1, 1), date(2026, 1, 1)):
                assert (
                    one(
                        conn,
                        """SELECT count(DISTINCT position_id) AS n FROM job_revisions
                    WHERE tenant_id=:tid AND effective_date=:day""",
                        day=day,
                    )["n"]
                    == workers
                )
            assert (
                one(
                    conn,
                    """SELECT count(*) AS n FROM time_off_balances b WHERE tenant_id=:tid
                AND as_of='2026-10-07' AND taken_hours <> (SELECT count(*)*8 FROM bp_events e
                WHERE e.tenant_id=b.tenant_id AND e.subject_worker_id=b.worker_id
                AND e.type='REQUEST_TIME_OFF' AND e.status='SUCCESSFULLY_COMPLETED')""",
                )["n"]
                == 0
            )
            assert (
                one(
                    conn, "SELECT count(*) AS n FROM audit_objects WHERE tenant_id=:tid"
                )["n"]
                == 0
            )
        with env.db.tenant_tx(tid) as conn:
            assert (
                one(
                    conn, "SELECT count(*) AS n FROM event_outbox WHERE tenant_id=:tid"
                )["n"]
                == 0
            )
        # A completed reload verifies data instead of overwriting it.
        assert load(env.db, slug) == expected
    assert 139 * 1024**2 < total_bytes < 141 * 1024**2
    assert env.get(env.worker("Bob"), env.login("bob")).json() == before
    response = env.client.post(
        "/oauth2/token",
        headers={"Host": "northstar.mockworkday.local"},
        data={
            "grant_type": "password",
            "username": "worker-00001",
            "password": "pw-bulk-northstar",
        },
    )
    assert response.status_code == 200, response.text
    headers = {
        "Host": "northstar.mockworkday.local",
        **env.headers(response.json()["access_token"]),
    }
    page = env.client.get("/api/v1/workers?limit=2", headers=headers)
    assert page.status_code == 200 and page.json()["next_cursor"]
    page2 = env.client.get(
        "/api/v1/workers",
        params={"limit": 2, "cursor": page.json()["next_cursor"]},
        headers=headers,
    )
    assert page2.status_code == 200 and page2.json()["data"] != page.json()["data"]
    exported = env.client.post(
        "/api/v1/report-exports", headers=headers, json={"report": "worker-roster"}
    )
    assert exported.status_code == 201, exported.text
    assert exported.json()["row_count"] == TENANTS["northstar"][0]
    assert exported.json()["byte_length"] <= 16 * 1024**2
    download = env.client.get(
        exported.json()["download_url"], headers={"Host": "northstar.mockworkday.local"}
    )
    assert download.status_code == 200
    assert len(download.content.splitlines()) == TENANTS["northstar"][0]
    cross = env.client.get(env.worker("Bob"), headers=headers)
    assert cross.status_code == 404
    with env.db.owner.connect() as conn:
        size = one(conn, "SELECT pg_database_size(current_database()) AS n")["n"]
        print(f"bulk-v1: document bytes={total_bytes}; database bytes={size}")
        assert size < 0.7 * 1024**3


def test_T_M3_SEED_02_partial_import_resume_and_modified_data(env, monkeypatch):
    from mock_workday import bulk_seed

    original = bulk_seed.records

    def interrupted(slug):
        for index, item in enumerate(original(slug)):
            if index == 900:
                raise RuntimeError("interrupted import")
            yield item

    # Keep manifest generation intact; fail the import after committed batches.
    expected = manifest("northstar")
    monkeypatch.setattr(bulk_seed, "manifest", lambda slug: expected)
    monkeypatch.setattr(bulk_seed, "records", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        load(env.db, "northstar")
    response = env.client.get(
        "/api/v1/workers", headers={"Host": "northstar.mockworkday.local"}
    )
    assert response.status_code == 404
    monkeypatch.setattr(bulk_seed, "records", original)
    load(env.db, "northstar")
    tid = seed_id("northstar", "tenant", "northstar")
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(
            conn,
            "UPDATE workers SET name='Modified' WHERE tenant_id=:tid AND employee_id='E00001'",
        )
    with pytest.raises(ValueError, match="Modified seed row"):
        load(env.db, "northstar")
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(
                conn,
                "SELECT name FROM workers WHERE tenant_id=:tid AND employee_id='E00001'",
            )["name"]
            == "Modified"
        )


def test_T_M3_SEED_02_s3_partial_upload_resume(env, monkeypatch):
    import io

    from botocore.exceptions import ClientError

    from mock_workday import bulk_seed
    from mock_workday.errors import APIError
    from mock_workday.storage import Storage

    monkeypatch.setitem(bulk_seed.TENANTS, "northstar", (8, 3, 12))
    tid = seed_id("northstar", "tenant", "northstar")
    storage = Storage({str(tid): {"bucket": "bucket", "kms_key_id": "key"}}, "role")
    objects = {}

    class S3:
        fail = True

        def put_object(self, **kwargs):
            key = kwargs["Key"]
            if self.fail and len(objects) == 3:
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
            if key in objects:
                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed"}}, "PutObject"
                )
            objects[key] = kwargs["Body"]
            return {}

        def get_object(self, **kwargs):
            return {"Body": io.BytesIO(objects[kwargs["Key"]])}

    s3 = S3()
    monkeypatch.setattr(storage, "client", lambda tenant: s3)
    with pytest.raises(APIError):
        load(env.db, "northstar", storage)
    with env.db.tenant_tx(tid, owner=True) as conn:
        assert not one(conn, "SELECT enabled FROM tenants WHERE id=:tid")["enabled"]
        assert (
            one(conn, "SELECT count(*) AS n FROM documents WHERE tenant_id=:tid")["n"]
            == 0
        )
    assert len(objects) == 3
    s3.fail = False
    result = load(env.db, "northstar", storage)
    assert len(objects) == result["counts"]["documents"] == 32
    with env.db.tenant_tx(tid, owner=True) as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM documents WHERE tenant_id=:tid AND content IS NULL",
            )["n"]
            == 32
        )
        assert one(conn, "SELECT complete FROM seed_loads WHERE tenant_id=:tid")[
            "complete"
        ]


def test_T_M3_SEED_01_realistic_architecture_and_documents():
    import hashlib
    import random
    from collections import Counter

    from mock_workday.bulk_data import FAMILIES, FIRST, INDUSTRIES, LAST, architecture
    from mock_workday.bulk_seed import records

    assert len(FIRST) == len(set(FIRST)) == 200
    assert len(LAST) == len(set(LAST)) == 200
    for slug, sizes in TENANTS.items():
        rng = random.Random(
            int.from_bytes(hashlib.sha256(f"20261008:{slug}".encode()).digest(), "big")
        )
        names, orgs, positions, history, salaries = architecture(slug, *sizes, rng)
        assert len(names) == len(set(names)) == sizes[0]
        assert all(not any(c.isdigit() for c in name) for name in names)
        assert {o["name"] for o in orgs} >= set(FAMILIES)
        assert 3 <= max(o["depth"] for o in orgs) <= 4
        ics = [p for p in positions[: sizes[0]] if not p["manager"]]
        levels = Counter(p["level"] for p in ics)
        assert (levels[0] + levels[1]) / len(ics) > 0.5
        assert (levels[3] + levels[4]) / len(ics) < 0.2
        assert sum(p["manager"] for p in positions[: sizes[0]]) / sizes[0] < 0.1
        promotions = 0
        for i, revisions in enumerate(history):
            assert len({positions[p]["family"] for p in revisions}) == 1
            assert salaries[i][0] < salaries[i][1] < salaries[i][2]
            assert 45000 <= salaries[i][0] <= salaries[i][-1] <= 280000
            if revisions[0] != revisions[2]:
                promotions += 1
                assert (
                    positions[revisions[0]]["level"] + 1
                    == positions[revisions[2]]["level"]
                )
                assert salaries[i][2] >= salaries[i][1] * 1.09
        assert promotions > sizes[0] * 0.04
        for n, p in enumerate(positions[: sizes[0]]):
            if p["manager"] and n:
                assert p["org"] == orgs[n]["parent"]
                assert orgs[n]["name"] in p["title"]
                assert p["title"].startswith(
                    "VP"
                    if orgs[n]["depth"] == 1
                    else "Director"
                    if orgs[n]["depth"] == 2
                    else ("Manager", "Senior Manager")
                )
        samples = [r["content"] for table, r in records(slug) if table == "documents"][
            :80
        ]
        assert len(set(samples)) == 80
        assert all(
            any(location in doc for location in INDUSTRIES[slug]["locations"])
            for doc in samples
        )
        # Shared vocabulary is fine; repeated filler paragraphs are not the bulk of a document.
        assert (
            len(
                {
                    paragraph
                    for doc in samples
                    for paragraph in doc.split("\n")
                    if len(paragraph) > 100
                }
            )
            > 500
        )
        assert "Untrusted fixture:" in samples[0] and "Control:" in samples[1]
