#!/usr/bin/env bash
# Delete the test cluster. Pass --all to also forget the generated tokens.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require minikube

minikube -p "$PROFILE" delete
if [[ "${1:-}" == --all ]]; then
  rm -rf "$GEN"
  echo "removed $GEN"
fi
