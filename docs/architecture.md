# Architecture

## Layers

```
HTTP request
   │
   ▼
api/views.py            thin: authentication, permissions, request/response
   │  validates input
   ▼
api/serializers.py      shape + field validation only
   │  calls
   ▼
services.py             write rules, transactions, cross-entity invariants
selectors.py            read queries, filtering, aggregates
   │
   ▼
models.py               persistence shape and small domain helpers
```

Rules of thumb:

- A view should rarely exceed ~30 lines and must not contain business decisions.
- A serializer may validate input, never decide pricing, availability or
  ownership.
- A service function is the only place that mutates state. It raises Django
  `ValidationError` for rule violations; the shared exception handler converts
  those to HTTP 400 with field names intact.
- A selector returns querysets or plain values and never writes.

## Applications

| App | Status | Responsibility |
|---|---|---|
| `common` | built | Abstract base models, money field/constants, pagination, error handling, health check. No tables. |
| `accounts` | built | Custom `User`, roles, JWT auth, profile and password services, admin integration. |
| `studios` | models + images | `Studio` and `StudioImage`: kobo hourly rate, capacity, JSON amenities, Cloudinary image uploads, deactivation. |
| `equipment` | models + images | Global `Equipment` catalogue: kobo fixed booking price, shared stock, Cloudinary image upload/replace, deactivation. |
| `bookings` | scaffolded | Booking lifecycle, availability resolution, quotes, cancellations. |
| `payments` | scaffolded | Paystack adapter, payment ledger, webhooks, refunds. |
| `dashboard` | scaffolded | Admin analytics endpoints over other apps' data. |
| `notifications` | later | Transactional email/SMS and delivery logging. |

Dependency direction: business apps may import from `common`; `common` never
imports a business app. Cross-app effects go through services, not direct model
manipulation from another app's views.

## Primary keys and timestamps

`apps.common.models.UUIDTimeStampedModel` provides:

- `id`: UUIDv4, non-editable, primary key.
- `created_at`: auto-set, indexed.
- `updated_at`: auto-set on save.

Use it for every new business entity. Public URLs and API payloads therefore
never expose sequential identifiers.

`SoftDeleteModel` is opt-in: `objects` hides deleted rows, `all_objects`
includes them, and deletion is deliberately **not** cascaded.

`DeactivateOnDeleteModel` is the alternative used by studios and equipment:
`delete()` (instance or queryset) sets `is_active=False` instead of removing the
row, so future bookings keep valid references. `hard_delete()` on the queryset
is the explicit escape hatch for cleanup.

## Money

- `apps.common.fields.MoneyField` = `DecimalField(max_digits=12, decimal_places=2)`.
- Platform currency is `NGN` (`MYSTUDIO_CURRENCY`, `apps.common.constants`).
- Studio and equipment prices are stored as **integer kobo**
  (`hourly_rate_kobo`, `fixed_price_kobo`). Never use floats for money.
- Paystack expects integer kobo; payment integration lives in
  `apps.payments`. Other apps deal only in kobo integers or `Decimal` NGN,
  never in floats.

## Studios and equipment

- `studios.Studio` is a UUID entity with a unique, indexed slug. A blank slug is
  generated from the name; collisions get `-2`, `-3` suffixes. Explicit
  duplicate slugs fail the database uniqueness check. Slug generation is
  case-insensitive and concurrency-safe: `My-Studio` and `my-studio` resolve to
  the same canonical lower-case slug; concurrent requests that collide on a
  generated slug retry automatically up to a configured attempt limit.
- `hourly_rate_kobo` and `capacity` must be at least 1 and at most the maximum
  PostgreSQL integer value (`2,147,483,647`), enforced by validators and
  database check constraints.
- `amenities` is a JSON list of non-empty strings. It is denormalised so public
  responses need no join.
- `studios.StudioImage` references the studio with `on_delete=PROTECT` and
  stores the Cloudinary `image_url`, unique `cloudinary_public_id`, and
  `updated_at`. Images are ordered by `display_order`, then `created_at`, then
  `id` for deterministic pagination.
- `equipment.Equipment` is a **global** catalogue: there is no studio foreign
  key and one `stock_quantity` is shared by every studio. Stock must not be
  negative; zero means temporarily unavailable. The price is per item per
  booking.
