#!/usr/bin/env bash
# ok/warn always succeed, so `test && ok || ko` is a safe if/else here; the
# cleanup trap is invoked indirectly.
# shellcheck disable=SC2015,SC2329
# Check the running test deployment. Exit status: number of failed checks.
#
# Environment:
#   SKIP_RELOAD=1   skip the Secret hot-reload check (it waits for the kubelet
#                   sync plus one poll cycle: up to ~5 minutes)
#   PF_PUBLIC / PF_PRIVATE   local ports for cvestream's Services (18080 / 19090)
#   PF_TRAEFIK / PF_INTERNAL local ports for traefik / traefik-internal (18000 / 18001)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require minikube curl python3 setsid
[[ -f "$ENDPOINTS" ]] || die "$ENDPOINTS is missing: run up.sh first"

PF_PUBLIC="${PF_PUBLIC:-18080}"
PF_PRIVATE="${PF_PRIVATE:-19090}"
PF_TRAEFIK="${PF_TRAEFIK:-18000}"
PF_INTERNAL="${PF_INTERNAL:-18001}"
PUBLIC_HOST=cvestream.cyprien.eu
ADMIN_HOST=cvestream-admin.cyprien.eu
WORK="$(mktemp -d)"
PF_PIDS=()
cleanup() {
  pf_stop
  bg_kill "${LOGS_PID:-}"
  rm -rf "$WORK"
}
trap cleanup EXIT

# --- helpers -----------------------------------------------------------------

pf_stop() {
  local pid
  for pid in "${PF_PIDS[@]}"; do bg_kill "$pid"; done
  PF_PIDS=()
}

pf_start() {
  pf_stop
  bg_kctl port-forward svc/cvestream-public "$PF_PUBLIC:8080" >"$WORK/pf-public.log" 2>&1
  PF_PIDS+=("$BG_PID")
  bg_kctl port-forward svc/cvestream-private "$PF_PRIVATE:9090" >"$WORK/pf-private.log" 2>&1
  PF_PIDS+=("$BG_PID")
  # The Internet side: the public Traefik's Service (a LoadBalancer in prod).
  bg_kubectl "$TRAEFIK_NS" port-forward svc/traefik "$PF_TRAEFIK:80" >"$WORK/pf-traefik.log" 2>&1
  PF_PIDS+=("$BG_PID")
  # The VPN side: straight into the traefik-internal pod, outside the CNI.
  bg_kubectl "$TRAEFIK_NS" port-forward deploy/traefik-internal "$PF_INTERNAL:8001" \
    >"$WORK/pf-internal.log" 2>&1
  PF_PIDS+=("$BG_PID")
  local _ port up
  for _ in $(seq 50); do
    up=0
    for port in "$PF_PUBLIC" "$PF_PRIVATE" "$PF_TRAEFIK" "$PF_INTERNAL"; do
      curl -s -o /dev/null "http://127.0.0.1:$port/" && up=$((up + 1))
    done
    ((up == 4)) && return 0
    sleep 0.2
  done
  die "port-forward did not come up (see $WORK/pf-*.log)"
}

# HTTP status of a local URL (000 when unreachable). Extra args go to curl.
http_code() { curl -s -o /dev/null -w '%{http_code}' -m 5 "$@"; }

current_pod() {
  kctl get pods -l app.kubernetes.io/name=cvestream \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}'
}

# id<TAB>enabled<TAB>token per endpoint. A line parser, not a YAML one: it
# expects `id:` to open each entry, as in endpoints.example.yaml.
endpoint_table() {
  python3 -I - "$ENDPOINTS" <<'PY'
import re, sys
rows, cur = [], None
for line in open(sys.argv[1]):
    m = re.match(r"\s*(?:-\s+)?(id|enabled|token):\s*(\S+)", line)
    if not m:
        continue
    key, value = m.groups()
    if key == "id":
        cur = {"id": value, "enabled": "true", "token": ""}
        rows.append(cur)
    elif cur is not None:
        cur[key] = value.strip("\"'")
for r in rows:
    print(r["id"], r["enabled"], r["token"], sep="\t")
PY
}

