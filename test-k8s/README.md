# test-k8s: cvestream on a throwaway minikube cluster

Everything needed to bootstrap a local Kubernetes test of cvestream, built from
the current working tree, behind the two Traefik instances proposed in
wixyvir/services#1: the public `traefik` serves the feeds, the VPN-only
`traefik-internal` serves the private API (`/healthz`, `/readyz`, `/status`).
All plain HTTP.

It is a test harness, not the supported deployment: the published manifests are
story 3.3 (`deploy/k8s/`). The manifests here follow that plan
(architecture.md, "Infrastructure & Deployment") and the conventions of
wixyvir/services (`k8s/`), so they can seed both.

## Quick start

```sh
./test-k8s/up.sh      # cluster + image + deploy + smoke checks (first run ~5 min)
./test-k8s/smoke.sh   # re-run the checks only
./test-k8s/down.sh    # delete the cluster (--all also deletes the generated tokens)
```

Run `up.sh` again after a code change: it rebuilds the image, loads it into the
node and restarts the Deployment. Environment knobs:

| Variable | Effect |
|---|---|
| `CVESTREAM_TEST_PROFILE` | minikube profile (default `cvestream-test`; other profiles are never touched) |
| `CVESTREAM_TEST_ENDPOINTS` | deploy your own endpoints file instead of generated tokens |
| `SKIP_SMOKE=1` | `up.sh` stops after the rollout |
| `SKIP_RELOAD=1` | `smoke.sh` skips the hot-reload check (the slowest, up to ~5 min) |
| `PF_PUBLIC`, `PF_PRIVATE`, `PF_TRAEFIK`, `PF_INTERNAL` | local port-forward ports of `smoke.sh` (18080, 19090, 18000, 18001) |

Prerequisites: `minikube` (docker driver), `docker`, `python3`, `curl`,
`setsid`, and direct HTTPS egress. kubectl is minikube's own
(`minikube kubectl`), which matches the cluster version; a distro kubectl may be
many minors behind.

## Layout

```
Internet ─▶ svc/traefik ─▶ traefik         ─ IngressRoute apps/cvestream     : Host(cvestream.cyprien.eu) && PathPrefix(/feeds/) ─▶ :8080
VPN ──────────────────────▶ traefik-internal ─ IngressRoute private/cvestream : Host(cvestream-admin.cyprien.eu)               ─▶ :9090
```

| Namespace | Content |
|---|---|
| `traefik` | `traefik` (public, copy of the production RBAC, watches `traefik,apps`) and `traefik-internal` (watches `private` only, no Service, deny-all NetworkPolicy) |
| `apps` | cvestream: Deployment, RWO PVC, Services `cvestream-public` (8080) and `cvestream-private` (9090), NetworkPolicy, public IngressRoute |
| `private` | cvestream's private IngressRoute and the ExternalName Service it targets; default-deny NetworkPolicy |

In this harness, "the Internet" is `kubectl port-forward svc/traefik` and "the
VPN" is `kubectl port-forward deploy/traefik-internal`. Like traffic decapsulated
on wg0, port-forward enters the pod without going through the CNI. WireGuard
itself is out of scope.

What `up.sh` sets up:

- minikube profile `cvestream-test` with **Calico**, because the default CNI
  does not enforce NetworkPolicy.
- Traefik CRDs pinned to v3.6.15 (server-side apply), identical to
  wixyvir/services `k8s/infra/traefik/crds.yaml`.
- Image `cvestream:test-k8s` (`docker build --target runtime`) loaded into the
  node, `imagePullPolicy: Never`.
- `.generated/endpoints.yaml` (gitignored, `chmod 600`): `endpoints.example.yaml`
  with one random token per placeholder, validated with
  `cvestream config validate` in the image, shipped as the Secret
  `cvestream-endpoints`, mounted as the `/config` directory (no `subPath`, so
  updates reach the pod).
- cvestream: 1 replica, `Recreate`, uid 999, read-only root, no capabilities,
  `RuntimeDefault` seccomp, `terminationGracePeriodSeconds: 60`,
  `enableServiceLinks: false`, exec probes on `cvestream healthcheck [--ready]`,
  `restricted` Pod Security Standard on `apps`. Poll intervals are `PT2M` so
  the reload check finishes quickly.

## Findings that matter for wixyvir/services#1

