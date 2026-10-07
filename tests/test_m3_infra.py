import importlib
import json
import stat
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