- Both catalogues use `is_active` for public visibility and history retention.
  Deactivation is the supported delete operation; only the explicit
  `hard_delete()` queryset method removes rows.
- Optional equipment images are all-or-nothing: set both `image_url` and
  `cloudinary_public_id` or neither.
- All integer price and stock fields are capped at the maximum PostgreSQL
  integer value; requests exceeding this return `400` instead of failing at the
  database.
- `remove_image` is rejected on equipment creation (there is no image to remove
  yet).

### Public catalogue API

- `apps.studios.selectors.active_studios()` and
  `apps.equipment.selectors.active_equipment()` are the only public visibility
  rules; views filter `is_active=True` and never remove inactive rows.
- Studio list responses expose `short_description` (160-character excerpt),
  `primary_image` (lowest `display_order`) and `image_count`. Studio detail
  exposes the full description and every image.
- Public image payloads expose `id`, `image_url`, `alt_text` and
  `display_order`. Cloudinary `public_id` values stay on the administrative
  serializer.
- Equipment responses expose the catalogue fields only: no `stock_quantity`,
  `is_active` or timestamps. There is no public equipment detail route.
- Public views set `authentication_classes = []` so anonymous customers and
  clients holding stale tokens can still browse.
- Filters, search and ordering are declared per view with django-filter,
  `SearchFilter` and `OrderingFilter`. Availability calculations are deferred to
  the bookings milestone.

### Administrative catalogue API

- Routes live under `/api/v1/admin/studios/` and `/api/v1/admin/equipment/`
  (Django URL namespaces `studios-admin` and `equipment-admin`). They accept
  **JWT Bearer tokens only**; Django session cookies are not accepted even for
  logged-in staff accounts.
- `apps.accounts.api.permissions.CanManageCatalogue` is a
  `RoleBasedPermission`: `owner` and `manager` may write, while `staff` is
  limited to safe methods. `RoleBasedPermission` takes separate `read_roles`
  and `write_roles`, so booking and payment permissions can reuse it.
- Studio and equipment writes go through `create_*`, `update_*` and
  `deactivate_*` functions in each app's `services.py`. Both `PATCH` (partial
  update) and `PUT` (full replace) are supported on detail endpoints.
- `DELETE` maps to deactivation: the row stays, `is_active` becomes `False`, and
  the public selectors stop returning it. Studio images keep their row and
  Cloudinary asset.
- Admin list endpoints include inactive records by default and expose an
  `is_active` filter. They are paginated, searchable and orderable like the
  public API.
- `StudioAdminSerializer` validates positive prices/capacity (with explicit
  upper bounds), amenity shape and slug uniqueness. `EquipmentAdminSerializer`
  validates positive prices, non-negative stock (with explicit upper bounds),
  accepts write-only `image` and `remove_image` fields, and rejects
  `remove_image` on create.
- Equipment image replacement follows the existing service ordering: persist the
  new references, then delete the previous asset. A failed database write
  destroys the fresh upload.
- Image removal (studio images, equipment `remove_image`) deletes the row/asset
  atomically in a transaction. A Cloudinary failure rolls back the database
  change, preventing orphaned assets. The operation returns `502` (or `503` when
  unconfigured) and the image is unchanged.
- Administrative responses may include Cloudinary public IDs; public responses
  never do.

### Image hosting

- Cloudinary is the only image store; the official SDK is wrapped by
  `apps.common.cloudinary`. See [cloudinary.md](cloudinary.md).
- `upload_image()` and `delete_image()` are the only functions that call the
  provider. Views and serializers never import the SDK.
- Uploads are validated with Pillow against the actual bytes: JPEG, PNG or
  WebP, maximum 5 MB. Extensions and browser content types are not trusted.
- Studio files go to `mystudio/studios`; equipment files to
  `mystudio/equipment`.
- Writes that change the database happen before the previous asset is deleted,
  so the catalogue never points at a missing file. A failed insert after an
  upload triggers a compensating Cloudinary deletion.
- Credentials come from `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` and
  `CLOUDINARY_API_SECRET`. Production refuses to start without them; tests mock
  the provider and need none.
- Image endpoints are owner/manager only; `staff` accounts cannot mutate the
  catalogue or its images.

## Time

- `TIME_ZONE = "Africa/Lagos"`, `USE_TZ = True`, `LANGUAGE_CODE = "en-us"`.
- Database values are UTC. Availability and business-day cut-offs must be
  computed in Lagos time explicitly (e.g. `timezone.localtime()`), never by
  relying on the server's clock or timezone.

