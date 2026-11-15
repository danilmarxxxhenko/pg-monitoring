#!/usr/bin/env python3
"""Generate provisioned Grafana dashboards.

Usage: python3 grafana/dashboards_src/build.py
"""
from __future__ import annotations

import json
from pathlib import Path

from lib import LOKI, PROM, TEMPO, Dashboard, loki_target, mapping_up_down, target

OUT = Path(__file__).resolve().parent.parent / "provisioning" / "dashboards" / "json"
DB = 'datname="$db"'


def postgres_dashboard() -> Dashboard:
    d = Dashboard("pg-overview", "СУБД PostgreSQL", ["pgmon", "postgres"],
                  description="Продуктивність PostgreSQL сервісу обробки замовлень (postgres-exporter + pg_stat_statements)")
    d.query_var("db", "База даних", "label_values(pg_database_size_bytes, datname)", default="shop")
    d.link("Інші дашборди", ["pgmon"])

    # ----------------------------------------------------------- overview
    d.row("Огляд")
    d.stat("Стан СУБД", "pg_up", mappings=mapping_up_down(), graph=False, color_mode="background",
           thresholds=[("red", None), ("green", 1)], w=3)
    d.stat("Розмір БД", f"pg_database_size_bytes{{{DB}}}", unit="bytes", w=3)
    d.gauge("Використання з'єднань",
            "sum(pg_stat_activity_count) / max(pg_settings_max_connections)",
            thresholds=[("green", None), ("orange", 0.4), ("red", 0.5)], w=3,
            description="Усі backend-и відносно max_connections; алерт — понад 50%")
    d.gauge("Cache hit ratio",
            f"sum(rate(pg_stat_database_blks_hit{{{DB}}}[5m])) / "
            f"(sum(rate(pg_stat_database_blks_hit{{{DB}}}[5m])) + sum(rate(pg_stat_database_blks_read{{{DB}}}[5m])))",
            min_=0.8, max_=1, thresholds=[("red", None), ("orange", 0.9), ("green", 0.99)], w=3,
            description="Частка читань блоків із shared_buffers (без звернення до диска)")
    d.stat("TPS", f"sum(rate(pg_stat_database_xact_commit{{{DB}}}[1m]) + rate(pg_stat_database_xact_rollback{{{DB}}}[1m]))",
           unit="ops", decimals=1, w=3)
    d.stat("Deadlocks за період", f"sum(increase(pg_stat_database_deadlocks{{{DB}}}[$__range]))",
           thresholds=[("green", None), ("red", 1)], decimals=0, graph=False, w=3)
    d.stat("Найдовша транзакція", f"max(pg_stat_activity_max_tx_duration{{{DB}}})", unit="s",
           thresholds=[("green", None), ("orange", 5), ("red", 30)], decimals=1, w=3)
    d.stat("Очікують на блокування",
           f'sum(pg_stat_activity_count{{{DB},wait_event_type="Lock"}}) or vector(0)',
           thresholds=[("green", None), ("orange", 1), ("red", 5)], decimals=0, w=3,
           description="Сесії в стані очікування lock (wait_event_type = Lock)")

    # ---------------------------------------------------- connections & tx
    d.row("З'єднання та транзакції")
    d.timeseries("З'єднання за станами", [
        target(f'sum by (state) (pg_stat_activity_count{{{DB}}})', "{{state}}"),
        target("max(pg_settings_max_connections)", "max_connections", "B"),
    ], stack=False, description="pg_stat_activity, згруповано за state",
        overrides=[{"matcher": {"id": "byName", "options": "max_connections"},
                    "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                                   {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
                                   {"id": "custom.fillOpacity", "value": 0}]}])
    d.timeseries("З'єднання за користувачами БД", [
        target(f'sum by (usename) (pg_stat_activity_count{{{DB},usename!=""}})', "{{usename}}"),
    ], stack=True)
    d.timeseries("Транзакції: commit / rollback", [
        target(f"sum(rate(pg_stat_database_xact_commit{{{DB}}}[1m]))", "commit"),
        target(f"sum(rate(pg_stat_database_xact_rollback{{{DB}}}[1m]))", "rollback", "B"),
    ], unit="ops", overrides=[{"matcher": {"id": "byName", "options": "rollback"},
                               "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}])
    d.timeseries("Cache hit ratio", [
        target(f"sum(rate(pg_stat_database_blks_hit{{{DB}}}[1m])) / "
               f"(sum(rate(pg_stat_database_blks_hit{{{DB}}}[1m])) + sum(rate(pg_stat_database_blks_read{{{DB}}}[1m])))",
               "hit ratio"),
    ], unit="percentunit", min_=None, max_=1)

    # --------------------------------------------------------------- locks
    d.row("Блокування")
    d.timeseries("Блокування за режимами", [
        target(f'sum by (mode) (pg_locks_count{{{DB}}}) > 0', "{{mode}}"),
    ], stack=True, w=8, description="pg_locks: кількість утримуваних і очікуваних блокувань")
    d.timeseries("Очікування (wait events) активних сесій", [
        target(f'sum by (wait_event_type) (pg_stat_activity_count{{{DB},state="active",wait_event_type!=""}})',
               "{{wait_event_type}}"),
    ], stack=True, w=8)
    d.timeseries("Deadlocks", [
        target(f"sum(increase(pg_stat_database_deadlocks{{{DB}}}[1m]))", "deadlocks / хв"),
    ], w=8, draw="bars", fill=80, thresholds=[("transparent", None), ("red", 1)])
    d.timeseries("Тривалість найдовшої транзакції", [
        target(f'max by (state) (pg_stat_activity_max_tx_duration{{{DB},state!~"disabled|fastpath function call"}})', "{{state}}"),
    ], unit="s", w=12)
    d.timeseries("Довгі транзакції (> 60 с)", [
        target("pg_long_running_transactions", "транзакцій"),
    ], w=12)

    # ----------------------------------------------------------- row ops
    d.row("Операції з рядками та таблиці")
    d.timeseries("Рядки: insert / update / delete", [
        target(f"sum(rate(pg_stat_database_tup_inserted{{{DB}}}[1m]))", "inserted"),
        target(f"sum(rate(pg_stat_database_tup_updated{{{DB}}}[1m]))", "updated", "B"),
        target(f"sum(rate(pg_stat_database_tup_deleted{{{DB}}}[1m]))", "deleted", "C"),
    ], unit="rowsps", w=8)
    d.timeseries("Рядки: returned / fetched", [
        target(f"sum(rate(pg_stat_database_tup_returned{{{DB}}}[1m]))", "returned"),
        target(f"sum(rate(pg_stat_database_tup_fetched{{{DB}}}[1m]))", "fetched", "B"),
    ], unit="rowsps", w=8)
    d.timeseries("Seq scan vs index scan", [
        target(f"sum by (relname) (rate(pg_stat_user_tables_seq_scan{{{DB}}}[1m]))", "seq {{relname}}"),
        target(f"sum by (relname) (rate(pg_stat_user_tables_idx_scan{{{DB}}}[1m]))", "idx {{relname}}", "B"),
    ], unit="ops", w=8)
    d.bargauge("Розмір таблиць (дані + індекси)", [
        target(f"sum by (relname) (pg_stat_user_tables_table_size_bytes{{{DB}}} + pg_stat_user_tables_index_size_bytes{{{DB}}})",
               "{{relname}}", instant=True),
    ], unit="bytes", w=8)
    d.timeseries("Розмір БД", [target(f"pg_database_size_bytes{{{DB}}}", "{{datname}}")], unit="bytes",
                 min_=None, w=8)
    d.timeseries("Живі / мертві кортежі", [
        target(f"sum by (relname) (pg_stat_user_tables_n_dead_tup{{{DB}}})", "dead {{relname}}"),
    ], w=8, description="Мертві кортежі прибирає autovacuum; ріст сигналізує про проблеми з vacuum")

    # ---------------------------------------------------- statements
    d.row("Запити (pg_stat_statements)")
    join = " * on (queryid) group_left(query) max by (queryid, query) (pg_stat_statements_query_id)"
    sel = f'{DB},user!="exporter"'
    d.table("Топ-10 повільних запитів (середній час виконання, 5 хв)", [
        target(f"topk(10, (sum by (queryid, user) (rate(pg_stat_statements_seconds_total{{{sel}}}[5m])) / "
               f"sum by (queryid, user) (rate(pg_stat_statements_calls_total{{{sel}}}[5m]) > 0)){join})",
               ref="A", instant=True, fmt="table"),
        target(f"sum by (queryid, user) (rate(pg_stat_statements_calls_total{{{sel}}}[5m])){join}",
               ref="B", instant=True, fmt="table"),
        target(f"sum by (queryid, user) (rate(pg_stat_statements_seconds_total{{{sel}}}[5m])){join}",
               ref="C", instant=True, fmt="table"),
    ], h=10, sort_by="Середній час",
        transformations=[
            # Rows with identical labels (queryid, user, query) are merged into one row
            {"id": "merge", "options": {}},
            # B and C are not limited by topk: keep only rows present in A
            {"id": "filterByValue", "options": {"filters": [{"fieldName": "Value #A", "config": {"id": "isNull"}}],
                                                "type": "exclude", "match": "any"}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True},
                "indexByName": {"Value #A": 0, "Value #B": 1, "Value #C": 2, "user": 3, "queryid": 4, "query": 5},
                "renameByName": {"Value #A": "Середній час", "Value #B": "Викликів/с",
                                 "Value #C": "Час у БД (с/с)", "user": "Роль", "query": "Запит"}}},
        ],
        overrides=[
            {"matcher": {"id": "byName", "options": "Середній час"},
             "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 4},
                            {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "gradient"}},
                            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                                {"color": "green", "value": None}, {"color": "orange", "value": 0.05},
                                {"color": "red", "value": 0.5}]}},
                            {"id": "custom.width", "value": 120}]},
            {"matcher": {"id": "byName", "options": "Викликів/с"},
             "properties": [{"id": "unit", "value": "ops"}, {"id": "decimals", "value": 2},
                            {"id": "custom.width", "value": 100}]},
            {"matcher": {"id": "byName", "options": "Час у БД (с/с)"},
             "properties": [{"id": "decimals", "value": 4}, {"id": "custom.width", "value": 120}]},
            {"matcher": {"id": "byName", "options": "Роль"}, "properties": [{"id": "custom.width", "value": 90}]},
            {"matcher": {"id": "byName", "options": "queryid"}, "properties": [{"id": "custom.width", "value": 170}]},
        ])
    d.timeseries("Час виконання запитів у БД за ролями", [
        target(f"sum by (user) (rate(pg_stat_statements_seconds_total{{{sel}}}[1m]))", "{{user}}"),
    ], unit="s", stack=True, description="Сумарний час виконання запитів за секунду (≈ кількість зайнятих backend-ів)")
    d.timeseries("Запитів за секунду за ролями", [
        target(f"sum by (user) (rate(pg_stat_statements_calls_total{{{sel}}}[1m]))", "{{user}}"),
    ], unit="ops", stack=True)
    return d


