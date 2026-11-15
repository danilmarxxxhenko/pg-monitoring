#!/usr/bin/env bash
# End-to-end smoke test of the running stack.
# Requires: docker compose, python3 on the host. Run with `make smoke`.
set -uo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose"
FAILED=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=$((FAILED + 1)); }

# Run an HTTP GET from inside the Prometheus container (busybox wget).
prom_get() { $COMPOSE exec -T prometheus wget -qO- "http://localhost:9090$1"; }
psql_q() { $COMPOSE exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""; }

echo "== Containers"
unhealthy=$($COMPOSE ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1}')
if [[ -z "$unhealthy" ]]; then pass "all services with healthchecks are healthy"; else fail "unhealthy: $unhealthy"; fi

echo "== Prometheus"
targets=$(prom_get /api/v1/targets | python3 -c '
import sys, json
ts = json.load(sys.stdin)["data"]["activeTargets"]
down = [t["labels"]["job"] for t in ts if t["health"] != "up"]
print(len(ts), ",".join(down))')
total=${targets%% *}; down=${targets#* }
if [[ -z "$down" && "$total" -gt 0 ]]; then pass "all $total scrape targets are up"; else fail "targets down: $down"; fi

stmts=$(prom_get '/api/v1/query?query=count(pg_stat_statements_calls_total)' | python3 -c '
import sys, json; r = json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else 0)')
if [[ "${stmts%.*}" -gt 0 ]]; then pass "pg_stat_statements metrics collected ($stmts statements)"; else fail "no pg_stat_statements metrics"; fi

echo "== Access control (nginx + auth)"
set -a; source .env; set +a
BASE="http://localhost:${HTTP_PORT:-80}"
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT
code_of() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

redirect=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "$BASE/grafana/d/pg-overview")
if [[ "$redirect" == 302*"/login?next="* ]]; then pass "Grafana without session -> 302 to /login"; else fail "Grafana without session -> $redirect"; fi
code=$(code_of -c "$JAR" -X POST --data-urlencode "username=$AUTH_ADMIN_USER" \
  --data-urlencode "password=$AUTH_ADMIN_PASSWORD" -d next=/grafana/ "$BASE/login")
if [[ "$code" == 303 ]]; then pass "admin login -> 303 with session cookie"; else fail "admin login -> $code"; fi
code=$(code_of -b "$JAR" "$BASE/grafana/d/pg-overview")
if [[ "$code" == 200 ]]; then pass "Grafana with session -> 200"; else fail "Grafana with session -> $code"; fi
role=$(curl -s -b "$JAR" "$BASE/grafana/api/user/orgs" | python3 -c 'import sys, json; print(json.load(sys.stdin)[0]["role"])' 2>/dev/null)
if [[ "$role" == Admin ]]; then pass "auth.proxy maps role admin -> Grafana Admin"; else fail "Grafana role for admin: $role"; fi
code=$(code_of -b "$JAR" "$BASE/prometheus/-/ready")
if [[ "$code" == 200 ]]; then pass "admin can open /prometheus/"; else fail "admin /prometheus/ -> $code"; fi
: > "$JAR"
curl -s -o /dev/null -c "$JAR" -X POST --data-urlencode "username=$AUTH_VIEWER_USER" \
  --data-urlencode "password=$AUTH_VIEWER_PASSWORD" -d next=/ "$BASE/login"
code=$(code_of -b "$JAR" "$BASE/prometheus/")
if [[ "$code" == 403 ]]; then pass "viewer is denied /prometheus/ (403)"; else fail "viewer /prometheus/ -> $code"; fi
code=$(code_of "$BASE/api/products?limit=1")
if [[ "$code" == 401 ]]; then pass "Order API without token -> 401"; else fail "Order API without token -> $code"; fi
token=$(curl -s -H 'Content-Type: application/json' \
  -d "{\"username\":\"$AUTH_SERVICE_USER\",\"password\":\"$AUTH_SERVICE_PASSWORD\"}" "$BASE/api/token" \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["access_token"])' 2>/dev/null)
code=$(code_of -H "Authorization: Bearer $token" "$BASE/api/products?limit=1")
if [[ "$code" == 200 ]]; then pass "Order API with service token -> 200"; else fail "Order API with token -> $code"; fi
open_ports=""
for port in 3000 3100 3200 4317 4318 5432 9090 12345; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then open_ports="$open_ports $port"; fi
done
if [[ -z "$open_ports" ]]; then pass "internal ports are not published on the host"; else fail "published internal ports:$open_ports"; fi

echo "== Order API and RabbitMQ"
consumers=$(prom_get '/api/v1/query?query=sum(rabbitmq_queue_consumers%7Bqueue%3D%22payments.order-created%22%7D)' | python3 -c '
import sys, json; r = json.load(sys.stdin)["data"]["result"]; print(int(float(r[0]["value"][1])) if r else 0)')
if [[ "$consumers" -gt 0 ]]; then pass "payments queue has $consumers consumer(s)"; else fail "payments queue has no consumers"; fi

echo "== Loki"
curl_net() { docker run --rm --network "${COMPOSE_PROJECT_NAME:-pgmon}_default" curlimages/curl:8.23.0 -s -G "$@"; }
counts=$(curl_net --data-urlencode 'query=sum by (service) (count_over_time({project="pgmon"}[1h]))' \
  http://loki:3100/loki/api/v1/query | python3 -c '
import sys, json
print(" ".join(r["metric"]["service"] for r in json.load(sys.stdin)["data"]["result"] if float(r["value"][1]) > 0))')
missing=""
for svc in $($COMPOSE ps --services); do
  [[ " $counts " == *" $svc "* ]] || missing="$missing $svc"
done
if [[ -z "$missing" ]]; then pass "Loki has logs of every service ($(wc -w <<<"$counts" | tr -d ' '))"; else fail "no logs in Loki for:$missing"; fi

echo "== Tempo"
now=$(date +%s)
trace=$(curl_net --data-urlencode 'q={ resource.service.name = "worker" }' --data-urlencode 'limit=1' \
  --data-urlencode "start=$((now - 600))" --data-urlencode "end=$now" http://tempo:3200/api/search | python3 -c '
import sys, json; t = json.load(sys.stdin).get("traces", []); print(t[0]["traceID"] if t else "")')
if [[ -n "$trace" ]]; then
  services=$(curl_net "http://tempo:3200/api/traces/$trace" | python3 -c '
import sys, json
d = json.load(sys.stdin)
names = {a["value"]["stringValue"] for b in d["batches"] for a in b["resource"]["attributes"] if a["key"] == "service.name"}
print(",".join(sorted(names)))')
  pass "trace $trace passes through worker (services: $services)"
  if [[ "$services" == *load-generator* && "$services" == *order-api* ]]; then
    pass "trace is end-to-end: load-generator -> order-api -> worker"
  else
    fail "trace is not end-to-end: $services"
  fi
else
  fail "no trace through worker in the last 10 minutes"
fi

echo "== Orders"
last_id=$(psql_q "SELECT coalesce(max(id), 0) FROM orders")
sleep 20
created=$(psql_q "SELECT count(*) FROM orders WHERE id > $last_id")
processed=$(psql_q "SELECT count(*) FROM orders WHERE id > $last_id AND status IN ('paid', 'failed')")
if [[ "$created" -gt 0 ]]; then pass "new orders are created ($created in 20s)"; else fail "no new orders in 20s"; fi
if [[ "$processed" -gt 0 ]]; then pass "new orders become paid/failed ($processed of $created)"; else fail "no new orders processed in 20s"; fi

echo
if [[ $FAILED -eq 0 ]]; then echo "Smoke test passed"; else echo "Smoke test FAILED: $FAILED check(s)"; fi
exit $FAILED
