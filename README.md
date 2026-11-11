# MyStudio Backend

## Project overview

API for **MyStudio**, a studio and equipment rental booking platform with
online payments through Paystack.

**Milestone 1 (current): project foundation.** The scaffold, environment-specific
settings, custom user model, JWT authentication, API documentation and test
suite are in place. Studio, equipment, booking and payment domains arrive in
later milestones.

Customers book as guests and never receive an account. The custom user model is
for platform staff only (`owner`, `manager`, `staff`).

## Technology stack

| Concern | Tool |
|---|---|
| Language | Python 3.15 |
| Framework | Django 6.1 |
| API | Django REST Framework 3.18 |
| Auth | SimpleJWT (access + refresh, rotation, blacklist, throttled login) |
| Database | PostgreSQL (development, production and CI) |
| Config | django-environ + `.env` |
| API docs | drf-spectacular (OpenAPI 3 + Swagger UI + ReDoc) |
| CORS | django-cors-headers |
| Filtering | django-filter + DRF search/ordering |
| Tests | pytest, pytest-django, pytest-cov |

Business locale: **Africa/Lagos** timezone, **NGN** currency.

## Prerequisites

- **Python 3.12 or newer** (this project is developed and tested on 3.15).
  Check with `python --version`.
- **pip** (bundled with Python).
- **PostgreSQL 14 or newer**, running locally, or a Supabase project. Django
  uses the standard PostgreSQL driver (psycopg 3); no provider SDK is needed.
- **Git** (optional, only if you are cloning the repository).
- PowerShell commands below assume Windows. On macOS/Linux, replace
  `.\venv\Scripts\python.exe` with `venv/bin/python` and use
  `python3 -m venv venv`.

