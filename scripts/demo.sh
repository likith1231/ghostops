#!/usr/bin/env bash
# GhostOps presentation helper. Run from anywhere; it switches to the repo root.
#
#   ./scripts/demo.sh check      # pre-flight: tools, cluster, keys, git state
#   ./scripts/demo.sh up         # open dashboards (port-forwards) and print the URLs
#   ./scripts/demo.sh backend    # run the GhostOps backend in this terminal (keep it open)
#   ./scripts/demo.sh tests      # run every test group separately, with a PASS banner each
#   ./scripts/demo.sh leak       # scenario 1, LIVE: leak memory -> alert -> AI fix -> PR
#   ./scripts/demo.sh ci         # scenario 2: CI test failure -> AI fix -> PR
#   ./scripts/demo.sh reset      # restart the sample app (frees the leaked memory)
#   ./scripts/demo.sh down       # stop the port-forwards
#
# `leak --direct` runs scenario 1 from a built-in sample instead of the live cluster
# (no Prometheus/Alertmanager wait), as a fallback.
set -uo pipefail
cd "$(dirname "$0")/.."

PROM=http://localhost:9090
BACKEND=http://localhost:8000
STATE_DIR=${TMPDIR:-/tmp}/ghostops-demo
mkdir -p "$STATE_DIR"

if [ -t 1 ]; then
  B=$'\033[1m'; G=$'\033[1;32m'; R=$'\033[1;31m'; Y=$'\033[1;33m'; C=$'\033[1;36m'; N=$'\033[0m'
else
  B=; G=; R=; Y=; C=; N=
fi
banner() { printf '\n%s%s%s\n%s%s%s\n%s%s%s\n' "$C" "$(printf '=%.0s' {1..72})" "$N" "$B" "  $*" "$N" "$C" "$(printf '=%.0s' {1..72})" "$N"; }
ok()   { printf '  %s✔%s %s\n' "$G" "$N" "$*"; }
bad()  { printf '  %s✘%s %s\n' "$R" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sERROR:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# Python from the project virtualenv.
if [ -x venv/bin/python ]; then PY=venv/bin/python
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else PY=python3; fi

kctl() { kubectl --context kind-ghostops "$@"; }

prom_value() {  # first value of an instant PromQL query, or empty
  curl -fsS -m 5 "$PROM/api/v1/query" --data-urlencode "query=$1" 2>/dev/null \
    | "$PY" -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "")' 2>/dev/null
}
memory_percent() {
  local v
  v=$(prom_value 'max(container_memory_working_set_bytes{pod=~"sample-app.*"} / container_spec_memory_limit_bytes{pod=~"sample-app.*"})')
  [ -n "$v" ] && "$PY" -c "print(round($v * 100))" || echo "?"
}
alert_state() {  # inactive | pending | firing
  curl -fsS -m 5 "$PROM/api/v1/alerts" 2>/dev/null | "$PY" -c '
import json, sys
alerts = [a for a in json.load(sys.stdin)["data"]["alerts"] if a["labels"].get("alertname") == "SampleAppHighMemory"]
states = {a["state"] for a in alerts}
print("firing" if "firing" in states else "pending" if "pending" in states else "inactive")' 2>/dev/null || echo "unknown"
}
incident_total() {
  curl -fsS -m 5 "$BACKEND/api/incidents/summary" 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["total"])' 2>/dev/null
}

# ------------------------------------------------------------------ check
cmd_check() {
  banner "Pre-flight check"
  local fail=0
  for t in docker kind kubectl curl; do
    command -v "$t" >/dev/null && ok "$t installed" || { bad "$t missing"; fail=1; }
  done
  docker info >/dev/null 2>&1 && ok "Docker is running (needed for the validation sandbox)" || { bad "Docker is not running"; fail=1; }
  if kctl get nodes >/dev/null 2>&1; then
    ok "kind cluster 'ghostops' is up"
    local notready
    notready=$(kctl get pods -n monitoring --no-headers 2>/dev/null | awk '$3 != "Running"' | wc -l)
    [ "$notready" -eq 0 ] && ok "monitoring pods running (Prometheus, Alertmanager, Grafana, Jaeger)" || { bad "$notready monitoring pod(s) not running"; fail=1; }
    kctl get deploy sample-app >/dev/null 2>&1 && ok "sample-app deployed" || { bad "sample-app not deployed"; fail=1; }
  else
    bad "kind cluster 'ghostops' not reachable (start Docker, then: docker start ghostops-control-plane)"; fail=1
  fi
  "$PY" -c 'import crewai, fastapi' 2>/dev/null && ok "Python deps installed ($PY)" || { bad "Python deps missing: $PY -m pip install -r backend/requirements.txt -r agents/requirements.txt"; fail=1; }
  if [ -f .env ]; then
    grep -q '^ANTHROPIC_API_KEY=.\+' .env && ok "ANTHROPIC_API_KEY set" || { bad "ANTHROPIC_API_KEY missing in .env"; fail=1; }
    grep -q '^GITHUB_TOKEN=.\+' .env && ok "GITHUB_TOKEN set (PRs will be opened)" || warn "GITHUB_TOKEN missing: fixes are validated but no PR is opened"
  else
    bad "no .env file"; fail=1
  fi
  # The deployer commits with `git add -A` and opens the PR against the current branch.
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)
  [ "$branch" = main ] && ok "on branch main" || warn "on branch '$branch': GhostOps PRs will target this branch, not main"
  [ -z "$(git status --porcelain)" ] && ok "git working tree clean" || { bad "uncommitted changes would be swept into the GhostOps PR:"; git status --short | sed 's/^/      /'; fail=1; }
  curl -fsS -m 3 "$BACKEND/health" >/dev/null 2>&1 && ok "backend running on :8000" || warn "backend not running yet: ./scripts/demo.sh backend (in its own terminal)"
  echo
  [ $fail -eq 0 ] && printf '%sReady for the demo.%s\n' "$G" "$N" || printf '%sFix the items marked ✘ first.%s\n' "$R" "$N"
  return $fail
}