# Run curl from one throwaway, unlabelled pod in $NS (restricted PSS) against
# each URL; prints one HTTP status per line (000 when unreachable).
in_cluster_codes() {
  local name="np-probe-$RANDOM" script="" url overrides
  for url in "$@"; do
    script+="curl -s -o /dev/null -w '%{http_code}\\n' -m 5 $url || true; "
  done
  overrides=$(
    cat <<EOF
{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":100,"runAsGroup":101,
                            "seccompProfile":{"type":"RuntimeDefault"}},
         "containers":[{"name":"$name","image":"$CURL_IMAGE",
           "command":["sh","-c","$script"],
           "securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}
EOF
  )
  kctl run "$name" --image="$CURL_IMAGE" --restart=Never --rm -i --quiet \
    --pod-running-timeout=3m --overrides="$overrides" 2>/dev/null | tr -d '\r'
}

# HTTP status of URL $2 fetched from inside the Traefik deployment $1 (busybox
# wget, which the image ships); 000 when unreachable.
from_traefik() {
  local code
  code=$(k -n "$TRAEFIK_NS" exec "deploy/$1" -- sh -c \
    "wget -S -q -T 5 -O /dev/null '$2' 2>&1 | awk '/^ *HTTP\\//{c=\$2} END{print c}'" 2>/dev/null)
  echo "${code:-000}"
}

# HTTP status through one of the Traefik port-forwards: $1 port, $2 Host, $3 path.
via() { http_code -H "Host: $2" "http://127.0.0.1:$1$3"; }

# --- checks ------------------------------------------------------------------

step "deployment shape"
replicas=$(kctl get deploy cvestream -o jsonpath='{.spec.replicas}')
strategy=$(kctl get deploy cvestream -o jsonpath='{.spec.strategy.type}')
ready=$(kctl get deploy cvestream -o jsonpath='{.status.readyReplicas}')
[[ "$replicas" == 1 ]] && ok "replicas: 1" || ko "replicas: $replicas"
[[ "$strategy" == Recreate ]] && ok "strategy: Recreate" || ko "strategy: $strategy"
[[ "$ready" == 1 ]] && ok "1 ready replica" || ko "ready replicas: ${ready:-0}"
POD=$(current_pod)
restarts=$(kctl get pod "$POD" -o jsonpath='{.status.containerStatuses[0].restartCount}')
[[ "$restarts" == 0 ]] && ok "no container restart" || warn "$restarts container restart(s) on $POD"

step "container hardening ($POD)"
uid=$(kctl exec "$POD" -- id -u)
[[ "$uid" == 999 ]] && ok "runs as uid 999" || ko "runs as uid $uid"
if kctl exec "$POD" -- sh -c ': >/app/.w' 2>/dev/null; then
  ko "root filesystem is writable"
else
  ok "root filesystem is read-only"
fi
kctl exec "$POD" -- sh -c ': >/data/.w && rm /data/.w && : >/tmp/.w && rm /tmp/.w' &&
  ok "/data and /tmp are writable" || ko "/data or /tmp is not writable"
capeff=$(kctl exec "$POD" -- sh -c "awk '/^CapEff/ {print \$2}' /proc/1/status")
[[ "$capeff" == 0000000000000000 ]] && ok "no effective capability" || ko "CapEff=$capeff"
leaked=$(kctl exec "$POD" -- sh -c 'env | grep -c "^CVESTREAM_PUBLIC\|^CVESTREAM_PRIVATE" || true')
[[ "$leaked" == 0 ]] && ok "no Service link variables in CVESTREAM_*" || ko "$leaked Service link variables"

step "probes, from inside the pod"
kctl exec "$POD" -- cvestream healthcheck && ok "cvestream healthcheck" || ko "cvestream healthcheck"
kctl exec "$POD" -- cvestream healthcheck --ready && ok "cvestream healthcheck --ready" ||
  ko "cvestream healthcheck --ready"

step "private listener (port-forward :$PF_PRIVATE)"
pf_start
for path in healthz readyz status; do
  code=$(http_code "http://127.0.0.1:$PF_PRIVATE/$path")
  [[ "$code" == 200 ]] && ok "GET /$path -> 200" || ko "GET /$path -> $code"
done
curl -s "http://127.0.0.1:$PF_PRIVATE/status" >"$WORK/status.json"
python3 -I -c '
import json, sys
s = json.load(open(sys.argv[1]))
missing = {"euvd", "cisa_kev"} - set(s["sources"])
sys.exit(f"sources missing from /status: {missing}" if missing else 0)
' "$WORK/status.json" && ok "/status lists euvd and cisa_kev" || ko "/status sources"

step "public feeds (port-forward :$PF_PUBLIC)"
endpoint_table >"$WORK/endpoints.tsv"
while IFS=$'\t' read -r id enabled token; do
  url="http://127.0.0.1:$PF_PUBLIC/feeds/$token.xml"
  code=$(http_code "$url")
  if [[ "$enabled" == true ]]; then
    [[ "$code" == 200 ]] && ok "$id (enabled) -> 200" || ko "$id (enabled) -> $code"
    curl -s "$url" | grep -q '<rss' && ok "$id serves RSS" || ko "$id body is not RSS"
    code=$(http_code -I "$url")
    [[ "$code" == 200 ]] && ok "$id HEAD -> 200" || ko "$id HEAD -> $code"
  else
    [[ "$code" == 404 ]] && ok "$id (disabled) -> 404" || ko "$id (disabled) -> $code"
  fi
done <"$WORK/endpoints.tsv"
code=$(http_code "http://127.0.0.1:$PF_PUBLIC/feeds/$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(32))').xml")
[[ "$code" == 404 ]] && ok "unknown token -> 404" || ko "unknown token -> $code"
code=$(http_code "http://127.0.0.1:$PF_PUBLIC/status")
[[ "$code" == 404 ]] && ok "/status is not on the public listener" || ko "public /status -> $code"

step "ingress: public Traefik (the Internet side, port-forward :$PF_TRAEFIK)"
# wixyvir/services#1 step 8: the public route allowlists /feeds/ only.
while IFS=$'\t' read -r id enabled token; do
  [[ "$enabled" == true ]] || continue
  code=$(via "$PF_TRAEFIK" "$PUBLIC_HOST" "/feeds/$token.xml")
  [[ "$code" == 200 ]] && ok "$PUBLIC_HOST feed of $id -> 200" || ko "$PUBLIC_HOST feed of $id -> $code"
done <"$WORK/endpoints.tsv"
for path in /healthz /readyz /status; do
  code=$(via "$PF_TRAEFIK" "$PUBLIC_HOST" "$path")
  [[ "$code" == 404 ]] && ok "$PUBLIC_HOST$path -> 404 (not routed)" || ko "$PUBLIC_HOST$path -> $code"
done
code=$(via "$PF_TRAEFIK" "$ADMIN_HOST" /status)
[[ "$code" == 404 ]] && ok "$ADMIN_HOST is not served publicly (private IngressRoute ignored)" ||
  ko "$ADMIN_HOST/status -> $code through the public Traefik"

step "ingress: traefik-internal (the VPN side, port-forward :$PF_INTERNAL)"
for path in /healthz /readyz /status; do
  code=$(via "$PF_INTERNAL" "$ADMIN_HOST" "$path")
  [[ "$code" == 200 ]] && ok "$ADMIN_HOST$path -> 200" || ko "$ADMIN_HOST$path -> $code"
done
curl -s -m 5 -H "Host: $ADMIN_HOST" "http://127.0.0.1:$PF_INTERNAL/status" | grep -q '"sources"' &&
  ok "$ADMIN_HOST/status is cvestream's status JSON" || ko "$ADMIN_HOST/status body"
token=$(awk -F'\t' '$2 == "true" {print $3; exit}' "$WORK/endpoints.tsv")
code=$(via "$PF_INTERNAL" "$PUBLIC_HOST" "/feeds/$token.xml")
[[ "$code" == 404 ]] && ok "public IngressRoute in apps is not served by traefik-internal" ||
  ko "$PUBLIC_HOST feed -> $code through traefik-internal"

step "ingress: Traefik logs"
traefik_log="$WORK/traefik.log"
k -n "$TRAEFIK_NS" logs deploy/traefik >"$traefik_log" 2>&1
leaks=0
while IFS=$'\t' read -r _ _ token; do
  [[ -n "$token" ]] && leaks=$((leaks + $(grep -c -- "$token" "$traefik_log")))
done <"$WORK/endpoints.tsv"
[[ "$leaks" == 0 ]] && ok "no feed token in the public Traefik's access log (route accessLogs: false)" ||
  ko "$leaks access-log line(s) with a feed token"
grep -q '"RequestPath":"/healthz"' "$traefik_log" && ok "access log still on for other requests" ||
  warn "no access-log line for the /healthz 404: is the access log on?"
for deployment in traefik traefik-internal; do
  # client-go reports RBAC failures on stderr in klog format (E1101 ...),
  # outside Traefik's own log levels.
  k -n "$TRAEFIK_NS" logs "deploy/$deployment" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' |
    grep -E '^E[0-9]{4} |forbidden| ERR ' >"$WORK/$deployment.errors"
  # "no servers found": the backend had no ready pod (cvestream starting or
  # restarting, e.g. by the restart check of a previous run). Expected, not fatal.
  transient=$(grep -c 'no servers found' "$WORK/$deployment.errors")
  errors=$(grep -vc 'no servers found' "$WORK/$deployment.errors")
  [[ "$errors" == 0 ]] && ok "$deployment: no ERR, klog error or RBAC refusal" ||
    ko "$deployment: $errors error line(s), see: k -n $TRAEFIK_NS logs deploy/$deployment"
  ((transient == 0)) || warn "$deployment: $transient 'no servers found' line(s) while cvestream was not ready"
done

step "NetworkPolicy: one port per Traefik instance"
code=$(from_traefik traefik "http://cvestream-public.$NS:8080/feeds/nope.xml")
[[ "$code" == 404 ]] && ok "traefik -> cvestream :8080 open ($code)" || ko "traefik -> cvestream :8080: $code"
code=$(from_traefik traefik "http://cvestream-private.$NS:9090/healthz")
[[ "$code" == 000 ]] && ok "traefik -> cvestream :9090 blocked" || ko "traefik reaches cvestream :9090 ($code)"
code=$(from_traefik traefik-internal "http://cvestream-private.$NS:9090/healthz")
[[ "$code" == 200 ]] && ok "traefik-internal -> cvestream :9090 open" || ko "traefik-internal -> cvestream :9090: $code"
code=$(from_traefik traefik-internal "http://cvestream-public.$NS:8080/feeds/nope.xml")
[[ "$code" == 000 ]] && ok "traefik-internal -> cvestream :8080 blocked" ||
  ko "traefik-internal reaches cvestream :8080 ($code)"

step "NetworkPolicy: other pods (one curl pod in $NS)"
internal_ip=$(k -n "$TRAEFIK_NS" get pods -l app.kubernetes.io/name=traefik-internal \
  -o jsonpath='{.items[0].status.podIP}')
mapfile -t codes < <(in_cluster_codes \
  "http://cvestream-public:8080/feeds/nope.xml" \
  "http://cvestream-private:9090/healthz" \
  "http://$internal_ip:8001/" \
  "http://$internal_ip:8080/ping" \
  "http://traefik.$TRAEFIK_NS/")
[[ "${codes[0]:-}" == 000 ]] && ok "pod in $NS -> cvestream :8080 blocked" || ko "pod in $NS -> cvestream :8080: ${codes[0]:-}"
[[ "${codes[1]:-}" == 000 ]] && ok "pod in $NS -> cvestream :9090 blocked" || ko "pod in $NS -> cvestream :9090: ${codes[1]:-}"
[[ "${codes[2]:-}" == 000 ]] && ok "pod in $NS -> traefik-internal :8001 blocked" || ko "pod in $NS -> traefik-internal :8001: ${codes[2]:-}"
[[ "${codes[3]:-}" == 000 ]] && ok "pod in $NS -> traefik-internal :8080 blocked" || ko "pod in $NS -> traefik-internal :8080: ${codes[3]:-}"
[[ "${codes[4]:-}" == 404 ]] && ok "pod in $NS -> public traefik open (control, ${codes[4]})" ||
  ko "control: pod in $NS -> public traefik: ${codes[4]:-} (is the curl pod working?)"

step "egress from the cvestream pod (NetworkPolicy, DNS, image trust store)"
# The hosts the adapters fetch from (src/cvestream/sources). Any HTTP answer,
# 404 included, proves DNS, TCP 443 and TLS verification work; no proxy, as in
# cvestream.http.
for host in euvdservices.enisa.europa.eu www.cisa.gov; do
  result=$(kctl exec "$POD" -- python -c '
import sys, urllib.error, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    print(opener.open("https://" + sys.argv[1] + "/", timeout=10).status)
except urllib.error.HTTPError as exc:
    print(exc.code)
except Exception as exc:
    print(type(exc).__name__, exc)
' "$host" 2>&1)
  [[ "$result" =~ ^[0-9]{3}$ ]] && ok "https://$host -> HTTP $result" || ko "https://$host -> $result"
done

step "source status (waiting up to 3 min for every source to settle)"
# The latest outcome counts: an old error followed by a success is fine. A
# source still fetching is a warning: the first EUVD fetch is a paginated
# backfill that takes minutes.
source_report() {
  python3 -I -c '
import json, sys
for name, v in sorted(json.load(open(sys.argv[1]))["sources"].items()):
    success, error, since = v["last_success_at"], v["last_error"], v["activity_since"]
    if v["activity"] == "fetching":
        print(f"warn\t{name}: fetching since {since}")
    elif error and (not success or error["at"] > success):
        print("ko\t%s: %s at %s" % (name, error["type"], error["at"]))
    elif success:
        print(f"ok\t{name}: last success {success}")
    else:
        print(f"warn\t{name}: no fetch yet")
' "$WORK/status.json"
}
for _ in $(seq 36); do
  curl -s "http://127.0.0.1:$PF_PRIVATE/status" >"$WORK/status.json"
  source_report | grep -q '^warn' || break
  sleep 5
done
while IFS=$'\t' read -r level msg; do
  case $level in ok) ok "$msg" ;; ko) ko "$msg" ;; *) warn "$msg" ;; esac
