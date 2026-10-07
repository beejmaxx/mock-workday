#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-agent-runtime}"
export AWS_REGION=us-east-2 AWS_DEFAULT_REGION=us-east-2 AWS_PAGER=""
REGISTRY="$ROOT/infra/envs/dev/registry"
SERVICE="$ROOT/infra/envs/dev/service"
VARS="$ROOT/.local/aws-dev.tfvars.json"

check_account() {
  local account
  account="$(aws sts get-caller-identity --query Account --output text)"
  if [[ "$account" != 729608197929 ]]; then
    echo "Expected dev account 729608197929, got $account; stopping." >&2
    exit 1
  fi
}

configure() {
  mkdir -p "$ROOT/.local"
  local current_ip cidr tag
  current_ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com)"
  read -r -p "Allowed IPv4 /32 [${current_ip}/32]: " cidr
  cidr="${cidr:-${current_ip}/32}"
  tag="${MW_IMAGE_TAG:-$(git -C "$ROOT" rev-parse --short=12 HEAD)}"
  python3 - "$VARS" "$cidr" "$tag" <<'PY'
import ipaddress, json, sys
network = ipaddress.ip_network(sys.argv[2])
if network.version != 4 or network.prefixlen != 32:
    raise SystemExit('Only a single IPv4 /32 is allowed')
with open(sys.argv[1], 'w') as output:
    json.dump({'allowed_cidr': str(network), 'image_tag': sys.argv[3]}, output)
PY
}

initialize() {
  terraform -chdir="$1" init -input=false
}

case "${1:-}" in
  plan)
    check_account
    configure
    initialize "$REGISTRY"
    terraform -chdir="$REGISTRY" plan
    initialize "$SERVICE"
    terraform -chdir="$SERVICE" plan -var-file="$VARS"
    ;;
  up)
    check_account
    configure
    initialize "$REGISTRY"
    terraform -chdir="$REGISTRY" apply
    repository="$(terraform -chdir="$REGISTRY" output -raw repository_url)"
    tag="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image_tag"])' "$VARS")"
    aws ecr get-login-password | docker login --username AWS --password-stdin "${repository%%/*}"
    docker build --platform linux/arm64 -t "$repository:$tag" "$ROOT"
    docker push "$repository:$tag"
    initialize "$SERVICE"
    terraform -chdir="$SERVICE" apply -var-file="$VARS"
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
    terraform -chdir="$SERVICE" destroy -var-file="$VARS"
    python3 "$ROOT/infra/scripts/leftovers.py"
    ;;
  leftovers)
    check_account
    python3 "$ROOT/infra/scripts/leftovers.py"
    ;;
  *) echo "Usage: $0 {plan|up|smoke|down|leftovers}" >&2; exit 2 ;;
esac
