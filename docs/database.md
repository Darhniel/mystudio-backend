# Database: PostgreSQL

MyStudio uses PostgreSQL in development, production and CI. Locally it can be a
plain PostgreSQL server; in production it is Supabase PostgreSQL. Both are
reached through Django's standard ORM and the `psycopg` (psycopg 3) driver.
There is no Supabase SDK, no Supabase Auth integration and no use of the
Supabase Data API (PostgREST) — Supabase is simply the managed PostgreSQL host.

Configuration lives in `config/settings/base.py`; URL parsing, Supabase SSL
defaults and the PostgreSQL guard live in `config/settings/database.py`.

## Local PostgreSQL (development)

Install PostgreSQL 15+ and create a database and role. With the default
`postgres` superuser:

```powershell
# Create the database owned by the current OS user
createdb -U postgres mystudio

# Or create a dedicated role first:
psql -U postgres -c "CREATE ROLE mystudio LOGIN PASSWORD 'change-me';"
psql -U postgres -c "CREATE DATABASE mystudio OWNER mystudio;"
```

Then point `DATABASE_URL` at it:

```dotenv
DATABASE_URL=postgresql://mystudio:change-me@localhost:5432/mystudio
```

Verify and migrate:

```powershell
.\venv\Scripts\python.exe manage.py check --database default
.\venv\Scripts\python.exe manage.py migrate
```

The sections below cover Supabase, which is the production provider.

## Which connection string to use

Supabase exposes three endpoints:

| Endpoint | Host | Port | IPv4 | Use for MyStudio |
|---|---|---|---|---|
| Session Pooler | `aws-0-<region>.pooler.supabase.com` | `5432` | ✅ | **Recommended** |
| Transaction Pooler | `aws-0-<region>.pooler.supabase.com` | `6543` | ✅ | Only with extra cursor config |
| Direct connection | `db.<project-ref>.supabase.co` | `5432` | ❌ (IPv6-only on new projects) | Only on IPv6-capable networks |

Use the **Session Pooler**. It is reachable over IPv4, supports Django's
persistent connections, and is fully compatible with `migrate`.

## 1. Copy the connection string

In the Supabase dashboard open **Project Settings → Database → Connection
string** (or the **Connect** button) and select **Session pooler**.

It has this shape:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Notes:

- The pooler user is `postgres.<project-ref>`, not plain `postgres`.
- The database is usually `postgres`.
- Keep `?sslmode=require` — Supabase rejects unencrypted connections.
  If it is missing on a Supabase host, `config/settings/database.py` adds
  `sslmode=require` automatically.

## 2. URL-encode the password

Passwords containing `@`, `:`, `/`, `#`, `?`, `%` or spaces must be
percent-encoded before they are placed in a URL. `@` and `#` in particular will
otherwise corrupt the URL.

```powershell
.\venv\Scripts\python.exe -c "from urllib.parse import quote; print(quote('your-password-here', safe=''))"
```

Use the printed value in place of `<password>`.

## 3. Store it in the environment

Copy the template, then edit `DATABASE_URL` in `.env` (git-ignored):

```dotenv
DATABASE_URL=postgresql://postgres.<ref>:<encoded-password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
DB_CONN_MAX_AGE=60
```

Production: set the same variable in the hosting platform's secret manager.
Never commit the URL, and never place it in a settings module.

`DATABASE_URL` is required in development and production: Django fails fast
rather than falling back to SQLite. Only the test harness falls back to
in-memory SQLite when `TEST_DATABASE_URL` is unset.

## 4. Tests

Tests should never run against the production project. Use a separate Supabase
project/branch or a local PostgreSQL instance:

```dotenv
TEST_DATABASE_URL=postgresql://postgres.<test-ref>:<encoded-password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Django creates a temporary `test_<database>` database, runs migrations, and
drops it afterwards. The database role therefore needs `CREATEDB`; if the
provider restricts that, point `TEST_DATABASE_URL` at local PostgreSQL instead.

Without `TEST_DATABASE_URL` the suite falls back to in-memory SQLite.

## 5. Connection behaviour

The following settings are applied automatically for PostgreSQL URLs:

| Setting | Default | Why |
|---|---|---|
| `CONN_MAX_AGE` | `60` (`DB_CONN_MAX_AGE`) | Reuse connections between requests |
| `CONN_HEALTH_CHECKS` | `True` (`DB_CONN_HEALTH_CHECKS`) | Detect connections dropped by the pooler |
| `DISABLE_SERVER_SIDE_CURSORS` | `False` (`DB_DISABLE_SERVER_SIDE_CURSORS`) | Session pooling supports cursors |

On serverless platforms (many short-lived instances), set
`DB_CONN_MAX_AGE=0` so each instance does not hold a pooled connection.

**Transaction Pooler (port 6543) only:** set
`DB_DISABLE_SERVER_SIDE_CURSORS=True`. Transaction pooling also does not support
session-level features such as advisory locks held across transactions. This is
why the Session Pooler is the recommended choice.

## 6. Verify the connection

```powershell
# Settings load and database backend is valid
.\venv\Scripts\python.exe manage.py check --database default

# Connect and print non-secret connection details
.\venv\Scripts\python.exe manage.py shell -c "from django.db import connection; connection.ensure_connection(); s = connection.settings_dict; print('vendor:', connection.vendor); print('host:', s['HOST']); print('port:', s['PORT']); print('db:', s['NAME']); print('sslmode:', s.get('OPTIONS', {}).get('sslmode'))"
```

Simpler one-liner for the essential check:

```powershell
.\venv\Scripts\python.exe manage.py shell -c "from django.db import connection; connection.ensure_connection(); print('connected to', connection.settings_dict['HOST'], connection.settings_dict['PORT'])"
```

Then run migrations:

```powershell
.\venv\Scripts\python.exe manage.py showmigrations
.\venv\Scripts\python.exe manage.py migrate
```

`showmigrations` reads `django_migrations`, so it proves the app can reach the
database. `migrate` applies the schema.

Finally check the running service:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/
```

The health endpoint executes `SELECT 1` and reports
`"checks": {"database": "ok"}`; it returns HTTP 503 when the connection fails.

## 7. Running migrations safely

- Run `migrate` from a single deploy job or one-off task, never concurrently
  from every application instance.
- The Session Pooler supports transactional DDL, so normal Django migrations
  need no special handling.
- Take a Supabase backup/snapshot before destructive migrations in production.
- After deploying a new image, `migrate` may run again; Django skips applied
  migrations.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `could not translate host name` / connection timeout | Direct host is IPv6-only. Switch to the Session Pooler (port 5432). |
| `password authentication failed` | Password is not URL-encoded, or the user is `postgres` instead of `postgres.<project-ref>`. Re-copy the pooler string. |
| `no pg_hba.conf entry ... no encryption` | `sslmode` was stripped or overridden. Append `?sslmode=require`. |
| `too many connections` | Lower `DB_CONN_MAX_AGE` (or use `0`), reduce worker count, or use the Session Pooler within your plan's pool size. |
| `cursor ... does not exist` / cursor errors | You are on the Transaction Pooler (6543). Set `DB_DISABLE_SERVER_SIDE_CURSORS=True` or move to port 5432. |
| `prepared statement ... already exists` | Transaction pooling with prepared statements. Use the Session Pooler. |
| Tests try to create a database and fail | Role lacks `CREATEDB`; use local PostgreSQL for the test suite or a test project where the role can create databases. |

## Security notes

- Database credentials exist only in environment variables; settings never
  contain a host, user or password.
- `sslmode=require` encrypts traffic but does not verify the server certificate
  chain. Use `sslmode=verify-full` in the URL if your environment provides the
  required CA roots.
- Supabase Row Level Security applies to the Data API roles. Django connects
  with the database role from `DATABASE_URL`, so RLS is **not** your access
  control layer here — the API's own permissions are. Never expose database
  credentials to the frontend.
- Rotate the database password in the Supabase dashboard and update the
  environment secret if it is ever exposed.
