# API reference

Base URL: `/api/v1/`
Content type: `application/json`
Authentication: `Authorization: Bearer <access_token>`

Interactive documentation: `/api/docs/` (Swagger UI) and `/api/redoc/`
(ReDoc).
Machine-readable schema: `/api/schema/` (OpenAPI 3).

## Error format

Field validation errors (HTTP 400):

```json
{
  "email": ["A user with this email address already exists."],
  "non_field_errors": ["This password is too short."]
}
```

Authentication errors (HTTP 401):

```json
{ "detail": "Given token not valid for any token type", "code": "token_not_valid" }
```

Unexpected server errors return HTTP 500 with a generic `detail` message; the
full traceback is logged, never returned.

## Health

### `GET /api/v1/health/` — public

```json
{
  "status": "ok",
  "checks": { "database": "ok" },
  "version": "1.0.0",
  "timestamp": "2026-11-10T14:00:00+00:00"
}
```

Returns `503` with `"status": "unhealthy"` if the database is unreachable.

## Login

### `POST /api/v1/auth/login/` — public

Staff accounts only: customers book as guests and never receive credentials, so
there is no registration endpoint.

```json
{ "email": "ada@example.com", "password": "Str0ng-Passw0rd!2026" }
```

**200 OK**

```json
{
  "user": {
    "id": "0259df01-3663-4bad-81c5-0bf7a0d3db7e",
    "email": "ada@example.com",
    "first_name": "Ada",
    "last_name": "Nwosu",
    "full_name": "Ada Nwosu",
    "role": "manager",
    "is_active": true,
    "date_joined": "2026-11-10T14:00:00Z",
    "updated_at": "2026-11-10T14:00:00Z"
  },
  "access": "<jwt>",
  "refresh": "<jwt>"
}
```

Access tokens carry `role`, `email` and `full_name` claims.

## Refresh and verify

### `POST /api/v1/auth/refresh/` — public

```json
{ "refresh": "<refresh_token>" }
```

**200 OK**

```json
{ "access": "<jwt>", "refresh": "<new_refresh_jwt>" }
```

Refresh tokens rotate; the used token is blacklisted immediately.

### `POST /api/v1/auth/verify/` — public

```json
{ "token": "<access_token>" }
```

Returns `200 OK` with `{}` when valid, `401` otherwise.

## Logout

### `POST /api/v1/auth/logout/` — Bearer

```json
{ "refresh": "<refresh_token>" }
```

**204 No Content.** The refresh token is blacklisted; presenting it again
returns `401`. A token that belongs to a different account is rejected with
`400` and left untouched.

## Profile

### `GET /api/v1/auth/me/` — Bearer

Returns the `user` object shown above.

### `PATCH /api/v1/auth/me/` — Bearer

```json
{ "first_name": "Ada", "last_name": "Nwosu" }
```

Only these two fields are mutable. `role`, `email` and status flags are
ignored; role changes are an administrative action.

## Change password

### `POST /api/v1/auth/change-password/` — Bearer

```json
{ "current_password": "old-secret", "new_password": "A-Brand-New-Pass!42" }
```

**204 No Content.** Incorrect current password → `400` with
`{"current_password": ["Current password is incorrect."]}`. New passwords go
through Django's configured password validators. Every refresh token issued
before the change is blacklisted, so all sessions must log in again; access
tokens stay valid until they expire.

## Public catalogue

Studios and equipment are browsable without authentication. These views disable
authentication entirely, so a missing, expired or malformed `Authorization`
header never blocks a catalogue request.