# ------------------------------------------------------------------ up / down
forward() {  # name local-port namespace target remote-port
  local name=$1 lport=$2 ns=$3 target=$4 rport=$5
  if ss -ltn 2>/dev/null | grep -q ":$lport "; then return; fi
  nohup kubectl --context kind-ghostops -n "$ns" port-forward "$target" "$lport:$rport" >"$STATE_DIR/$name.log" 2>&1 &
  echo $! >"$STATE_DIR/$name.pid"
}
cmd_up() {
  banner "Opening dashboards"
  kctl apply -f infra/k8s/monitoring/grafana-nodeport.yaml >/dev/null
  forward jaeger-ui 16686 monitoring svc/jaeger 16686
  forward jaeger-otlp 4317 monitoring svc/jaeger 4317
  forward vault 8200 default svc/vault 8200
  forward chaos 2333 chaos-mesh svc/chaos-dashboard 2333
  forward sample-app 5000 default deploy/sample-app 5000
  sleep 2
  cat <<EOF
  Grafana (incident dashboard)  http://localhost:3000
  Prometheus (Alerts tab)       http://localhost:9090/alerts
  Alertmanager                  http://localhost:8080
  Jaeger (agent traces)         http://localhost:16686
  Chaos Mesh                    http://localhost:2333
  GhostOps API docs             http://localhost:8000/docs   (after ./scripts/demo.sh backend)
  Sample app                    http://localhost:5000
EOF
}
cmd_down() {
  for f in "$STATE_DIR"/*.pid; do
    [ -f "$f" ] || continue
    kill "$(cat "$f")" 2>/dev/null
    rm -f "$f"
  done
  ok "port-forwards stopped"
}

# ------------------------------------------------------------------ backend
cmd_backend() {
  banner "GhostOps backend: listening for Alertmanager webhooks on :8000"
  # No --reload: the deployer switches git branches while it opens a PR, which would restart the server.
  exec "$PY" -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
}

# ------------------------------------------------------------------ tests
cmd_tests() {
  local groups=(
    "Policy guardrails (OPA): AI may only patch allowed files|agents/tests/test_policy_enforcement.py"
    "PR safety gates: no PR unless validation passed|agents/tests/test_deployer.py"
    "Knowledge base: every incident is remembered|agents/tests/test_knowledge_base.py"
    "Backend API: health and incident endpoints|backend/tests/test_incidents.py"
  )
  local summary=() all_ok=0 i=1
  for g in "${groups[@]}"; do
    local title=${g%%|*} file=${g##*|} log="$STATE_DIR/test-$i.log"
    banner "Test group $i/${#groups[@]}: $title"
    OTEL_SDK_DISABLED=true "$PY" -m pytest -v --no-header -p no:cacheprovider --color=yes "$file" | tee "$log"
    local rc=${PIPESTATUS[0]} counts
    counts=$(tail -1 "$log" | sed 's/\x1b\[[0-9;]*m//g' | grep -oE '[0-9]+ (passed|failed|error)[s]?' | paste -sd, -)
    if [ "$rc" -eq 0 ]; then
      printf '\n%s  ✅ PASSED%s  %s\n' "$G" "$N" "$counts"; summary+=("$G✅ PASS$N  $title  ($counts)")
    else
      printf '\n%s  ❌ FAILED%s  %s\n' "$R" "$N" "$counts"; summary+=("$R❌ FAIL$N  $title  ($counts)"); all_ok=1
    fi
    i=$((i + 1))
  done
  banner "Test summary"
  printf '  %s\n' "${summary[@]}"
  return $all_ok
}

# ------------------------------------------------------------------ scenarios
cmd_leak() {
  if [ "${1:-}" = "--direct" ]; then
    banner "Scenario 1: Memory leak (built-in sample, no cluster)"
    "$PY" -m scripts.run_scenario leak
    return
  fi
  banner "Scenario 1: Memory leak, live on the cluster"
  curl -fsS -m 3 "$BACKEND/health" >/dev/null 2>&1 || die "Backend is not running. Open another terminal and run: ./scripts/demo.sh backend"
  curl -fsS -m 3 "$PROM/-/ready" >/dev/null 2>&1 || die "Prometheus is not reachable on :9090"
  curl -fsS -m 3 localhost:5000/health >/dev/null 2>&1 || { forward sample-app 5000 default deploy/sample-app 5000; sleep 3; }
  curl -fsS -m 3 localhost:5000/health >/dev/null 2>&1 || die "sample-app not reachable on :5000"

  local before started
  before=$(incident_total); before=${before:-0}
  started=$(date +%s)

  echo "  Step 1: leaking memory in the sample app (10 MiB per request, limit 128 MiB)"
  for i in $(seq 1 8); do
    local held
    held=$(curl -fsS -m 5 localhost:5000/leak >/dev/null && curl -fsS -m 5 localhost:5000/health | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["leaked_objects"] * 10)')
    printf '    request %d → %s MiB leaked\n' "$i" "${held:-?}"
  done

  echo "  Step 2: waiting for Prometheus to fire SampleAppHighMemory (memory > 80% for 30 s)"
  local state=""
  for _ in $(seq 1 60); do
    state=$(alert_state)
    printf '\r    memory %s%%  alert: %-10s' "$(memory_percent)" "$state"
    [ "$state" = firing ] && break
    sleep 5
  done
  echo
  [ "$state" = firing ] || die "Alert did not fire within 5 minutes. Try: ./scripts/demo.sh reset, then leak again, or ./scripts/demo.sh leak --direct"
  ok "alert FIRING → Alertmanager sends it to GhostOps"

  echo "  Step 3: AI agents diagnosing, patching and validating (watch the backend terminal)"
  local total=$before
  for _ in $(seq 1 120); do
    total=$(incident_total); total=${total:-$before}
    [ "$total" -gt "$before" ] && break
    printf '\r    working… %ds' $(($(date +%s) - started))
    sleep 5
  done
  echo
  [ "$total" -gt "$before" ] || die "No new incident after 10 minutes. Check the backend terminal for errors."

  local elapsed=$(($(date +%s) - started))
  curl -fsS -m 5 "$BACKEND/api/incidents/recent?limit=1" | "$PY" -c '
import json, sys
inc = json.load(sys.stdin)[0]
G, R, B, N = sys.argv[2:6]
print("=" * 72)
print(f"{B}Scenario 1: Memory leak (live){N}")
print("=" * 72)
print(f"  Root cause  : {inc.get(\"root_cause\") or \"-\"}")
print(f"  Confidence  : {inc.get(\"confidence\") or \"-\"}")
print(f"  Pull request: {inc.get(\"pr_url\") or \"not created\"}")
print(f"  Time taken  : {sys.argv[1]} s from first leak to fix")
print()
if inc.get("validation_passed"):
    print(f"{G}  ✅ PASSED: the AI fix was validated in the sandbox{N}")
else:
    print(f"{R}  ❌ FAILED: the patch did not pass validation{N}")
print("=" * 72)
sys.exit(0 if inc.get("validation_passed") else 1)' "$elapsed" "$G" "$R" "$B" "$N"
}

cmd_ci() {
  banner "Scenario 2: CI test failure (multiply() uses + instead of *)"
  "$PY" -m scripts.run_scenario ci
}

cmd_reset() {
  kctl rollout restart deploy/sample-app >/dev/null
  kctl rollout status deploy/sample-app --timeout=120s
  # The old port-forward points at the deleted pod.
  [ -f "$STATE_DIR/sample-app.pid" ] && kill "$(cat "$STATE_DIR/sample-app.pid")" 2>/dev/null
  rm -f "$STATE_DIR/sample-app.pid"
  forward sample-app 5000 default deploy/sample-app 5000
  ok "sample-app restarted with empty memory"
}

case "${1:-}" in
  check) cmd_check ;;
  up) cmd_up ;;
  down) cmd_down ;;
  backend) cmd_backend ;;
  tests) cmd_tests ;;
  leak) shift; cmd_leak "$@" ;;
  ci) cmd_ci ;;
  reset) cmd_reset ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