## Settings

| Module | Used by | Purpose |
|---|---|---|
| `config.settings.base` | imported by all | shared config, environment parsing |
| `config.settings.development` | `manage.py`, local runs | DEBUG, permissive CORS, PostgreSQL required |
| `config.settings.test` | pytest | fast hashing, in-memory email, test DB |
| `config.settings.production` | gunicorn/WSGI | required secrets, HTTPS, Whitenoise, Sentry |

`config` must stay importable without touching the database, so it never imports
models or app internals.

## Database

- `DATABASE_URL` is the single source of truth for PostgreSQL credentials; it is
  read from the environment and never hardcoded.
- Development and production both refuse to start unless `DATABASE_URL` resolves
  to Django's PostgreSQL backend; there is no SQLite fallback outside tests.
- `config/settings/database.py` parses the URL, forwards libpq options (for
  example `sslmode`) into Django's `OPTIONS`, and defaults Supabase hosts to
  `sslmode=require`.
- PostgreSQL connections configure `CONN_MAX_AGE`, `CONN_HEALTH_CHECKS` and
  `DISABLE_SERVER_SIDE_CURSORS`; the latter only matters for a transaction
  pooler.
- The test settings fall back to in-memory SQLite when `TEST_DATABASE_URL` is
  unset, so the suite runs without a database server.
- Detailed setup and verification: [database.md](database.md).

## Authentication

- `AUTH_USER_MODEL = "accounts.User"`, email as `USERNAME_FIELD`.
- Accounts are for platform staff only; customers book as guests.
- Roles: `owner`, `manager`, `staff`. `create_user()` can never create a
  superuser or the owner role; `create_superuser()` creates the owner. A
  database check constraint rejects unknown roles.
- Access tokens: 30 minutes. Refresh tokens: 7 days, rotated and blacklisted.
- Custom claims: `role`, `email`, `full_name`.
- Logout blacklists the presented refresh token, but only when it belongs to the
  authenticated user.
- Password changes blacklist **every** outstanding refresh token for that user.
  Access tokens already issued remain valid until they expire (30 minutes by
  default); add a token-version claim if immediate access-token revocation is
  required later.
- Login, refresh and verify are rate limited per IP (`AUTH_*_THROTTLE`). A
  shared cache (`CACHE_URL`) is required in production so every worker shares
  the counters.

## API surface

- All business routes: `/api/v1/...`.
- API root: `/api/v1/` (public JSON index).
- Schema: `/api/schema/`; Swagger UI: `/api/docs/`; ReDoc: `/api/redoc/`.
  Public in development; staff-only in production unless `API_DOCS_PUBLIC=True`.
- Authentication is required by default. Public endpoints opt out explicitly
  with `AllowAny` (health, login, refresh, verify).
- There is no public registration endpoint; staff accounts are provisioned by
  an administrator.
- Pagination is page-number based, 20 items by default, 100 maximum.
- Filtering backends are configured globally: django-filter for declared
  filtersets, plus DRF `SearchFilter` and `OrderingFilter`.

## Logging

- `config.settings.base` defines a console handler used by every environment.
- Development keeps `django.db.backends` at `WARNING` so SQL is not printed.
- Production uses a verbose formatter that includes the process id.
- The `redact_secrets` logging filter masks database URL credentials, bearer
  tokens, key/value secrets and the literal values of sensitive settings before
  a record is emitted.
- `SafeExceptionReporterFilter` applies the same idea to debug pages and error
  reports.

## Testing strategy

- Unit tests per app: models, services, API.
- Cross-app tests in `tests/` only for behaviour that spans apps.
- `config.settings.test` runs against SQLite unless `TEST_DATABASE_URL` points
  at PostgreSQL; CI should set the PostgreSQL URL for parity.
- `test_no_missing_migrations` guards against model changes without migrations.

## Adding a new domain app

1. Create `apps/<name>/{__init__,apps,models,services,selectors,admin}.py`.
2. Add `api/{serializers,views,urls,permissions}.py`.
3. Register `apps.<name>` in `config.settings.base.INSTALLED_APPS`.
4. Mount its URLs inside `config.urls.api_v1_patterns`.
5. Add `migrations/` and `tests/` with factories.
6. Run `python manage.py makemigrations <name>` and the test suite.
