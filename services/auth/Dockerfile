FROM python:3.12.16-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv
COPY auth/requirements.txt .
RUN pip install -r requirements.txt

COPY common ./common
COPY auth/app ./app

# SQLite database lives on a volume mounted at /data
RUN useradd --system --no-create-home app && mkdir -p /data && chown app /data
USER app
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=15s \
    CMD ["python", "-m", "common.healthcheck", "http://127.0.0.1:8000/health"]
CMD ["python", "-m", "app.main"]