Only **active** records are returned. Inactive studios and equipment are kept in
the database for booking history but are invisible to customers. Responses
never include Cloudinary public IDs, stock levels, `is_active` flags or
timestamps (other than a studio's `created_at`).

### `GET /api/v1/studios/` — public

Paginated list of active studios, ordered by name.

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "5c1f0f5e-0000-0000-0000-000000000000",
      "name": "Natural Light Studio",
      "slug": "natural-light-studio",
      "short_description": "A bright north-facing studio with a white cyclorama.",
      "hourly_rate_kobo": 2500000,
      "capacity": 12,
      "amenities": ["WiFi", "Parking"],
      "primary_image": {
        "id": "0f0f0f0f-0000-0000-0000-000000000000",
        "image_url": "https://res.cloudinary.com/demo/image/upload/studio.jpg",
        "alt_text": "White cyclorama",
        "display_order": 0
      },
      "image_count": 3
    }
  ]
}
```

- `short_description` is the studio description trimmed to 160 characters; the
  full text is available from the detail endpoint.
- `primary_image` is the image with the lowest `display_order`, or `null` when
  the studio has no images.
- `image_count` counts all images attached to the studio.

Filters:

| Parameter | Meaning |
|---|---|
| `capacity_min` | Minimum capacity |
| `capacity_max` | Maximum capacity |
| `hourly_rate_min` | Minimum hourly rate in kobo |
| `hourly_rate_max` | Maximum hourly rate in kobo |
| `search` | Case-insensitive match on `name` or `description` |
| `ordering` | `name`, `hourly_rate_kobo`, `capacity`, `created_at`; prefix `-` for descending |

Examples:

```
GET /api/v1/studios/?capacity_min=10&hourly_rate_max=5000000
GET /api/v1/studios/?search=cyclorama&ordering=-hourly_rate_kobo
GET /api/v1/studios/?page=2&page_size=50
```

### `GET /api/v1/studios/{slug}/` — public

Full detail for one active studio. Inactive or unknown slugs return `404`.
The slug lookup is case-insensitive: `/api/v1/studios/My-Studio/` and
`/api/v1/studios/my-studio/` resolve to the same studio.

```json
{
  "id": "5c1f0f5e-0000-0000-0000-000000000000",
  "name": "Natural Light Studio",
  "slug": "natural-light-studio",
  "description": "A bright north-facing studio with a white cyclorama and parking.",
  "hourly_rate_kobo": 2500000,
  "capacity": 12,
  "amenities": ["WiFi", "Parking"],
  "images": [
    {
      "id": "0f0f0f0f-0000-0000-0000-000000000000",
      "image_url": "https://res.cloudinary.com/demo/image/upload/studio.jpg",
      "alt_text": "White cyclorama",
      "display_order": 0
    }
  ],
  "created_at": "2026-11-10T14:00:00Z"
}
```

Images are ordered by `display_order`, then `created_at`, then `id`.

All integer fields (`hourly_rate_kobo`, `capacity`) are capped at the maximum
PostgreSQL integer value (`2,147,483,647`). Requests exceeding this return
`400` instead of failing at the database.

### `GET /api/v1/equipment/` — public

Paginated list of active equipment, ordered by name. Equipment is shared
inventory; availability and stock are a future bookings concern and are not part
of this response.

Items with `stock_quantity = 0` (temporarily out of stock) are included in the
public catalogue. This allows customers to see the item and its price; the
bookings milestone will enforce availability when a reservation is attempted.

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "6a6a6a6a-0000-0000-0000-000000000000",
      "name": "Godox SL-60W light",
      "description": "Continuous LED light with a Bowens mount.",
      "fixed_price_kobo": 500000,
      "image_url": "https://res.cloudinary.com/demo/image/upload/light.jpg"
    }
  ]
}
```

`image_url` is `null` for items without an image.

Filters:

| Parameter | Meaning |
|---|---|
| `fixed_price_min` | Minimum fixed price in kobo |
| `fixed_price_max` | Maximum fixed price in kobo |
| `search` | Case-insensitive match on `name` or `description` |
| `ordering` | `name`, `fixed_price_kobo`, `created_at`; prefix `-` for descending |

There is no public equipment detail endpoint; the catalogue list is the public
surface.

## Administrative catalogue

Administrative routes live under `/api/v1/admin/`. Every route requires a valid
JWT access token (Bearer authentication only; Django session cookies are not
accepted); anonymous requests return `401`.

| Role | Administrative access |
|---|---|
| `owner` | Full management: create, read, update, deactivate, images |
| `manager` | Create, read, update and deactivate studios/equipment, images |
| `staff` | Read-only: list and retrieve studios/equipment |

`staff` accounts receive `403` for `POST`, `PATCH`, `PUT`, `DELETE` and every
image mutation. Public catalogue routes are unaffected by these rules.

### `GET /api/v1/admin/studios/` — owner/manager/staff

Paginated list of **all** studios, including inactive ones. Default ordering is
newest first (`-created_at`).

Filters:

| Parameter | Meaning |
|---|---|
| `is_active` | `true` or `false` |
| `capacity_min`, `capacity_max` | Capacity range |
| `hourly_rate_min`, `hourly_rate_max` | Hourly rate range in kobo |
| `search` | Match on `name`, `slug` or `description` |
| `ordering` | `name`, `slug`, `hourly_rate_kobo`, `capacity`, `is_active`, `created_at`, `updated_at` |
| `page`, `page_size` | Pagination (`20` default, `100` maximum) |

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "5c1f0f5e-0000-0000-0000-000000000000",
      "name": "Natural Light Studio",
      "slug": "natural-light-studio",
      "description": "A bright north-facing studio.",
      "hourly_rate_kobo": 2500000,
      "capacity": 12,
      "amenities": ["WiFi", "Parking"],
      "is_active": true,
      "images": [],
      "created_at": "2026-11-10T14:00:00Z",
      "updated_at": "2026-11-10T14:00:00Z"
    }
  ]
}
```

### `POST /api/v1/admin/studios/` — owner/manager

```json
{
  "name": "Natural Light Studio",
  "slug": "natural-light-studio",
  "description": "A bright north-facing studio.",
  "hourly_rate_kobo": 2500000,
  "capacity": 12,
  "amenities": ["WiFi", "Parking"]
}
```

`slug` is optional; a blank or omitted slug is generated from the name with
`-2`, `-3`, ... suffixes on collision. An explicit duplicate slug returns `400`.

**201 Created** with the administrative representation above.

Validation rejects:

- a missing name;
- `hourly_rate_kobo` or `capacity` below `1` (zero and negative values);
- amenities that are not a list of non-empty strings.

### `GET /api/v1/admin/studios/{id}/` — owner/manager/staff

Returns one studio, active or inactive, with its ordered images.

### `PATCH /api/v1/admin/studios/{id}/` — owner/manager

Accepts any subset of the create fields. `is_active` may be set directly, but
`DELETE` is the supported way to deactivate a studio. A duplicate slug returns
`400`; sending `"slug": ""` regenerates it from the current name.

### `DELETE /api/v1/admin/studios/{id}/` — owner/manager

**204 No Content.** Deactivates the studio (`is_active=false`) instead of
deleting the row. Images and booking history remain intact, the studio
disappears from the public catalogue, and repeated deletes are safe.

### Administrative studio images

| Method | Path | Roles |
|---|---|---|
| POST | `/api/v1/admin/studios/{id}/images/` | owner, manager |
| PATCH | `/api/v1/admin/studios/{id}/images/{image_id}/` | owner, manager |
| DELETE | `/api/v1/admin/studios/{id}/images/{image_id}/` | owner, manager |

`POST` uses `multipart/form-data`:

| Field | Required | Notes |
|---|---|---|
| `image` | yes | JPEG, PNG or WebP; maximum 5 MB |
| `alt_text` | no | Defaults to an empty string |
| `display_order` | no | Appends after the current last image |

**201 Created** returns the stored image, including its Cloudinary public ID
(administrative responses only).

`PATCH` accepts JSON with `alt_text` and/or `display_order`; at least one field
is required. Image IDs are scoped to their studio, so an image under another
studio returns `404`.

`DELETE` removes the image row and its Cloudinary asset atomically. Both
operations run in one database transaction: if the Cloudinary deletion fails,
the row deletion is rolled back and the image stays visible. This prevents
orphaned Cloudinary assets. A provider failure returns `502` (or `503` when
unconfigured) and the image is unchanged. Studio images have no booking
history, so they are permanently deleted rather than deactivated.

### `GET /api/v1/admin/equipment/` — owner/manager/staff

Paginated list of **all** equipment, including inactive items, ordered newest
first by default. Supports `is_active`, `fixed_price_min`,
`fixed_price_max`, `search` (name/description), `ordering` (`name`,
`fixed_price_kobo`, `stock_quantity`, `is_active`, `created_at`, `updated_at`)
and pagination.

### `POST /api/v1/admin/equipment/` — owner/manager

JSON or `multipart/form-data`:

```json
{
  "name": "Godox SL-60W light",
  "description": "Continuous LED light with a Bowens mount.",
  "fixed_price_kobo": 500000,
  "stock_quantity": 4
}
```

`fixed_price_kobo` must be at least `1`; `stock_quantity` must be at least `0`
(zero means temporarily out of stock). To attach an image in the same request,
send `multipart/form-data` with an `image` field. The response includes
`id`, `name`, `description`, `fixed_price_kobo`, `stock_quantity`, `image_url`,
`is_active`, `created_at` and `updated_at`.

### `GET /api/v1/admin/equipment/{id}/` — owner/manager/staff

Returns one equipment item, active or inactive.

### `PATCH /api/v1/admin/equipment/{id}/` — owner/manager

Accepts any subset of the create fields. Image handling:

- send `image` as `multipart/form-data` to upload or replace the current image;
- send `"remove_image": true` as JSON to clear it;
- sending both in one request returns `400`.
- `remove_image` is rejected on create (there is no image to remove yet).

A replacement is saved before the previous Cloudinary asset is deleted. If the
database write fails, the new upload is discarded; if the old-asset deletion
fails, the replacement still succeeds and the failure is logged.

Removing an image clears the row and deletes the Cloudinary asset in one
transaction. If the provider deletion fails, the row clearing is rolled back
and the image stays visible. This prevents orphaned Cloudinary assets. A
provider failure returns `502` (or `503` when unconfigured) and the image is
unchanged.

All integer fields (`fixed_price_kobo`, `stock_quantity`) are capped at the
maximum PostgreSQL integer value (`2,147,483,647`). Requests exceeding this
return `400` instead of failing at the database.

### `DELETE /api/v1/admin/equipment/{id}/` — owner/manager

**204 No Content.** Deactivates the item (`is_active=false`) instead of deleting
the row. The item disappears from the public catalogue and repeated deletes are
safe.

## Studio and equipment images

Images are hosted by Cloudinary; PostgreSQL stores the `secure_url` and
`public_id` only. The reusable wrapper lives in `apps.common.cloudinary` and
validates real image bytes with Pillow before upload.

### `POST /api/v1/studios/{slug}/images/` — Bearer, owner/manager

`multipart/form-data`:

| Field | Required | Notes |
|---|---|---|
| `image` | yes | JPEG, PNG or WebP; maximum 5 MB |
| `alt_text` | no | Defaults to an empty string |
| `display_order` | no | Defaults to appending after the last image |

**201 Created**

```json
{
  "id": "0f0f0f0f-0000-0000-0000-000000000000",
  "image_url": "https://res.cloudinary.com/demo/image/upload/studio.jpg",
  "cloudinary_public_id": "mystudio/studios/studio",
  "alt_text": "White cyclorama",
  "display_order": 0,
  "created_at": "2026-11-11T00:00:00Z"
}
```

### `PATCH /api/v1/studios/{slug}/images/{image_id}/` — Bearer, owner/manager

```json
{ "alt_text": "Side view", "display_order": 3 }
```

At least one field is required. Images are scoped to their studio, so an image
ID under another slug returns `404`.

### `DELETE /api/v1/studios/{slug}/images/{image_id}/` — Bearer, owner/manager

**204 No Content.** The row is removed first, then the Cloudinary asset. If the
provider deletion fails the API returns `502`, but the catalogue entry is gone.

### `POST` / `PUT /api/v1/equipment/{id}/image/` — Bearer, owner/manager

`multipart/form-data` with `image`. Uploading replaces the current image and
then deletes the previous Cloudinary asset.

**200 OK**

```json
{
  "id": "6a6a6a6a-0000-0000-0000-000000000000",
  "image_url": "https://res.cloudinary.com/demo/image/upload/light.jpg",
  "cloudinary_public_id": "mystudio/equipment/light"
}
```

### `DELETE /api/v1/equipment/{id}/image/` — Bearer, owner/manager

**204 No Content.** Removing an item with no image is a no-op. Cloudinary
credentials are required in production; development without them returns
`503` from upload/delete endpoints.

## Pagination and filtering

List endpoints use page-number pagination:

- `?page=2`
- `?page_size=50` (default `20`, maximum `100`)

The paginated envelope is always `count`, `next`, `previous`, `results`.

The public catalogue uses django-filter filtersets together with `?search=` and
`?ordering=`; the supported values are listed under each endpoint above.
Unsupported ordering fields are ignored. A non-numeric value for a numeric
filter returns `400`.

## Status codes used

| Code | Meaning |
|---|---|
| 200 | OK |
| 201 | Created |
| 204 | No content (logout, password change, deletes) |
| 400 | Validation or business rule failure |
| 401 | Missing, invalid or blacklisted token |
| 403 | Authenticated but not permitted |
| 404 | Not found (JSON for `/api/` paths) |
| 429 | Rate limit exceeded on login/refresh/verify |
| 502 | Cloudinary rejected an upload or deletion |
| 503 | Health check dependency unavailable, or image hosting not configured |

Unknown `/api/...` routes return `{"detail": "Not found."}` even though the rest
of the site still uses Django's HTML 404 page.