API = 'job="order-api",route!~"/metrics|/health"'
Q = 'queue="payments.order-created"'
DLQ = 'queue="payments.order-created.dlq"'


def _quantile(q: float, by: str = "") -> str:
    group = f"le{', ' + by if by else ''}"
    return f"histogram_quantile({q}, sum by ({group}) (rate(http_request_duration_seconds_bucket{{{API}}}[1m])))"


def application_dashboard() -> Dashboard:
    d = Dashboard("app-overview", "Застосунок", ["pgmon", "application"],
                  description="Order API, worker оплат і черга RabbitMQ")
    d.link("Інші дашборди", ["pgmon"])

    d.row("Огляд")
    d.stat("RPS", f"sum(rate(http_request_duration_seconds_count{{{API}}}[1m]))", unit="reqps", decimals=1, w=4)
    d.stat("p95 latency", _quantile(0.95), unit="s", decimals=3, w=4,
           thresholds=[("green", None), ("orange", 0.3), ("red", 0.5)])
    d.stat("Частка помилок 5xx",
           f'sum(rate(http_request_duration_seconds_count{{{API},status=~"5.."}}[5m])) / '
           f"sum(rate(http_request_duration_seconds_count{{{API}}}[5m])) or vector(0)",
           unit="percentunit", decimals=2, w=4, thresholds=[("green", None), ("orange", 0.01), ("red", 0.05)])
    d.stat("Замовлень за хвилину", "sum(rate(orders_created_total[5m])) * 60", decimals=1, w=4)
    d.stat("Успішних оплат",
           'sum(rate(orders_processed_total{status="paid"}[5m])) / '
           'sum(rate(orders_processed_total{status=~"paid|failed"}[5m]))',
           unit="percentunit", decimals=1, w=4, thresholds=[("red", None), ("orange", 0.8), ("green", 0.88)])
    d.stat("Черга оплат", f"sum(rabbitmq_queue_messages{{{Q}}})", decimals=0, w=4,
           thresholds=[("green", None), ("orange", 20), ("red", 100)],
           description="Повідомлення order.created, що очікують або обробляються (ready + unacked)")

    d.row("HTTP: Order API")
    d.timeseries("Запити за секунду за маршрутами", [
        target(f"sum by (method, route) (rate(http_request_duration_seconds_count{{{API}}}[1m]))",
               "{{method}} {{route}}"),
    ], unit="reqps", stack=True)
    d.timeseries("Латентність p50 / p95 / p99 (з exemplars)", [
        target(_quantile(0.50), "p50", "A"),
        target(_quantile(0.95), "p95", "B", exemplar=True),
        target(_quantile(0.99), "p99", "C"),
    ], unit="s", description="Ромбики-exemplars ведуть до трейсу конкретного запиту в Tempo")
    d.timeseries("p95 за маршрутами", [
        target(_quantile(0.95, "method, route"), "{{method}} {{route}}"),
    ], unit="s", w=8)
    d.timeseries("Коди відповідей", [
        target(f"sum by (status) (rate(http_request_duration_seconds_count{{{API}}}[1m]))", "{{status}}"),
    ], unit="reqps", stack=True, w=8, overrides=[
        {"matcher": {"id": "byRegexp", "options": "5.."},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
        {"matcher": {"id": "byRegexp", "options": "4.."},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
        {"matcher": {"id": "byRegexp", "options": "2.."},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}])
    d.timeseries("Частка помилок (4xx / 5xx)", [
        target(f'(sum(rate(http_request_duration_seconds_count{{{API},status=~"4.."}}[1m])) or vector(0)) / '
               f"sum(rate(http_request_duration_seconds_count{{{API}}}[1m]))", "4xx"),
        target(f'(sum(rate(http_request_duration_seconds_count{{{API},status=~"5.."}}[1m])) or vector(0)) / '
               f"sum(rate(http_request_duration_seconds_count{{{API}}}[1m]))", "5xx", "B"),
    ], unit="percentunit", w=8)

    d.row("Замовлення та оплати")
    d.timeseries("Замовлення: створені / відхилені", [
        target("sum(rate(orders_created_total[1m])) * 60", "створено"),
        target("sum by (reason) (rate(orders_rejected_total[1m])) * 60", "відхилено: {{reason}}", "B"),
        target("sum(rate(orders_republished_total[1m])) * 60", "повторно опубліковано", "C"),
    ], unit="short", description="Кількість за хвилину")
    d.timeseries("Оброблені worker-ом за результатом", [
        target("sum by (status) (rate(orders_processed_total[1m])) * 60", "{{status}}"),
    ], stack=True, description="Кількість за хвилину", overrides=[
        {"matcher": {"id": "byName", "options": "failed"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
        {"matcher": {"id": "byName", "options": "paid"},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}])
    d.timeseries("Час обробки оплати (p50 / p95)", [
        target("histogram_quantile(0.5, sum by (le) (rate(payment_processing_duration_seconds_bucket[5m])))", "p50"),
        target("histogram_quantile(0.95, sum by (le) (rate(payment_processing_duration_seconds_bucket[5m])))",
               "p95", "B", exemplar=True),
    ], unit="s", w=8)
    d.timeseries("Тривалість операцій з БД (p95)", [
        target('histogram_quantile(0.95, sum by (le, job, operation) (rate(db_query_duration_seconds_bucket{job=~"order-api|worker"}[1m])))',
               "{{job}}: {{operation}}"),
    ], unit="s", w=8, description="db_query_duration_seconds{operation}")
    d.timeseries("Повідомлення в обробці (worker)", [
        target("sum(worker_messages_in_flight)", "in flight"),
        target("max(worker_payment_failure_rate)", "ймовірність відмови", "B"),
    ], w=8, overrides=[{"matcher": {"id": "byName", "options": "ймовірність відмови"},
                        "properties": [{"id": "custom.axisPlacement", "value": "right"},
                                       {"id": "unit", "value": "percentunit"}]}])

    d.row("RabbitMQ")
    d.timeseries("Довжина черги оплат", [
        target(f"sum(rabbitmq_queue_messages_ready{{{Q}}})", "ready"),
        target(f"sum(rabbitmq_queue_messages_unacked{{{Q}}})", "unacked", "B"),
    ], stack=True, w=8)
    d.timeseries("Dead-letter queue", [
        target(f"sum(rabbitmq_queue_messages{{{DLQ}}})", "у DLQ"),
    ], w=8, thresholds=[("transparent", None), ("red", 1)])
    d.timeseries("Потік повідомлень", [
        target('sum(rate(rabbitmq_global_messages_received_total{protocol="amqp091"}[1m]))', "отримано брокером"),
        target('sum(rate(rabbitmq_global_messages_acknowledged_total{protocol="amqp091"}[1m]))', "підтверджено (ack)", "B"),
        target(f"sum(rabbitmq_queue_consumers{{{Q}}})", "consumers", "C"),
    ], unit="short", w=8, overrides=[{"matcher": {"id": "byName", "options": "consumers"},
                                      "properties": [{"id": "custom.axisPlacement", "value": "right"},
                                                     {"id": "custom.drawStyle", "value": "line"}]}])

    d.row("Генератор навантаження")
    d.timeseries("Операції покупців за результатом", [
        target('sum by (result) (rate(loadgen_operations_total{operation!~"job_.*"}[1m]))', "{{result}}"),
    ], unit="ops", stack=True, w=8)
    d.timeseries("Латентність на боці клієнта (p95)", [
        target("histogram_quantile(0.95, sum by (le, operation) (rate(loadgen_request_duration_seconds_bucket[1m])))",
               "{{operation}}"),
    ], unit="s", w=8)
    d.timeseries("Цільовий RPS і режим хаосу", [
        target("max(loadgen_target_rps)", "target RPS"),
        target("max by (mode) (loadgen_chaos_mode{mode!=\"off\"}) > 0", "хаос: {{mode}}", "B"),
    ], w=8)
    return d


LOGS = '{project="pgmon", service=~"$service"}'


def tempo_traceql(query: str, ref: str = "A", limit: int = 20) -> dict:
    return {"datasource": TEMPO, "refId": ref, "queryType": "traceql", "query": query,
            "limit": limit, "tableType": "traces"}


def logs_traces_dashboard() -> Dashboard:
    d = Dashboard("logs-traces", "Логи і трейси", ["pgmon", "logs", "traces"],
                  description="Loki (логи всіх контейнерів) і Tempo (розподілені трейси) з переходами між ними",
                  time_from="now-15m")
    d.loki_label_var("service", "Сервіс", "service", '{project="pgmon"}')
    d.loki_label_var("level", "Рівень", "level", '{project="pgmon"}')
    d.textbox_var("search", "Пошук (текст / trace_id)")
    d.link("Інші дашборди", ["pgmon"])

    d.text("**Як користуватися.** У рядку логу натисніть **trace_id → Відкрити трейс у Tempo**. "
           "У трейсі кнопка **Logs for this span** відкриває логи цього трейсу. "
           "На дашборді «Застосунок» ромбики-exemplars на графіку латентності ведуть до конкретного трейсу.",
           h=2)

    d.row("Логи (Loki)")
    d.timeseries("Обсяг логів за сервісами", [
        loki_target(f'sum by (service) (count_over_time({LOGS} [1m]))', legend="{{service}}"),
    ], ds=LOKI, stack=True, draw="bars", fill=80, w=12, description="Рядків логу за хвилину")
    d.timeseries("Помилки та попередження", [
        loki_target('sum by (service, level) (count_over_time({project="pgmon", service=~"$service", '
                    'level=~"error|critical|warning"} [1m]))', legend="{{service}} {{level}}"),
    ], ds=LOKI, stack=True, draw="bars", fill=80, w=12)
    d.logs("Логи", f'{{project="pgmon", service=~"$service", level=~"$level"}} |~ "(?i)$search"', h=14)
    d.logs("Останні помилки", '{project="pgmon", service=~"$service", level=~"error|critical"}', h=8)

    d.row("Трейси (Tempo)")
    d.custom({"type": "nodeGraph", "title": "Карта сервісів (service graph)", "datasource": TEMPO,
              "description": "Будується metrics generator-ом Tempo з трейсів (метрики traces_service_graph_* у Prometheus)",
              "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "serviceMap"}],
              "options": {}}, w=12, h=12)
    d.timeseries("Запити між сервісами (з трейсів)", [
        target("sum by (client, server) (rate(traces_service_graph_request_total[1m]))", "{{client}} → {{server}}"),
    ], unit="reqps", w=12, h=12)
    trace_overrides = [{"matcher": {"id": "byName", "options": "Trace ID"},
                        "properties": [{"id": "custom.width", "value": 280}]}]
    d.table("Трейси, що пройшли через worker (оплата)",
            [tempo_traceql('{ resource.service.name = "worker" && name = "payment.gateway" }')],
            ds=TEMPO, w=12, h=9, overrides=trace_overrides)
    d.table("Трейси з помилками",
            [tempo_traceql("{ status = error }")], ds=TEMPO, w=12, h=9, overrides=trace_overrides)
    d.table("Повільні запити Order API (> 250 мс)",
            [tempo_traceql('{ resource.service.name = "order-api" && kind = server && duration > 250ms }')],
            ds=TEMPO, w=12, h=9, overrides=trace_overrides)
    d.table("Повільні SQL-запити (> 100 мс)",
            [tempo_traceql('{ span.db.system = "postgresql" && duration > 100ms }')],
            ds=TEMPO, w=12, h=9, overrides=trace_overrides)

    d.row("Метрики зі спанів (span metrics)")
    d.timeseries("Частота спанів за сервісами (server / consumer)", [
        target('sum by (service) (rate(traces_spanmetrics_calls_total{span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"}[1m]))',
               "{{service}}"),
    ], unit="ops", w=8)
    d.timeseries("p95 тривалості спанів за операціями", [
        target('histogram_quantile(0.95, sum by (le, service, span_name) (rate(traces_spanmetrics_latency_bucket'
               '{span_kind=~"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER"}[5m])))', "{{service}}: {{span_name}}"),
    ], unit="s", w=8)
    d.timeseries("Частка спанів з помилкою", [
        target('sum by (service) (rate(traces_spanmetrics_calls_total{status_code="STATUS_CODE_ERROR"}[5m])) / '
               'sum by (service) (rate(traces_spanmetrics_calls_total[5m]))', "{{service}}"),
    ], unit="percentunit", w=8)
    return d


def security_dashboard() -> Dashboard:
    d = Dashboard("security", "Безпека", ["pgmon", "security"],
                  description="Автентифікація та авторизація: власний auth-сервіс (FastAPI + SQLite) і nginx auth_request",
                  time_from="now-1h")
    d.link("Інші дашборди", ["pgmon"])
    rng = "[$__range]"

    d.row("Огляд за період")
    d.stat("Успішні входи", f'sum(increase(auth_login_total{{result="success"}}{rng})) or vector(0)',
           decimals=0, graph=False, w=4)
    d.stat("Невдалі входи", f'sum(increase(auth_login_total{{result!="success"}}{rng})) or vector(0)',
           decimals=0, graph=False, w=4, thresholds=[("green", None), ("orange", 5), ("red", 20)])
    d.stat("Заблоковано (throttling)", f'sum(increase(auth_login_total{{result="throttled"}}{rng})) or vector(0)',
           decimals=0, graph=False, w=4, thresholds=[("green", None), ("red", 1)],
           description="Спроби після 10 невдалих входів за 5 хв з тієї ж IP для того ж логіна (HTTP 429)")
    d.stat("Відмови 401 (немає / недійсна сесія)",
           f'sum(increase(auth_denied_total{{reason=~"missing|invalid|expired|revoked|inactive"}}{rng})) or vector(0)',
           decimals=0, graph=False, w=4)
    d.stat("Відмови 403 (недостатньо прав)", f'sum(increase(auth_denied_total{{reason="forbidden"}}{rng})) or vector(0)',
           decimals=0, graph=False, w=4, thresholds=[("green", None), ("orange", 1)])
    d.custom({"type": "piechart", "title": "Користувачі за ролями", "datasource": PROM,
              "targets": [target("sum by (role) (auth_users)", "{{role}}", instant=True)],
              "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
              "options": {"pieType": "donut", "legend": {"displayMode": "list", "placement": "right", "values": ["value"]},
                          "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}, w=4, h=4)

    d.row("Автентифікація")
    d.timeseries("Спроби входу за результатом", [
        target("sum by (method, result) (rate(auth_login_total[1m])) * 60", "{{method}}: {{result}}"),
    ], stack=True, draw="bars", fill=80, description="Кількість за хвилину; method = form (браузер) або api (сервіси)",
        overrides=[{"matcher": {"id": "byRegexp", "options": ".*success"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]},
                   {"matcher": {"id": "byRegexp", "options": ".*bad_credentials"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                   {"matcher": {"id": "byRegexp", "options": ".*throttled"},
                    "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "purple"}}]}])
    d.timeseries("Перевірки сесій (/verify)", [
        target("sum by (scope, result) (rate(auth_verify_total[1m]))", "{{scope}}: {{result}}"),
    ], unit="ops", stack=True,
        description="scope=any — order-api; grafana — вхід у Grafana через nginx; admin — /prometheus/ і /chaos")
    d.timeseries("Відмови в доступі за причинами", [
        target("sum by (reason) (rate(auth_denied_total[1m])) * 60", "{{reason}}"),
    ], stack=True, w=8, description="Кількість за хвилину")
    d.timeseries("HTTP 401 / 403 / 429 на вході (nginx)", [
        loki_target('sum by (status) (count_over_time({service="nginx"} | json | status=~"401|403|429" [1m]))',
                    legend="{{status}}"),
    ], ds=LOKI, stack=True, draw="bars", fill=80, w=8, description="З access-логів nginx у Loki")
    d.timeseries("401 від Order API (без / з недійсним токеном)", [
        target('sum(rate(http_request_duration_seconds_count{job="order-api",status="401"}[1m])) * 60 or vector(0)', "401"),
    ], w=8)

    d.row("Аналіз атак")
    d.bargauge("Топ IP за невдалими входами", [
        loki_target(f'topk(10, sum by (client_ip) (count_over_time({{service="auth"}} | json | event="login_failed" {rng})))',
                    legend="{{client_ip}}", query_type="instant"),
    ], ds=LOKI, w=8, h=8)
    d.bargauge("Топ логінів, які намагаються підібрати", [
        loki_target(f'topk(10, sum by (username) (count_over_time({{service="auth"}} | json | event="login_failed" {rng})))',
                    legend="{{username}}", query_type="instant"),
    ], ds=LOKI, w=8, h=8)
    d.timeseries("Навантаження на вході (nginx)", [
        target("sum(rate(nginx_http_requests_total[1m]))", "запитів/с"),
        target("sum(nginx_connections_active)", "активні з'єднання", "B"),
    ], w=8, h=8)
    d.logs("Події безпеки (auth)",
           '{service="auth"} | json | event=~"login_failed|login_success|access_forbidden|logout|user_.*"', h=10)
    d.logs("Відмови на вході (nginx 401/403/429)", '{service="nginx"} | json | status=~"401|403|429"', h=8)
    return d


ALERTS_OUT = Path(__file__).resolve().parent.parent / "provisioning" / "alerting" / "rules.yml"
API_ROUTES = 'job="order-api",route!~"/metrics|/health"'
PAY_Q = 'queue="payments.order-created"'


def panel_id(d: Dashboard, title: str) -> str:
    for p in d.panels:
        if p.get("title") == title:
            return str(p["id"])
    raise KeyError(f"panel {title!r} not found in {d.uid}")


def _rule(uid: str, title: str, expr: str, op: str, threshold: float, *, for_: str, severity: str,
          summary: str, description: str, dash: Dashboard, panel: str, window_s: int = 600) -> dict:
    return {
        "uid": uid, "title": title, "condition": "C", "for": for_, "isPaused": False,
        "noDataState": "OK", "execErrState": "Error",
        "labels": {"severity": severity, "stack": "pgmon"},
        "annotations": {"summary": summary, "description": description,
                        "__dashboardUid__": dash.uid, "__panelId__": panel_id(dash, panel)},
        "data": [
            {"refId": "A", "relativeTimeRange": {"from": window_s, "to": 0}, "datasourceUid": "prometheus",
             "model": {"refId": "A", "expr": expr, "instant": True, "range": False,
                       "intervalMs": 15000, "maxDataPoints": 43200}},
            {"refId": "B", "datasourceUid": "__expr__",
             "model": {"refId": "B", "type": "reduce", "expression": "A", "reducer": "last",
                       "settings": {"mode": "dropNN"}}},
            {"refId": "C", "datasourceUid": "__expr__",
             "model": {"refId": "C", "type": "threshold", "expression": "B",
                       "conditions": [{"evaluator": {"type": op, "params": [threshold]}}]}},
        ],
    }


def alert_rules(pg: Dashboard, app: Dashboard, sec: Dashboard) -> dict:
    rules = [
        _rule("pg-connections-high", "Забагато з'єднань із БД",
              "sum(pg_stat_activity_count) / max(pg_settings_max_connections)", "gt", 0.5,
              for_="1m", severity="warning", dash=pg, panel="З'єднання за станами",
              summary="Зайнято більше половини max_connections PostgreSQL",
              description="Використано {{ humanizePercentage $values.B.Value }} з'єднань. Перевірте блокування "
                          "та сесії idle in transaction на дашборді «СУБД PostgreSQL»."),
        _rule("pg-deadlocks", "Виявлено deadlock у PostgreSQL",
              'sum(increase(pg_stat_database_deadlocks{datname="shop"}[2m]))', "gt", 0,
              for_="0s", severity="warning", dash=pg, panel="Deadlocks",
              summary="PostgreSQL перервав транзакції через взаємне блокування",
              description="За 2 хв зафіксовано {{ $values.B.Value }} deadlock(ів)."),
        _rule("api-p95-latency", "p95 латентності Order API вище порогу",
              f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{API_ROUTES}}}[2m])))",
              "gt", 0.5, for_="1m", severity="critical", dash=app, panel="Латентність p50 / p95 / p99 (з exemplars)",
              summary="p95 латентності Order API перевищує 500 мс",
              description="Поточний p95 = {{ humanizeDuration $values.B.Value }}. Відкрийте exemplar на графіку "
                          "латентності, щоб перейти до повільного трейсу."),
        _rule("payment-queue-growing", "Черга оплат зростає",
              f"sum(rabbitmq_queue_messages{{{PAY_Q}}}) and on() (sum(deriv(rabbitmq_queue_messages{{{PAY_Q}}}[2m])) > 0)",
              "gt", 30, for_="1m", severity="warning", dash=app, panel="Довжина черги оплат",
              summary="Worker не встигає обробляти order.created",
              description="У черзі {{ $values.B.Value }} повідомлень і вона продовжує зростати."),
        _rule("payment-failure-ratio", "Висока частка невдалих оплат",
              'sum(rate(orders_processed_total{status="failed"}[2m])) / '
              'sum(rate(orders_processed_total{status=~"paid|failed"}[2m]))',
              "gt", 0.3, for_="1m", severity="warning", dash=app, panel="Оброблені worker-ом за результатом",
              summary="Понад 30% оплат відхилено",
              description="Частка невдалих оплат: {{ humanizePercentage $values.B.Value }}."),
        _rule("failed-logins-spike", "Сплеск невдалих спроб входу",
              'sum(rate(auth_login_total{result!="success"}[1m])) * 60', "gt", 20,
              for_="30s", severity="critical", dash=sec, panel="Спроби входу за результатом",
              summary="Можливий підбір пароля (brute force)",
              description="{{ humanize $values.B.Value }} невдалих входів за хвилину. Джерела — на дашборді «Безпека»."),
        _rule("scrape-target-down", "Ціль моніторингу недоступна",
              "up", "lt", 1, for_="1m", severity="critical", dash=app, panel="RPS",
              summary="Prometheus не може зібрати метрики з {{ $labels.job }}",
              description="Target {{ $labels.instance }} (job {{ $labels.job }}) не відповідає понад 1 хв."),
    ]
    return {"apiVersion": 1, "groups": [{"orgId": 1, "name": "pgmon", "folder": "PG Monitoring",
                                         "interval": "20s", "rules": rules}]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pg, app, logs, sec = postgres_dashboard(), application_dashboard(), logs_traces_dashboard(), security_dashboard()
    pg.write(OUT, "postgres.json")
    app.write(OUT, "application.json")
    logs.write(OUT, "logs-traces.json")
    sec.write(OUT, "security.json")
    # JSON is valid YAML; Grafana reads *.yml provisioning files
    ALERTS_OUT.write_text("# Generated by grafana/dashboards_src/build.py - do not edit by hand\n"
                          + json.dumps(alert_rules(pg, app, sec), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(f"wrote {ALERTS_OUT}")


if __name__ == "__main__":
    main()
