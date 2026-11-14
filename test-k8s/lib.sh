# shellcheck shell=bash disable=SC2034
# Shared by up.sh, smoke.sh and down.sh. Sourced, not executed.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PROFILE="${CVESTREAM_TEST_PROFILE:-cvestream-test}"
# The namespaces of wixyvir/services: apps in `apps`, ingress in `traefik`.
NS=apps
TRAEFIK_NS=traefik
IMAGE=cvestream:test-k8s
# Pinned with the image (traefik:v3.6.15); identical to wixyvir/services
# k8s/infra/traefik/crds.yaml.
TRAEFIK_CRDS=https://raw.githubusercontent.com/traefik/traefik/v3.6.15/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml
GEN="$HERE/.generated"
ENDPOINTS="$GEN/endpoints.yaml"
CURL_IMAGE=curlimages/curl:8.11.1

# cvestream ignores proxy variables (trust_env=False): the pod needs direct
# egress whatever we do here. Dropping them also keeps minikube from baking a
# proxy into the node and kubectl from sending the API calls through it.
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy

# minikube's kubectl matches the cluster version (a distro kubectl can be many
# minors behind). It forks the real binary: start long-running calls with
# `setsid` and kill the process group, or the child outlives the wrapper.
k() { minikube -p "$PROFILE" kubectl -- --context "$PROFILE" "$@"; }
kctl() { k -n "$NS" "$@"; }

# Background kubectl in its own session; the session (= process group) ID goes
# to BG_PID. setsid forks when its caller already leads a group (job control
# on), so $! is not reliable: the new session leader writes its own PID.
# $1 is the namespace.
bg_kubectl() {
  local ns="$1" pidfile
  shift
  pidfile=$(mktemp)
  # shellcheck disable=SC2016 # $$ and $@ are the inner shell's
  setsid sh -c 'echo $$ >"$0"; exec "$@"' "$pidfile" \
    minikube -p "$PROFILE" kubectl -- --context "$PROFILE" -n "$ns" "$@" &
  local _
  for _ in $(seq 50); do [[ -s "$pidfile" ]] && break; sleep 0.1; done
  BG_PID=$(cat "$pidfile")
  rm -f "$pidfile"
}
bg_kctl() { bg_kubectl "$NS" "$@"; }
bg_kill() { [[ -n "${1:-}" ]] && kill -- "-$1" 2>/dev/null; return 0; }

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok() { printf '  \033[32mOK\033[0m   %s\n' "$*"; }
ko() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
FAILURES=0

require() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || die "$tool is required"
  done
}
