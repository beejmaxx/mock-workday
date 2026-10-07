import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def aws(*args, region="us-east-2"):
    result = subprocess.run(
        [
            "aws",
            "--profile",
            os.getenv("AWS_PROFILE", "agent-runtime"),
            "--region",
            region,
            "--output",
            "json",
            "--no-cli-pager",
            *args,
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def deployment():
    return json.loads(
        subprocess.check_output(
            [
                "terraform",
                f"-chdir={ROOT / 'infra/envs/dev/service'}",
                "output",
                "-json",
                "deployment",
            ],
            text=True,
        )
    )
