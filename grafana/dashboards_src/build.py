#!/usr/bin/env python3
"""Generate provisioned Grafana dashboards.

Usage: python3 grafana/dashboards_src/build.py
"""
from __future__ import annotations

from pathlib import Path

from lib import Dashboard, mapping_up_down, target

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
            thresholds=[("green", None), ("orange", 0.6), ("red", 0.8)], w=3,
            description="Усі backend-и відносно max_connections")
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    postgres_dashboard().write(OUT, "postgres.json")


if __name__ == "__main__":
    main()
