"""Minimal helpers to build Grafana dashboard JSON from Python.

The generated JSON files are committed under
grafana/provisioning/dashboards/json/ and provisioned by Grafana at start-up,
so Python is needed only when a dashboard is changed.
"""
from __future__ import annotations

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}


def target(expr: str, legend: str = "", ref: str = "A", *, instant: bool = False,
           fmt: str = "time_series", ds: dict = PROM, exemplar: bool = False) -> dict:
    t = {"datasource": ds, "refId": ref, "expr": expr, "legendFormat": legend or "__auto",
         "range": not instant, "instant": instant, "format": fmt}
    if exemplar:
        t["exemplar"] = True
    return t


def loki_target(expr: str, ref: str = "A", legend: str = "", query_type: str = "range") -> dict:
    return {"datasource": LOKI, "refId": ref, "expr": expr, "queryType": query_type,
            "legendFormat": legend}


class Dashboard:
    """Accumulates panels and lays them out on a 24-column grid."""

    def __init__(self, uid: str, title: str, tags: list[str], description: str = "",
                 refresh: str = "10s", time_from: str = "now-30m") -> None:
        self.uid, self.title, self.tags = uid, title, tags
        self.description, self.refresh, self.time_from = description, refresh, time_from
        self.panels: list[dict] = []
        self.variables: list[dict] = []
        self.links: list[dict] = []
        self._x = self._y = self._row_h = 0
        self._id = 0

    # ---------------------------------------------------------------- layout
    def _place(self, w: int, h: int) -> dict:
        if self._x + w > 24:
            self._x, self._y = 0, self._y + self._row_h
            self._row_h = 0
        pos = {"x": self._x, "y": self._y, "w": w, "h": h}
        self._x += w
        self._row_h = max(self._row_h, h)
        return pos

    def row(self, title: str) -> None:
        if self._x:
            self._x, self._y, self._row_h = 0, self._y + self._row_h, 0
        self._id += 1
        self.panels.append({"type": "row", "id": self._id, "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self._y, "w": 24, "h": 1}, "panels": []})
        self._y += 1

    def add(self, panel: dict, w: int, h: int) -> dict:
        self._id += 1
        panel = {"id": self._id, "gridPos": self._place(w, h), **panel}
        self.panels.append(panel)
        return panel

    # ---------------------------------------------------------------- panels
    def timeseries(self, title: str, targets: list[dict], *, unit: str = "short", w: int = 12,
                   h: int = 8, description: str = "", stack: bool = False, min_: float | None = 0,
                   max_: float | None = None, thresholds: list[tuple[str, float | None]] | None = None,
                   overrides: list[dict] | None = None, draw: str = "line", fill: int = 10,
                   ds: dict = PROM) -> dict:
        custom = {"drawStyle": draw, "lineWidth": 1, "fillOpacity": fill, "showPoints": "never",
                  "spanNulls": True, "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                  "axisSoftMin": min_}
        if thresholds:
            custom["thresholdsStyle"] = {"mode": "dashed"}
        defaults = {"unit": unit, "custom": custom,
                    "thresholds": _thresholds(thresholds or [("green", None)])}
        if min_ is not None:
            defaults["min"] = min_
        if max_ is not None:
            defaults["max"] = max_
        return self.add({
            "type": "timeseries", "title": title, "description": description, "datasource": ds,
            "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
        }, w, h)

    def stat(self, title: str, expr: str, *, unit: str = "short", w: int = 4, h: int = 4,
             thresholds: list[tuple[str, float | None]] | None = None, description: str = "",
             decimals: int | None = None, mappings: list[dict] | None = None,
             graph: bool = True, color_mode: str = "value") -> dict:
        defaults = {"unit": unit, "thresholds": _thresholds(thresholds or [("green", None)]),
                    "color": {"mode": "thresholds"}, "mappings": mappings or []}
        if decimals is not None:
            defaults["decimals"] = decimals
        return self.add({
            "type": "stat", "title": title, "description": description, "datasource": PROM,
            "targets": [target(expr, instant=not graph)],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": "area" if graph else "none",
                        "textMode": "auto", "justifyMode": "auto", "orientation": "auto"},
        }, w, h)

    def gauge(self, title: str, expr: str, *, unit: str = "percentunit", w: int = 4, h: int = 4,
              min_: float = 0, max_: float = 1,
              thresholds: list[tuple[str, float | None]] | None = None, description: str = "") -> dict:
        return self.add({
            "type": "gauge", "title": title, "description": description, "datasource": PROM,
            "targets": [target(expr, instant=True)],
            "fieldConfig": {"defaults": {"unit": unit, "min": min_, "max": max_,
                                         "thresholds": _thresholds(thresholds or [("green", None)])},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "showThresholdLabels": False, "showThresholdMarkers": True},
        }, w, h)

    def bargauge(self, title: str, targets: list[dict], *, unit: str = "short", w: int = 12,
                 h: int = 8, description: str = "", ds: dict = PROM) -> dict:
        return self.add({
            "type": "bargauge", "title": title, "description": description, "datasource": ds,
            "targets": targets,
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "color": {"mode": "continuous-BlYlRd"},
                                         "thresholds": _thresholds([("green", None)])},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "orientation": "horizontal", "displayMode": "gradient",
                        "showUnfilled": True, "valueMode": "color", "namePlacement": "left"},
        }, w, h)

    def table(self, title: str, targets: list[dict], *, w: int = 24, h: int = 9,
              transformations: list[dict] | None = None, overrides: list[dict] | None = None,
              description: str = "", unit: str = "short", sort_by: str | None = None,
              ds: dict = PROM) -> dict:
        options = {"showHeader": True, "cellHeight": "sm"}
        if sort_by:
            options["sortBy"] = [{"displayName": sort_by, "desc": True}]
        return self.add({
            "type": "table", "title": title, "description": description, "datasource": ds,
            "targets": targets,
            "fieldConfig": {"defaults": {"unit": unit, "custom": {"align": "auto", "cellOptions": {"type": "auto"}},
                                         "thresholds": _thresholds([("green", None)])},
                            "overrides": overrides or []},
            "options": options,
            "transformations": transformations or [],
        }, w, h)

    def logs(self, title: str, expr: str, *, w: int = 24, h: int = 12, description: str = "") -> dict:
        return self.add({
            "type": "logs", "title": title, "description": description, "datasource": LOKI,
            "targets": [loki_target(expr)],
            "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True,
                        "sortOrder": "Descending", "dedupStrategy": "none", "prettifyLogMessage": False},
        }, w, h)

    def text(self, content: str, *, w: int = 24, h: int = 3, title: str = "") -> dict:
        return self.add({"type": "text", "title": title,
                         "options": {"mode": "markdown", "content": content}}, w, h)

    def custom(self, panel: dict, w: int, h: int) -> dict:
        return self.add(panel, w, h)

    # ------------------------------------------------------------- variables
    def query_var(self, name: str, label: str, query: str, *, default: str | None = None,
                  multi: bool = False, include_all: bool = False) -> None:
        current = {"text": default, "value": default} if default else {}
        self.variables.append({
            "type": "query", "name": name, "label": label, "datasource": PROM,
            "query": {"query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
            "definition": query, "refresh": 2, "sort": 1, "multi": multi, "includeAll": include_all,
            "current": current, "hide": 0,
        })

    def loki_label_var(self, name: str, label: str, loki_label: str, stream: str) -> None:
        self.variables.append({
            "type": "query", "name": name, "label": label, "datasource": LOKI,
            "query": {"label": loki_label, "stream": stream, "type": 1,
                      "refId": "LokiVariableQueryEditor-VariableQuery"},
            "definition": f"label_values({stream}, {loki_label})",
            "refresh": 2, "sort": 1, "multi": True, "includeAll": True, "allValue": ".+",
            "current": {"text": ["All"], "value": ["$__all"]}, "hide": 0,
        })

    def textbox_var(self, name: str, label: str, default: str = "") -> None:
        self.variables.append({"type": "textbox", "name": name, "label": label, "query": default,
                               "current": {"text": default, "value": default}, "hide": 0})

    def link(self, title: str, tags: list[str]) -> None:
        self.links.append({"title": title, "type": "dashboards", "tags": tags, "asDropdown": False,
                           "includeVars": False, "keepTime": True, "targetBlank": False, "icon": "external link"})

    # ---------------------------------------------------------------- output
    def to_json(self) -> dict:
        return {
            "uid": self.uid, "title": self.title, "tags": self.tags, "description": self.description,
            "timezone": "browser", "editable": False, "graphTooltip": 1,
            "refresh": self.refresh, "schemaVersion": 41, "version": 1,
            "time": {"from": self.time_from, "to": "now"},
            "templating": {"list": self.variables},
            "annotations": {"list": [{
                "builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts", "type": "dashboard"}]},
            "links": self.links,
            "panels": self.panels,
        }

    def write(self, directory: Path, filename: str) -> None:
        path = directory / filename
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")


def _thresholds(steps: list[tuple[str, float | None]]) -> dict:
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def mapping_up_down() -> list[dict]:
    return [{"type": "value", "options": {
        "0": {"text": "DOWN", "color": "red", "index": 0},
        "1": {"text": "UP", "color": "green", "index": 1}}}]
