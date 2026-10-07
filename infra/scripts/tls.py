"""Checkpoint-4 owner enrollment. No private certificate values enter Terraform."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from common import ROOT, aws, deployment

DIRECTORY = ROOT / ".local/m3-tls"
VARS = ROOT / ".local/aws-dev.tfvars.json"


def private_file(path, data):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
    ) as file:
        file.write(data)


def enroll():
    DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    inventory = DIRECTORY / "inventory.json"
    if inventory.exists():
        arn = json.loads(inventory.read_text())["certificate_arn"]
        aws("acm", "describe-certificate", "--certificate-arn", arn)
    else:
        now = datetime.now(UTC)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Mock Workday disposable lab CA")]
        )
        ca = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=90))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    False, False, False, False, False, True, True, False, False
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        hosts = [
            s + ".mockworkday.internal"
            for s in ("acme", "globex", "northstar", "meridian", "cedar")
        ]
        leaf = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])])
            )
            .issuer_name(name)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        for filename, key in [("ca.key", ca_key), ("leaf.key", leaf_key)]:
            private_file(
                DIRECTORY / filename,
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            )
        for filename, cert in [("ca.pem", ca), ("leaf.pem", leaf)]:
            private_file(
                DIRECTORY / filename, cert.public_bytes(serialization.Encoding.PEM)
            )
        result = aws(
            "acm",
            "import-certificate",
            "--certificate",
            f"fileb://{DIRECTORY / 'leaf.pem'}",
            "--private-key",
            f"fileb://{DIRECTORY / 'leaf.key'}",
            "--certificate-chain",
            f"fileb://{DIRECTORY / 'ca.pem'}",
            "--tags",
            "Key=Project,Value=mock-workday",
            "Key=Environment,Value=dev",
            "Key=Stack,Value=service",
            "Key=ManagedBy,Value=owner-enrollment",
        )
        arn = result["CertificateArn"]
        private_file(
            inventory,
            json.dumps(
                {
                    "certificate_arn": arn,
                    "ca_sha256": hashlib.sha256(
                        ca.public_bytes(serialization.Encoding.DER)
                    ).hexdigest(),
                    "hosts": hosts,
                }
            ).encode(),
        )
    settings = json.loads(VARS.read_text())
    settings["private_certificate_arn"] = arn
    VARS.write_text(json.dumps(settings, indent=2) + "\n")
    print(
        "Imported private TLS certificate recorded. Share only ca.pem and inventory.json with the consumer."
    )


def store():
    values = {
        "certificate": (DIRECTORY / "leaf.pem").read_text(),
        "private_key": (DIRECTORY / "leaf.key").read_text(),
        "ca": (DIRECTORY / "ca.pem").read_text(),
    }
    temporary = DIRECTORY / "secret.json"
    private_file(temporary, json.dumps(values).encode())
    try:
        aws(
            "secretsmanager",
            "put-secret-value",
            "--secret-id",
            deployment()["tls_secret_arn"],
            "--secret-string",
            f"file://{temporary}",
        )
    finally:
        temporary.unlink(missing_ok=True)
    print(
        "TLS leaf stored in operational secret; key values were not printed or passed to Terraform."
    )


def aws_error_code(error):
    match = re.search(r"An error occurred \(([^)]+)\)", error.stderr or "")
    return match[1] if match else f"AWSCLIExit{error.returncode}"


def delete_certificate(arn):
    delays = (2, 4, 8, 16, 30)
    for attempt in range(len(delays) + 1):
        try:
            aws("acm", "delete-certificate", "--certificate-arn", arn)
            return
        except subprocess.CalledProcessError as error:
            code = aws_error_code(error)
            if code == "ResourceNotFoundException":
                return
            # Permissions and malformed requests cannot recover by waiting.
            if code in {
                "AccessDenied",
                "AccessDeniedException",
                "InvalidArnException",
                "ValidationException",
            } or code.startswith("AWSCLIExit"):
                raise SystemExit(f"ACM DeleteCertificate failed: {code}") from None
            try:
                certificate = aws(
                    "acm", "describe-certificate", "--certificate-arn", arn
                )["Certificate"]
            except subprocess.CalledProcessError as describe_error:
                describe_code = aws_error_code(describe_error)
                if describe_code == "ResourceNotFoundException":
                    return
                raise SystemExit(
                    f"ACM DescribeCertificate failed: {describe_code}; DeleteCertificate: {code}"
                ) from None
            if code != "ResourceInUseException" and certificate["InUseBy"]:
                raise SystemExit(
                    f"ACM DeleteCertificate failed: {code}; certificate still attached"
                ) from None
            if attempt == len(delays):
                raise SystemExit(
                    f"ACM DeleteCertificate failed after {attempt + 1} attempts: {code}; local TLS files retained"
                ) from None
            print(
                f"ACM DeleteCertificate: {code}; retry in {delays[attempt]}s",
                file=sys.stderr,
            )
            time.sleep(delays[attempt])


def cleanup():
    inventory = DIRECTORY / "inventory.json"
    if inventory.exists():
        delete_certificate(json.loads(inventory.read_text())["certificate_arn"])
    for name in (
        "inventory.json",
        "ca.key",
        "leaf.key",
        "ca.pem",
        "leaf.pem",
        "secret.json",
    ):
        (DIRECTORY / name).unlink(missing_ok=True)
    if VARS.exists():
        settings = json.loads(VARS.read_text())
        settings["private_certificate_arn"] = None
        VARS.write_text(json.dumps(settings, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["enroll", "store", "cleanup"])
    args = parser.parse_args()
    {"enroll": enroll, "store": store, "cleanup": cleanup}[args.action]()


if __name__ == "__main__":
    main()
