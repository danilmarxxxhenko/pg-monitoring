# Система моніторингу продуктивності СУБД PostgreSQL

Курсова робота з дисципліни «Інформаційні системи»: моніторинг продуктивності PostgreSQL сервісу обробки замовлень інтернет-магазину.

> Проєкт у розробці. Повна документація (архітектура, таблиця відповідності вимогам, сценарій демонстрації) з'явиться на етапі 5.

## Швидкий старт

Потрібен лише Docker (Compose v2) і ~4 ГБ вільної пам'яті.

```bash
make up            # створює .env з випадковими секретами і запускає стек
```

Без `make`:

```bash
./scripts/init_env.sh     # або: cp .env.example .env (і змінити паролі)
docker compose up -d
```

Перевірка: `make smoke`.

Відкрийте http://localhost/ — без сесії nginx перенаправить на сторінку входу.

| Обліковий запис | Роль | Доступ |
|---|---|---|
| `AUTH_ADMIN_USER` / `AUTH_ADMIN_PASSWORD` з `.env` | admin | Grafana (Admin + Server Admin), `/prometheus/`, `/chaos`, CRUD користувачів |
| `AUTH_VIEWER_USER` / `AUTH_VIEWER_PASSWORD` | viewer | Grafana (Viewer) |
| `AUTH_SERVICE_USER` / `AUTH_SERVICE_PASSWORD` | service | лише API (`POST /api/token`), без доступу до UI |

Назовні публікується лише порт nginx (80) і, для налагодження, RabbitMQ management на `127.0.0.1:15672`.

## Поточний стан (етап 4)

| Сервіс | Призначення |
|---|---|
| `nginx` | **єдина точка входу** (порт 80): `auth_request` до auth, `/grafana/`, `/prometheus/` (лише admin), `/api/` → order-api |
| `auth` | сервер авторизації (FastAPI + SQLite, bcrypt, JWT): `/login`, `/api/token`, `/verify`, `/logout`, CRUD користувачів |
| `nginx-exporter` | метрики nginx (stub_status) для Prometheus |
| `postgres` | PostgreSQL 16 з `pg_stat_statements`, схема й сид-дані з `postgres/init/` |
| `postgres-exporter` | метрики СУБД, зокрема колектор `stat_statements` |
| `rabbitmq` | брокер повідомлень: `orders` → `payments.order-created` (+ DLQ), fanout `control` для режимів хаосу; метрики на `:15692`; UI для налагодження на `http://127.0.0.1:15672` |
| `order-api` | FastAPI: каталог і замовлення (`SELECT … FOR UPDATE`, 409 при нестачі товару), публікація `order.created` |
| `worker` | споживач `order.created`: імітація оплати (0.5–3 с, ~10% відмов), повернення stock, ack/DLQ |
| `load-generator` | віртуальні покупці працюють через Order API; фонові задачі (поповнення складу, зміна цін, архівація, видалення) прямо в БД |
| `prometheus` | збір і зберігання метрик (TSDB, retention 15 днів, exemplars, remote write від Tempo) |
| `alloy` | збирає логи всіх контейнерів через Docker API → Loki; приймає OTLP-трейси → Tempo |
| `loki` | зберігання логів (JSON-логи сервісів, `trace_id` як structured metadata) |
| `tempo` | зберігання трейсів; metrics generator будує service graph і span metrics |
| `grafana` | дашборди «СУБД PostgreSQL», «Застосунок», «Логи і трейси», «Безпека»; режим `auth.proxy` за nginx, власна форма входу вимкнена |

Дашборди генеруються скриптом `grafana/dashboards_src/build.py` (Python 3, без залежностей) у `grafana/provisioning/dashboards/json/`.

## Команди

| Команда | Дія |
|---|---|
| `make up` / `make down` | запуск / зупинка |
| `make ps`, `make logs` | стан і логи |
| `make chaos MODE=...` | режим хаосу (реалізація на етапі 5) |
| `make smoke` | smoke test |
| `make reset` | видалити контейнери **і дані** та запустити заново |
