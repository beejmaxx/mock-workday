import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def script(tmp_path):
    root = tmp_path / "repo"
    scripts = root / "infra/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "infra/scripts/aws.sh"
    shutil.copy(source, scripts / "aws.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Every external command is intercepted; these tests cannot reach AWS/network.
    stub = (
        f"#!{sys.executable}\n"
        + r"""
import json, os, subprocess, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as out:
    out.write(json.dumps([name, *args]) + '\n')
if name == 'aws' and args[:2] == ['sts', 'get-caller-identity']:
    print(os.environ.get('TEST_ACCOUNT', '729608197929'))
elif name == 'curl':
    print(os.environ.get('TEST_IP', '203.0.113.7'))
elif name == 'git':
    print('synthetic-tag')
elif name == 'terraform' and 'output' in args:
    print('example.invalid/mock-workday')
elif name == 'python3' and args[0] in ('-', '-c'):
    raise SystemExit(subprocess.call([sys.executable, *args]))
elif name == 'docker' and args[0] == 'login':
    sys.stdin.read()
"""
    )
    for command in ("aws", "curl", "git", "terraform", "docker", "uv", "python3"):
        path = bin_dir / command
        path.write_text(stub)
        path.chmod(0o700)
    calls = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", CALLS=str(calls))
    env.pop("MW_ALLOWED_CIDR", None)
    env.pop("MW_IMAGE_TAG", None)

    def run(action, *args, updates=None, stdin=""):
        result = subprocess.run(
            ["/bin/bash", str(scripts / "aws.sh"), action, *args],
            input=stdin,
            text=True,
            capture_output=True,
            env=env | (updates or {}),
            timeout=10,
        )
        recorded = (
            [json.loads(line) for line in calls.read_text().splitlines()]
            if calls.exists()
            else []
        )
        return result, recorded

    return root, run


@pytest.mark.parametrize("action", ["up", "down", "plan"])
@pytest.mark.parametrize("mode", ["flag", "env", "interactive"])
def test_T_D1_06_hands_off_approval(script, action, mode):
    root, run = script
    args = ("--yes",) if mode == "flag" else ()
    updates = {"MW_ALLOWED_CIDR": "203.0.113.7/32"} if mode == "env" else {}
    result, calls = run(
        action, *args, updates=updates, stdin="\n" if mode == "interactive" else ""
    )
    assert result.returncode == 0, result.stderr
    settings = json.loads((root / ".local/aws-dev.tfvars.json").read_text())
    assert settings["allowed_cidr"] == "203.0.113.7/32"
    mutations = [
        c
        for c in calls
        if c[0] == "terraform" and any(a in c for a in ("apply", "destroy"))
    ]
    assert len(mutations) == {"up": 2, "down": 1, "plan": 0}[action]
    if action == "plan":
        plans = [c for c in calls if c[0] == "terraform" and "plan" in c]
        assert len(plans) == 2
        assert all("-auto-approve" not in c for c in plans)
        assert all(("-input=false" in c) == (mode != "interactive") for c in plans)
    for call in mutations:
        assert ("-auto-approve" in call) == (mode != "interactive")
        assert ("-input=false" in call) == (mode != "interactive")


@pytest.mark.parametrize(
    "updates,args",
    [
        ({"MW_ALLOWED_CIDR": "0.0.0.0/0"}, ()),
        ({"MW_ALLOWED_CIDR": "203.0.113.8/32"}, ()),
        ({"MW_ALLOWED_CIDR": ""}, ()),
        ({"TEST_IP": "invalid"}, ("--yes",)),
        ({"TEST_ACCOUNT": "000000000000"}, ("--yes",)),
        ({}, ("--unknown",)),
    ],
)
def test_T_D1_06_hands_off_refusal(script, updates, args):
    root, run = script
    result, calls = run("up", *args, updates=updates)
    assert result.returncode != 0
    assert not any(c[0] in {"terraform", "docker", "uv"} for c in calls)
    assert not (root / ".local/aws-dev.tfvars.json").exists()


def test_T_D1_06_interactive_eof_refuses(script):
    _, run = script
    result, calls = run("up")
    assert result.returncode != 0
    assert not any(c[0] == "terraform" for c in calls)
