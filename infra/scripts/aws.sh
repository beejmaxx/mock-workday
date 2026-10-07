#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-agent-runtime}"
export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2 AWS_PAGER=""
REGISTRY="$ROOT/infra/envs/dev/registry"
SERVICE="$ROOT/infra/envs/dev/service"
VARS="$ROOT/.local/aws-dev.tfvars.json"

ACTION="${1:-}"
# A nonempty array also works with nounset in macOS Bash 3.2.
AUTO_APPROVE=(-input=true)
NONINTERACTIVE=false
if [[ "${2:-}" == --yes && $# == 2 ]]; then
  NONINTERACTIVE=true
elif [[ $# -gt 1 ]]; then
  echo "Usage: $0 {plan|up|smoke|down|leftovers} [--yes]" >&2
  exit 2
fi
if $NONINTERACTIVE; then AUTO_APPROVE=(-input=false -auto-approve); fi

check_account() {
  local account
  account="$(aws sts get-caller-identity --query Account --output text </dev/null)"
  if [[ "$account" != 729608197929 ]]; then
    echo "Expected dev account 729608197929, got $account; stopping." >&2
    exit 1
  fi
}

configure() {
  mkdir -p "$ROOT/.local"
  local current_ip cidr tag
  current_ip="$(curl --noproxy "*" -fsS --max-time 10 https://checkip.amazonaws.com)"
  if $NONINTERACTIVE || [[ "${MW_ALLOWED_CIDR+x}" == x ]]; then
    cidr="${current_ip}/32"
    if [[ "${MW_ALLOWED_CIDR+x}" == x && "$MW_ALLOWED_CIDR" != "$cidr" ]]; then
      echo "MW_ALLOWED_CIDR must match detected direct egress $cidr; refusing." >&2
      exit 1
    fi
  else
    read -r -p "Allowed IPv4 /32 [${current_ip}/32]: " cidr
    cidr="${cidr:-${current_ip}/32}"
  fi
  tag="${MW_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
  python3 - "$VARS" "$cidr" "$tag" <<'PY'
import ipaddress, json, sys
network = ipaddress.ip_network(sys.argv[2])
if network.version != 4 or network.prefixlen != 32:
    raise SystemExit('Only a single IPv4 /32 is allowed')
from pathlib import Path
path = Path(sys.argv[1])
settings = json.loads(path.read_text()) if path.exists() else {}
settings.update(allowed_cidr=str(network), image_tag=sys.argv[3])
settings.setdefault('public_domain', None)
with path.open('w') as output:
    json.dump(settings, output)
PY
}

initialize() {
  terraform -chdir="$1" init -input=false
}

case "$ACTION" in
  plan)
    umask 077
    check_account
    configure
    initialize "$REGISTRY"
    terraform -chdir="$REGISTRY" fmt -check
    terraform -chdir="$REGISTRY" validate
    terraform -chdir="$REGISTRY" plan "${AUTO_APPROVE[0]}" -out="$ROOT/.local/m3-registry.tfplan"
    initialize "$SERVICE"
    terraform -chdir="$SERVICE" fmt -check
    terraform -chdir="$SERVICE" validate
    terraform -chdir="$SERVICE" plan "${AUTO_APPROVE[0]}" -var-file="$VARS" -out="$ROOT/.local/m3-service.tfplan"
    ;;
  up)
    check_account
    configure
    initialize "$REGISTRY"
    terraform -chdir="$REGISTRY" apply "${AUTO_APPROVE[@]}"
    repository="$(terraform -chdir="$REGISTRY" output -raw repository_url)"
    tag="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image_tag"])' "$VARS")"
    # Preserve piped approval answers; docker login reads only the ECR password pipe.
    aws ecr get-login-password </dev/null | docker login --username AWS --password-stdin "${repository%%/*}"
    docker build --platform linux/arm64 -t "$repository:$tag" "$ROOT" </dev/null
    docker push "$repository:$tag" </dev/null
    initialize "$SERVICE"
    uv run --project "$ROOT" --frozen python "$ROOT/infra/scripts/tls.py" enroll
    terraform -chdir="$SERVICE" apply "${AUTO_APPROVE[@]}" -var-file="$VARS"
    uv run --project "$ROOT" --frozen python "$ROOT/infra/scripts/tls.py" store
    python3 "$ROOT/infra/scripts/migrate.py"
    ;;
  smoke)
    check_account
    python3 "$ROOT/infra/scripts/smoke.py"
    ;;
  down)
    check_account
    [[ -f "$VARS" ]] || configure
    initialize "$SERVICE"
    python3 "$ROOT/infra/scripts/prepare_down.py"
    terraform -chdir="$SERVICE" destroy "${AUTO_APPROVE[@]}" -var-file="$VARS"
    uv run --project "$ROOT" --frozen python "$ROOT/infra/scripts/tls.py" cleanup
    python3 "$ROOT/infra/scripts/leftovers.py"
    ;;
  leftovers)
    check_account
    python3 "$ROOT/infra/scripts/leftovers.py"
    ;;
  *) echo "Usage: $0 {plan|up|smoke|down|leftovers} [--yes]" >&2; exit 2 ;;
esac
