#!/usr/bin/env bash
# Bootstrap (or refresh) a throwaway minikube cluster running cvestream built
# from the current working tree, then run smoke.sh. Idempotent: run it again
# after a code change to rebuild, reload and restart.
#
# Environment:
#   CVESTREAM_TEST_PROFILE    minikube profile (default cvestream-test)
#   CVESTREAM_TEST_ENDPOINTS  your own endpoints file instead of generated tokens
#   SKIP_SMOKE=1              stop after the rollout
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require minikube docker python3 setsid

step "minikube profile $PROFILE"
if minikube -p "$PROFILE" status >/dev/null 2>&1; then
  ok "already running"
else
  # Calico: the default CNI does not enforce NetworkPolicy.
  minikube start -p "$PROFILE" --driver=docker --cni=calico --cpus=2 --memory=3g
fi

step "build $IMAGE from $REPO"
docker build --target runtime -t "$IMAGE" "$REPO"
minikube -p "$PROFILE" image load --overwrite=true "$IMAGE"
ok "image loaded into the node"

step "endpoints file"
mkdir -p "$GEN"
chmod 700 "$GEN"
if [[ -n "${CVESTREAM_TEST_ENDPOINTS:-}" ]]; then
  install -m 600 "$CVESTREAM_TEST_ENDPOINTS" "$ENDPOINTS"
  ok "copied $CVESTREAM_TEST_ENDPOINTS"
elif [[ -f "$ENDPOINTS" ]]; then
  ok "kept $ENDPOINTS (delete it to regenerate the tokens)"
else
  # endpoints.example.yaml with one random token per REPLACE-ME placeholder.
  python3 -I -c '
import re, secrets, sys
text = open(sys.argv[1]).read()
sys.stdout.write(re.sub(r"REPLACE-ME-[A-Za-z0-9_-]+", lambda _: secrets.token_urlsafe(32), text))
' "$REPO/endpoints.example.yaml" >"$ENDPOINTS"
  chmod 600 "$ENDPOINTS"
  ok "generated $ENDPOINTS with fresh tokens"
fi
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$GEN:/config:ro" "$IMAGE" config validate /config/endpoints.yaml
ok "endpoints file validates against the image"

step "apply"
existed=false
kctl get deployment cvestream >/dev/null 2>&1 && existed=true
# Server-side: the CRDs are too large for the last-applied annotation.
k apply --server-side -f "$TRAEFIK_CRDS"
k wait --for=condition=Established --timeout=60s crd/ingressroutes.traefik.io
k apply -f "$HERE/manifests/namespaces.yaml"
kctl create secret generic cvestream-endpoints \
  --from-file=endpoints.yaml="$ENDPOINTS" --dry-run=client -o yaml | kctl apply -f -
k apply -k "$HERE/manifests"
# Same tag, new image: a restart is the only way to pick it up.
if $existed; then kctl rollout restart deployment/cvestream; fi
kctl rollout status deployment/cvestream --timeout=180s
for deployment in traefik traefik-internal; do
  k -n "$TRAEFIK_NS" rollout status "deployment/$deployment" --timeout=180s
done

step "ready"
k8s="minikube -p $PROFILE kubectl --"
cat <<EOF
  kubectl         : $k8s   (context $PROFILE)
  cvestream logs  : $k8s -n $NS logs -f deploy/cvestream
  public  (Internet, through traefik):
    $k8s -n $TRAEFIK_NS port-forward svc/traefik 8000:80
    curl -H 'Host: cvestream.cyprien.eu' http://127.0.0.1:8000/feeds/<token>.xml
    (tokens in $ENDPOINTS)
  private (the "VPN", straight to traefik-internal):
    $k8s -n $TRAEFIK_NS port-forward deploy/traefik-internal 8001
    curl -H 'Host: cvestream-admin.cyprien.eu' http://127.0.0.1:8001/status
EOF

if [[ "${SKIP_SMOKE:-}" != 1 ]]; then
  exec "$HERE/smoke.sh"
fi
