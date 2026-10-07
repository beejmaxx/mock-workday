import importlib
import json
import stat
import subprocess

import pytest
from pathlib import Path

from cryptography import x509


def test_T_M3_N_01_owner_tls_custody_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "infra/scripts")
    )
    tls = importlib.import_module("tls")
    directory = tmp_path / "tls"
    variables = tmp_path / "vars.json"
    variables.write_text("{}")
    monkeypatch.setattr(tls, "DIRECTORY", directory)
    monkeypatch.setattr(tls, "VARS", variables)
    calls = []
    stored = []

    def aws(*args):
        calls.append(args)
        assert not any("PRIVATE KEY" in a for a in args)
        if args[:2] == ("acm", "import-certificate"):
            assert str(directory / "ca.key") not in str(args)
            return {"CertificateArn": "synthetic-certificate-arn"}
        if args[:2] == ("secretsmanager", "put-secret-value"):
            stored.append(
                json.loads(Path(args[-1].removeprefix("file://")).read_text())
            )
        return {}

    monkeypatch.setattr(tls, "aws", aws)
    monkeypatch.setattr(
        tls, "deployment", lambda: {"tls_secret_arn": "synthetic-secret"}
    )
    tls.enroll()
    assert json.loads(variables.read_text()) == {
        "private_certificate_arn": "synthetic-certificate-arn"
    }
    for name in ["leaf.key", "ca.key", "leaf.pem", "ca.pem", "inventory.json"]:
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
    cert = x509.load_pem_x509_certificate((directory / "leaf.pem").read_bytes())
    ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
    cert.verify_directly_issued_by(ca)
    assert cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName) == [
        s + ".mockworkday.internal"
        for s in ["acme", "globex", "northstar", "meridian", "cedar"]
    ]
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days == 30
    assert (ca.not_valid_after_utc - ca.not_valid_before_utc).days == 90
    tls.enroll()
    assert sum(c[:2] == ("acm", "import-certificate") for c in calls) == 1
    tls.store()
    assert stored[0]["private_key"] == (directory / "leaf.key").read_text()
    assert (directory / "ca.key").read_text() not in json.dumps(stored)
    assert not (directory / "secret.json").exists()
    tls.cleanup()
    assert not list(directory.iterdir())
    assert json.loads(variables.read_text())["private_certificate_arn"] is None


@pytest.mark.parametrize(
    "code,in_use,succeeds,attempts",
    [
        ("ResourceInUseException", [], True, 2),
        ("ResourceInUseException", ["listener"], True, 2),
        ("InternalFailure", [], True, 2),
        ("InternalFailure", ["listener"], False, 1),
        ("AccessDeniedException", [], False, 1),
        ("ResourceInUseException", [], False, 6),
        ("ResourceNotFoundException", [], True, 1),
    ],
)
def test_T_M3_N_01_tls_cleanup_retry_and_refusal(
    tmp_path, monkeypatch, capsys, code, in_use, succeeds, attempts
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "infra/scripts")
    )
    tls = importlib.import_module("tls")
    directory = tmp_path / "tls"
    directory.mkdir()
    (directory / "inventory.json").write_text('{"certificate_arn":"cert"}')
    (directory / "leaf.key").write_text("private-sentinel")
    variables = tmp_path / "vars.json"
    variables.write_text('{"private_certificate_arn":"cert"}')
    monkeypatch.setattr(tls, "DIRECTORY", directory)
    monkeypatch.setattr(tls, "VARS", variables)
    deletes = []
    sleeps = []

    def aws(*args):
        if args[1] == "describe-certificate":
            return {"Certificate": {"InUseBy": in_use}}
        deletes.append(args)
        if (
            succeeds
            and len(deletes) == attempts
            and code != "ResourceNotFoundException"
        ):
            return {}
        raise subprocess.CalledProcessError(
            254,
            "aws",
            stderr=f"An error occurred ({code}) when calling DeleteCertificate: private-sentinel",
        )

    monkeypatch.setattr(tls, "aws", aws)
    monkeypatch.setattr(tls.time, "sleep", sleeps.append)
    if succeeds:
        tls.cleanup()
        assert not list(directory.iterdir())
        assert json.loads(variables.read_text())["private_certificate_arn"] is None
    else:
        with pytest.raises(SystemExit, match=code):
            tls.cleanup()
        assert (directory / "leaf.key").read_text() == "private-sentinel"
        assert (directory / "inventory.json").exists()
        assert json.loads(variables.read_text())["private_certificate_arn"] == "cert"
    assert len(deletes) == attempts
    assert sleeps == [2, 4, 8, 16, 30][: attempts - 1]
    output = capsys.readouterr().err
    assert "private-sentinel" not in output
    if sleeps:
        assert code in output
