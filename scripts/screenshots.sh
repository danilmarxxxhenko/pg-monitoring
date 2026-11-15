#!/usr/bin/env bash
# Regenerates docs/img/*.png: terminal outputs, Mermaid diagrams and UI
# screenshots taken by headless Chromium (Playwright) through nginx.
# The stack must be running (`make up`). Takes ~12 minutes because chaos
# modes are switched on one after another.
#
# Usage: scripts/screenshots.sh [diagrams] [terminal] [ui]   (default: all)
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=mcr.microsoft.com/playwright/python:v1.65.2-noble
PROJECT=$(grep -E '^COMPOSE_PROJECT_NAME=' .env | cut -d= -f2)
PROJECT=${PROJECT:-pgmon}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p docs/img

want() { [[ $# -eq 0 ]] && return 0; [[ " $ARGS " == *" $1 "* ]]; }
ARGS="$*"

if [[ -z "$ARGS" || " $ARGS " == *" terminal "* ]]; then
  { echo '$ docker compose ps'; docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'; } > "$WORK/fig_compose_ps.txt"
  { echo '$ make smoke'; ./scripts/smoke_test.sh 2>&1; } > "$WORK/fig_smoke.txt"
fi

if [[ -z "$ARGS" || " $ARGS " == *" diagrams "* ]]; then
  # Mermaid blocks of the report: architecture (flowchart) and ER diagram
  python3 - "$WORK" <<'EOF'
import re, sys, pathlib
work = pathlib.Path(sys.argv[1])
def blocks(path):
    return re.findall(r"```mermaid\n(.*?)```", pathlib.Path(path).read_text(), re.S)
report = blocks("docs/report.md")
(work / "fig_architecture.mmd").write_text([b for b in report if b.lstrip().startswith("flowchart")][0])
er = [b for b in report if b.lstrip().startswith("erDiagram")][0]
(work / "fig_er.mmd").write_text(er)
EOF
fi

docker run --rm --network "${PROJECT}_default" --env-file .env \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -v "$PWD/scripts/screenshots.py:/work/screenshots.py:ro" \
  -v "$PWD/grafana/provisioning/dashboards/json:/dash:ro" \
  -v "$WORK:/text:ro" \
  -v "$PWD/docs/img:/out" \
  "$IMAGE" bash -c "pip install -q --break-system-packages playwright==1.65.2 && python /work/screenshots.py $ARGS"