> **Python 3.15 and PyYAML:** `drf-spectacular` depends on PyYAML, which does
> not yet publish a `cp315` wheel. See [Troubleshooting](#troubleshooting) before
> installing on 3.15.

## Project structure

```
mystudio-backend/
├── manage.py
├── pytest.ini
├── conftest.py                 shared test fixtures
├── .env.example                every supported environment variable
├── requirements.txt            pinned dependencies (runtime + tests)
├── config/                     project wiring only
│   ├── settings/
│   │   ├── base.py             env parsing, apps, DRF, JWT, CORS, logging
│   │   ├── database.py         DATABASE_URL parsing + Supabase SSL defaults
│   │   ├── development.py      DEBUG, permissive CORS, console email
│   │   ├── test.py             fast hashing, in-memory DB fallback
│   │   └── production.py       required secrets, HTTPS/HSTS, SMTP, Sentry
│   ├── logging_filters.py      secret/token redaction for log records
│   ├── exception_filter.py     masks secrets in error reports
│   ├── urls.py                 /api/v1/, /api/schema/, /api/docs/, /api/redoc/
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── common/                 shared foundation (no business tables)
│   │   ├── models.py           UUIDModel, TimeStampedModel, SoftDeleteModel
│   │   ├── fields.py           MoneyField (NGN, Decimal)
│   │   ├── constants.py        currency + money precision
│   │   ├── api/                pagination, exception handler, health check
│   │   └── tests/
│   ├── accounts/               user model, roles, JWT auth
│   │   ├── models.py           User (UUID PK, email login, role)
│   │   ├── managers.py         UserManager
│   │   ├── services.py         staff accounts, profile, password, tokens
│   │   ├── selectors.py        read helpers
│   │   ├── api/                serializers, permissions, views, urls
│   │   ├── admin.py / forms.py Django admin integration
│   │   ├── migrations/
│   │   └── tests/
│   ├── studios/                studios, hours, pricing rules
│   ├── equipment/              catalogue, stock, rates
│   ├── bookings/               availability, quotes, lifecycle
│   ├── payments/               Paystack, refunds, reconciliation
│   └── dashboard/              administrative reporting
├── tests/                      cross-app integration tests
└── docs/
    ├── architecture.md
    ├── api.md
    └── database.md
```

The six business apps (`accounts`, `studios`, `equipment`, `bookings`,
`payments`, `dashboard`) share the same internal layout: `models.py`,
`services.py`, `selectors.py`, `admin.py`, `api/`, `migrations/` and `tests/`.
`accounts` is fully implemented; the rest are scaffolded for their milestones.

## Local setup

### 1. Create and activate a virtual environment

From the project root:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, allow it for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

All versions are pinned in `requirements.txt`.

### 3. Configure the environment

```powershell
Copy-Item .env.example .env
```

Open `.env` and replace the placeholders. At minimum set `SECRET_KEY` and
`DATABASE_URL`. The full supported list is commented in `.env.example` and
summarised under [Environment variables](#environment-variables).

`.env` is git-ignored. In production, set the same names as environment
variables in the hosting platform's secret manager instead of shipping a file.

### 4. Set up PostgreSQL

**Local PostgreSQL** — create a role and database once:

```powershell
psql -U postgres
```

```sql
CREATE ROLE mystudio WITH LOGIN PASSWORD 'change-me';
CREATE DATABASE mystudio OWNER mystudio;
\q
```

Then point `.env` at it:

```dotenv
DATABASE_URL=postgresql://mystudio:change-me@localhost:5432/mystudio
```

**Supabase** — use the connection string from *Connect → Session pooler* and
include `sslmode=require`. Full instructions:
[docs/database.md](docs/database.md) and [Supabase PostgreSQL](#supabase-postgresql).

PostgreSQL is required in development as well as production; the app refuses to
start without a `postgresql://` `DATABASE_URL`.

### 5. Run migrations

```powershell
python manage.py migrate
python manage.py createsuperuser
```

`createsuperuser` provisions the first platform owner. Additional staff accounts
are created in the Django admin.

Check that no model changes are missing from migrations:

```powershell
python manage.py makemigrations --check --dry-run
```

### 6. Start the development server

```powershell
python manage.py runserver
```

`manage.py` defaults to `config.settings.development`. The server listens on
<http://127.0.0.1:8000/>.

## API documentation URLs

| URL | Purpose |
|---|---|
| `http://127.0.0.1:8000/api/schema/` | OpenAPI 3 schema |
| `http://127.0.0.1:8000/api/docs/` | Swagger UI |
| `http://127.0.0.1:8000/api/redoc/` | ReDoc |
| `http://127.0.0.1:8000/api/v1/` | JSON API index |
| `http://127.0.0.1:8000/api/v1/health/` | Health/readiness probe |
| `http://127.0.0.1:8000/admin/` | Django admin |

Documentation is public in development. In production it defaults to staff-only
access; set `API_DOCS_PUBLIC=True` to open it to everyone.

Studio and equipment image hosting is documented in
[docs/cloudinary.md](docs/cloudinary.md).

The schema can be exported and validated without a running server:

```powershell
python manage.py spectacular --file schema.yaml --validate
```

## Authentication endpoints

Staff accounts only. Customers book as guests and have no account, so there is
no registration endpoint.

All routes are under `/api/v1/auth/`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `login/` | public | Email + password → access/refresh tokens |
| POST | `refresh/` | public | Rotate a refresh token |
| POST | `verify/` | public | Verify a token signature/expiry |
| POST | `logout/` | Bearer | Blacklist your own refresh token |
| GET | `me/` | Bearer | Current user profile |
| PATCH | `me/` | Bearer | Update first and last name |
| POST | `change-password/` | Bearer | Change password and revoke all refresh tokens |

`login/`, `refresh/` and `verify/` are rate limited per IP (defaults: 5, 20 and
20 requests per minute). A password change blacklists every outstanding refresh
token; access tokens remain valid until they expire.

Roles: `owner`, `manager`, `staff`. Accounts are created by an existing
administrator (Django admin or `createsuperuser`); there is no public sign-up.

Authentication is required by default. Public endpoints opt out with
`AllowAny`. List endpoints use page-number pagination (`?page=`, `?page_size=`,
default 20, maximum 100) and support django-filter, `?search=` and `?ordering=`.

## Public catalogue endpoints

Customers browse studios and equipment without signing in. These views disable
authentication, so even an invalid Bearer token does not block them.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/studios/` | public | Paginated active studios with primary image and image count |
| GET | `/api/v1/studios/{slug}/` | public | Full active studio detail with all images; inactive → `404` |
| GET | `/api/v1/equipment/` | public | Paginated active equipment |

Studios support `capacity_min`/`capacity_max`, `hourly_rate_min`/
`hourly_rate_max`, `?search=` and `?ordering=`. Equipment supports
`fixed_price_min`/`fixed_price_max`, `?search=` and `?ordering=`. Public
responses never include Cloudinary public IDs, stock levels or administrative
flags, and stock/booking availability is not calculated yet. Full reference:
[docs/api.md](docs/api.md#public-catalogue).

## Administrative catalogue endpoints

All administrative routes live under `/api/v1/admin/` and require a JWT access
token. Anonymous requests return `401`.

| Method | Path | Roles |
|---|---|---|
| GET | `/api/v1/admin/studios/` | owner, manager, staff |
| POST | `/api/v1/admin/studios/` | owner, manager |
| GET | `/api/v1/admin/studios/{id}/` | owner, manager, staff |
| PATCH | `/api/v1/admin/studios/{id}/` | owner, manager |
| DELETE | `/api/v1/admin/studios/{id}/` | owner, manager |
| POST | `/api/v1/admin/studios/{id}/images/` | owner, manager |
| PATCH | `/api/v1/admin/studios/{id}/images/{image_id}/` | owner, manager |
| DELETE | `/api/v1/admin/studios/{id}/images/{image_id}/` | owner, manager |
| GET | `/api/v1/admin/equipment/` | owner, manager, staff |
| POST | `/api/v1/admin/equipment/` | owner, manager |
| GET | `/api/v1/admin/equipment/{id}/` | owner, manager, staff |
| PATCH | `/api/v1/admin/equipment/{id}/` | owner, manager |
| DELETE | `/api/v1/admin/equipment/{id}/` | owner, manager |

`owner` and `manager` manage the catalogue; `staff` has read-only access.
`DELETE` deactivates studios and equipment instead of removing rows, so booking
history stays valid. Studio images are removed individually (row plus
Cloudinary asset). Equipment images can be uploaded or replaced with
`multipart/form-data` on create/update, or cleared with `"remove_image": true`.
Full reference: [docs/api.md](docs/api.md#administrative-catalogue).

## Environment variables

See `.env.example` for the full, commented list. Core variables:

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | dev-only key | required and must be real in production |
| `DEBUG` | `False` (`True` in development) | never true in production |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | required in production |
| `LOG_LEVEL` | `INFO` | root/Django log level |
| `DATABASE_URL` | none | **required** PostgreSQL URL; Supabase Session Pooler recommended |
| `TEST_DATABASE_URL` | unset → in-memory SQLite | separate test database/project; never production |
| `DB_CONN_MAX_AGE` | `60` | seconds to reuse a connection; `0` on serverless |
| `DB_CONN_HEALTH_CHECKS` | `True` | detect connections dropped by a pooler |
| `DB_CONNECT_TIMEOUT` | `5` | seconds to wait for a new connection |
| `DB_STATEMENT_TIMEOUT_MS` | `60000` | per-statement limit; `0` disables |
| `DB_DISABLE_SERVER_SIDE_CURSORS` | `False` | set `True` only for the transaction pooler (6543) |
| `CORS_ALLOWED_ORIGINS` | empty | required in production |
| `CSRF_TRUSTED_ORIGINS` | empty | must use `https://` in production when set |
| `CACHE_URL` | unset → local memory | **required in production** for shared throttling (e.g. `redis://host:6379/1`) |
| `AUTH_LOGIN_THROTTLE` | `5/min` | login attempts per IP |
| `AUTH_REFRESH_THROTTLE` | `20/min` | refresh attempts per IP |
| `AUTH_VERIFY_THROTTLE` | `20/min` | verify attempts per IP |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | `30` | |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | `7` | |
| `JWT_LEEWAY_SECONDS` | `10` | clock-skew tolerance |
| `API_DOCS_PUBLIC` | `True` in dev, `False` in production | staff-only docs when false |
| `ADMINS` | empty | comma-separated `Name <email>` error recipients |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | empty | production SMTP |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | empty | **required in production**; studio/equipment image hosting |
| `IMAGE_UPLOAD_MAX_BYTES` | `5242880` | maximum accepted image size (5 MB) |
| `PAYSTACK_*`, `RESEND_API_KEY` | placeholders | reserved for later milestones; not used yet |

The `DJANGO_`-prefixed names from earlier milestones
(`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, ...) are still accepted as fallbacks.

Only `config.settings.development` reads `.env`. Production takes every value
from the process environment, so a stale file on a server cannot override the
secret manager.

## Logging

Logging is configured in `config/settings/base.py`; development and production
override the level and production uses a more detailed formatter.

- Development: console handler at `LOG_LEVEL` (default `INFO`). SQL queries from
  `django.db.backends` stay at `WARNING` to keep output readable.
- Production: console handler at `LOG_LEVEL` (default `INFO`) with the process
  id included, ready for a platform log collector.
- Every console handler runs `config.logging_filters.SensitiveDataFilter`, which
  masks database URL credentials, bearer tokens, `password=`/`token:` pairs,
  exception tracebacks and the literal values of `SECRET_KEY`, `DATABASE_URL`,
  `SENTRY_DSN` and provider keys. Django's exception reporter is also filtered
  by `config.exception_filter.SafeExceptionReporterFilter`.
- Sentry, when configured, scrubs outgoing events with the same `redact()`
  helper (`scrub_event`), because it receives exception objects directly.

Passwords, tokens and connection strings are never written to API responses;
the log filter is a second line of defence for error paths.

## Supabase PostgreSQL

Supabase provides the managed PostgreSQL database. Django connects with the
standard `psycopg` (psycopg 3) driver — no Supabase SDK, Auth or Data API is used.

Use the **Session Pooler** connection string (IPv4-compatible, port `5432`):

```dotenv
DATABASE_URL=postgresql://postgres.<project-ref>:<url-encoded-password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

- Copy it from Supabase → **Connect → Session pooler**.
- URL-encode any special characters in the password.
- `sslmode=require` is required; it is added automatically for Supabase hosts
  when absent, but keeping it explicit is preferred.
- Direct connections (`db.<ref>.supabase.co`) are IPv6-only on newer projects.
- The transaction pooler (port `6543`) needs
  `DB_DISABLE_SERVER_SIDE_CURSORS=True`; session pooling is preferred.

Full setup, migration and verification steps: [docs/database.md](docs/database.md).

## Running tests

```powershell
.\venv\Scripts\python.exe -m pytest
```

Or, with the virtual environment activated:

```powershell
python -m pytest
```

The suite is configured in `pytest.ini` (`config.settings.test`) and uses the
shared fixtures in `conftest.py`. Tests live inside each app
(`apps/<app>/tests/`) plus cross-app tests in `tests/`.

The suite runs on SQLite by default. For production parity, create a PostgreSQL
test database and set `TEST_DATABASE_URL`:

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
.\venv\Scripts\python.exe -m pytest
```

Never point `TEST_DATABASE_URL` at production: the test runner creates and drops
its own schema. Coverage is collected for `apps/` and `config/` with an 85%
minimum (see `pytest.ini`); the suite currently runs at roughly 93%.

## Conventions

- **Thin HTTP layer.** Views validate and delegate; serializers shape data;
  `services.py` holds write rules; `selectors.py` holds read queries.
- **UUID primary keys** for every business entity via `UUIDTimeStampedModel`.
  Django framework tables keep integer keys.
- **Money** is never stored as a float. Studio and equipment prices use integer
  kobo (`hourly_rate_kobo`, `fixed_price_kobo`); other amounts use `Decimal` via
  `apps.common.fields.MoneyField`. Paystack conversion belongs to `apps.payments`.
- **Time** is stored in UTC (`USE_TZ=True`) and rendered in `Africa/Lagos`.
- **Errors** from services are Django `ValidationError`; the shared exception
  handler turns them into `400` responses with field-level messages.
- **Settings** never import business models. Environment modules only override
  what differs.

## Production notes

- Set `DJANGO_SETTINGS_MODULE=config.settings.production`.
- Install `requirements.txt`.
- `python manage.py collectstatic` is required because Whitenoise serves the
  compressed manifest.
- Production fails fast if `SECRET_KEY`, `DATABASE_URL`, `ALLOWED_HOSTS` or
  `CORS_ALLOWED_ORIGINS` are missing, and rejects an insecure development key or
  non-HTTPS `CSRF_TRUSTED_ORIGINS`.
- Set `CACHE_URL` to a shared cache (Redis) so login throttling applies across
  every worker; the default local-memory cache is per process.
- API documentation is staff-only unless `API_DOCS_PUBLIC=True`.
- Production does not read `.env`; configure the platform's secret manager.
- `python manage.py check --deploy` must report no issues before deploying.
- Production requires a PostgreSQL `DATABASE_URL`; development does too, so
  schema changes are never validated against a different database engine.

## Troubleshooting

**PyYAML has no Python 3.15 wheel.** `drf-spectacular` depends on PyYAML, which
does not yet publish a `cp315` wheel. `pip install` therefore tries to compile
it and fails without Microsoft C++ Build Tools. This virtual environment already
contains a pure-Python PyYAML 6.0.3 wheel. If you rebuild the environment, either
install the C++ Build Tools, use Python 3.12/3.13 for development, or build the
pure-Python wheel from the official sdist.

**`Database access not allowed` in tests.** Add the `db` fixture or
`@pytest.mark.django_db` to the test.

**`No module named 'config'`.** Run pytest/manage.py from the project root, or
ensure `pythonpath = .` in `pytest.ini` is present.

**PowerShell blocks `Activate.ps1`.** Run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the current
session, then activate again.