done < <(source_report)

step "restart: graceful shutdown and /data persistence"
marker=".smoke-$RANDOM"
kctl exec "$POD" -- sh -c ": >/data/$marker"
bg_kctl logs -f "$POD" >"$WORK/old-pod.log" 2>&1
LOGS_PID=$BG_PID
pf_stop
kctl delete pod "$POD" --wait=true >/dev/null
bg_kill "$LOGS_PID"
LOGS_PID=
grep -q '"app.stopping"' "$WORK/old-pod.log" && ok "old pod logged app.stopping" || ko "no app.stopping in the old pod's logs"
grep -q '"app.stopped"' "$WORK/old-pod.log" && ok "old pod logged app.stopped" || ko "no app.stopped (killed before the end of the grace period?)"
kctl rollout status deployment/cvestream --timeout=180s >/dev/null && ok "new pod ready" || ko "new pod not ready"
POD=$(current_pod)
kctl exec "$POD" -- test -f "/data/$marker" && ok "/data survived the restart" || ko "/data was lost"
kctl exec "$POD" -- test -s /data/cvestream.db && ok "state database present" || ko "no /data/cvestream.db"
kctl exec "$POD" -- rm -f "/data/$marker"
pf_start

step "hot reload of the endpoints Secret"
disabled=$(awk -F'\t' '$2 == "false" {print $1 "\t" $3; exit}' "$WORK/endpoints.tsv")
if [[ "${SKIP_RELOAD:-}" == 1 ]]; then
  warn "skipped (SKIP_RELOAD=1)"