Measured on Traefik v3.6.15:

1. **`namespaces` also limits Service lookups.** With
   `--providers.kubernetescrd.namespaces=private`, an IngressRoute in `private`
   pointing at `apps/cvestream-private` fails with "namespace is not within
   watched namespaces", even with `allowCrossNamespace=true`. Step 8 of the
   issue does not work as written. Here, an **ExternalName Service in
   `private`** (`private/service-cvestream.yaml`) points at
   `cvestream-private.apps.svc.cluster.local`, with
   `--providers.kubernetescrd.allowExternalNameServices=true`. traefik-internal
   then needs **no RBAC at all in `apps`** and no cross-namespace references.
   The connection still comes from the traefik-internal pod, so cvestream's
   NetworkPolicy still applies.
2. **The CRD provider needs `secrets` and `configmaps` in every watched
   namespace**, even with plain HTTP. Without them it builds no configuration:
   every route returns 404, and the only trace is client-go `forbidden` lines
   (klog format, `E1101 ...`) on stderr. Watching `apps` would therefore mean
   reading its secrets, which is one more reason for finding 1's approach.
   `disableClusterScopeResources=true` works: no ClusterRole is needed.
3. **Feed tokens and access logs.** The production Traefik logs every request
   in JSON, path included. The public cvestream route sets
   `observability.accessLogs: false`, so feed requests never reach the access
   log while the rest is still logged.
4. A deny-all ingress NetworkPolicy on traefik-internal keeps its kubelet
   `httpGet` probes working (Calico).

## What smoke.sh checks

1. Deployment shape: 1 replica, `Recreate`, ready, no container restart.
2. Hardening inside the pod: uid 999, read-only root, writable `/data` and
   `/tmp`, no effective capability, no Service-link variables in `CVESTREAM_*`.
3. `cvestream healthcheck` and `--ready` from inside the pod.
4. cvestream's own listeners through their Services: `/healthz`, `/readyz`,
   `/status`; each enabled token serves RSS (GET and HEAD); the disabled
   endpoint and an unknown token return 404; `/status` is not public.
5. Public Traefik: feeds return 200; `/healthz`, `/readyz` and `/status` on the
   public host return 404 (allowlist); the admin host is not served.
6. traefik-internal: the admin host serves `/healthz`, `/readyz` and `/status`;
   the public route in `apps` is not served there.
7. Traefik logs: no feed token in the access log, the access log is otherwise
   on, and neither instance logs an error or RBAC refusal.
8. NetworkPolicy, one port per instance: `traefik` reaches 8080 only,
   `traefik-internal` reaches 9090 only.
9. NetworkPolicy, other pods: a pod in `apps` reaches neither cvestream port
   nor traefik-internal; it does reach the public Traefik (control).
10. Egress from the cvestream pod: HTTPS to each source host answers
    (NetworkPolicy egress, DNS and the image's trust store, no proxy). Then
    `/status`: the latest outcome of each source must not be an error. A source
    still fetching is only a warning; the first EUVD fetch is a paginated
    backfill (about 17,600 records in 5 to 6 minutes when measured).
11. Restart: the old pod logs `app.stopping` and `app.stopped` (graceful
    SIGTERM); a marker file in `/data` and the state database survive.
12. Hot reload: enabling the disabled endpoint in the Secret makes its feed
    answer 200 without a restart; then the Secret is restored.

The exit status is the number of failed checks.

## Using it by hand

```sh
alias k='minikube -p cvestream-test kubectl --'
k -n apps logs -f deploy/cvestream

# Internet side
k -n traefik port-forward svc/traefik 8000:80
curl -H 'Host: cvestream.cyprien.eu' http://127.0.0.1:8000/feeds/<token>.xml

# VPN side
k -n traefik port-forward deploy/traefik-internal 8001
curl -H 'Host: cvestream-admin.cyprien.eu' http://127.0.0.1:8001/status
```

## Network notes

- cvestream ignores proxy variables (`trust_env=False`): the cluster needs
  **direct** HTTPS egress to the sources. The scripts unset `HTTP(S)_PROXY` so
  minikube does not bake a proxy into the node and kubectl does not send API
  calls to the node IP through it.
- No stock `Ingress`: wixyvir/services uses Traefik IngressRoutes, and
  ingress-nginx (minikube's `ingress` addon) was retired in March 2026.
