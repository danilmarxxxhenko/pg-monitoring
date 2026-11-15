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

echo "== Order API and RabbitMQ"
http_code() { docker run --rm --network "${COMPOSE_PROJECT_NAME:-pgmon}_default" curlimages/curl:8.23.0 -s -o /dev/null -w '%{http_code}' "$@"; }
code=$(http_code "http://order-api:8000/products?limit=1")
if [[ "$code" == 200 ]]; then pass "order-api GET /products -> 200"; else fail "order-api GET /products -> $code"; fi
consumers=$(prom_get '/api/v1/query?query=sum(rabbitmq_queue_consumers%7Bqueue%3D%22payments.order-created%22%7D)' | python3 -c '
import sys, json; r = json.load(sys.stdin)["data"]["result"]; print(int(float(r[0]["value"][1])) if r else 0)')
if [[ "$consumers" -gt 0 ]]; then pass "payments queue has $consumers consumer(s)"; else fail "payments queue has no consumers"; fi

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