elif [[ -z "$disabled" ]]; then
  warn "skipped: no disabled endpoint to enable"
else
  IFS=$'\t' read -r id token <<<"$disabled"
  sed 's/^\(\s*\)enabled: false/\1enabled: true/' "$ENDPOINTS" >"$WORK/endpoints.yaml"
  kctl create secret generic cvestream-endpoints --from-file=endpoints.yaml="$WORK/endpoints.yaml" \
    --dry-run=client -o yaml | kctl apply -f - >/dev/null
  echo "  enabled $id in the Secret; waiting for the kubelet sync and the next poll cycle..."
  code=
  for _ in $(seq 60); do
    code=$(http_code "http://127.0.0.1:$PF_PUBLIC/feeds/$token.xml")
    [[ "$code" == 200 ]] && break
    sleep 5
  done
  [[ "$code" == 200 ]] && ok "$id now -> 200 without a restart" || ko "$id still -> $code after 5 min"
  restarts=$(kctl get pod "$POD" -o jsonpath='{.status.containerStatuses[0].restartCount}')
  [[ "$restarts" == 0 ]] && ok "no restart during the reload" || ko "$restarts restart(s)"
  kctl logs "$POD" | grep -q '"config.reloaded"' && ok "logged config.reloaded" || warn "no config.reloaded log line"
  # Back to the original file, and wait until cvestream applies it: otherwise
  # the next run starts with the endpoint still enabled.
  kctl create secret generic cvestream-endpoints --from-file=endpoints.yaml="$ENDPOINTS" \
    --dry-run=client -o yaml | kctl apply -f - >/dev/null
  echo "  restored the Secret; waiting for $id to be disabled again..."
  for _ in $(seq 60); do
    code=$(http_code "http://127.0.0.1:$PF_PUBLIC/feeds/$token.xml")
    [[ "$code" == 404 ]] && break
    sleep 5
  done
  [[ "$code" == 404 ]] && ok "Secret restored and applied ($id -> 404)" || ko "$id still -> $code after the restore"
fi

step "summary"
if ((FAILURES == 0)); then
  printf '  \033[32mall checks passed\033[0m\n'
else
  printf '  \033[31m%d check(s) failed\033[0m\n' "$FAILURES"
fi
exit "$FAILURES"
