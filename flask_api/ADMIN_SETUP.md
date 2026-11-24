# Admin access and moderation settings

## Who can use the admin endpoints
There is **no login on this service**. Admins sign in on BONC (the .NET backend), and every
admin request is checked one of these ways (`app/admin_auth.py`, `check_admin`):

| How | When | Header |
|---|---|---|
| BONC admin sign-in | `BONC_AUTH_VERIFY_URL` is set | `Authorization: Bearer <BONC token>` |
| Ops / scripts token | `ADMIN_TOKEN` is set | `X-Admin-Token: <ADMIN_TOKEN>` |
| Nobody checked | `AUTH_DISABLED=1` | none |

With a BONC token, each request is forwarded to the .NET endpoint at `BONC_AUTH_VERIFY_URL`
(the `bonc-auth` package). It must answer **200** with `{"adminId", "role", "sessionId"}` for a
live admin session, **401** when the token is invalid or the session ended, **403** when the
account is not an admin. Anything else - timeout, 5xx, a body without `adminId` - is refused
with `503 auth_unavailable`; it never lets a request through. HTTPS is required except for
`localhost`. `GET /v1/admin/auth/me` returns the verified identity, which is the quickest way
to check the wiring; `GET /ready` reports `admin_verifier: bonc` once it is set.

**Using .NET's existing `getUserDetail` instead** (`BONC_AUTH_STYLE=user-detail`). Until .NET
has a purpose-built verifier, point the URL at the endpoint it already has:

```
BONC_AUTH_VERIFY_URL=https://api.boncnetwork.com/api/userService/getUserDetail
BONC_AUTH_STYLE=user-detail
BONC_AUTH_ADMIN_ROLES=Admin,SuperAdmin   # the default
```

Each check is `GET getUserDetail?userRowId=<the token's UserRowId claim>` with the admin's
token. .NET validates the token (401 when invalid or expired); this service then lets the
request through only if the returned record is **the same user**, is active, and has one of
`BONC_AUTH_ADMIN_ROLES` in `UserRoles`. A signed-in non-admin is `403 not_admin`; a
deactivated account is `401`; a record for a different user, `Success: false`, or any other
status is `503 auth_unavailable`. The admin id recorded on blocks is their `UserRowId`;
`session_id` in `/v1/admin/auth/me` is `null`, because the token carries none.

With none of the three set, admin endpoints answer `404` (disabled).

**Caching.** A token .NET accepted is trusted for `BONC_AUTH_CACHE_SECONDS` (default 60)
before it is checked again, so one page of the dashboard costs one call to .NET rather than
twenty-five. The trade-off: signing out of BONC takes up to that long to reach this service.
Refusals and failures are never cached. Set it to `0` to check every request.

**CORS.** If the BONC admin frontend calls `/v1/admin/*` from the browser on its own domain,
list that origin in `ADMIN_CORS_ORIGINS` (comma-separated, exact, e.g.
`https://admin.boncnetwork.com`). Only `/v1/admin/*` gets CORS headers, only for those
origins, allowing the `Authorization` and `Content-Type` headers and no cookies. Calls made
server-to-server from .NET need none of this.

## The Admin tab on /articles (DEMO_PAGE=1)
It asks for a token and keeps it in that browser tab only (sessionStorage):
* server with `BONC_AUTH_VERIFY_URL` - paste the admin access token from your BONC sign-in;
* otherwise - paste the server's `ADMIN_TOKEN`;
* with `AUTH_DISABLED=1` it asks for nothing.

**Sign out** removes the token from the tab. A rejected or expired token sends you back to
the sign-in screen.

## Local setup (PowerShell, from `flask_api`)
```powershell
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(16))"   # paste after ADMIN_TOKEN= in .env
python -m flask --app wsgi run --port 8000
```
Open http://localhost:8000/articles -> **Admin** and paste that token.

## Moderation settings
* A popup with the AI-moderation ON/OFF switch and the level -> Save.
* One setting covers **every** content type (Articles, Add Business, Videos, Requests,
  Proposals, Business Proposals, and the rest of `ContentType`). The backend enforces it; the
  page only shows it.
* The setting is saved in `data/admin.db`, so it survives refreshes and restarts.

## Production hosting
* Set the same names as real environment variables in your host, use HTTPS, keep `data/` on
  disk that is backed up, and never commit `.env`.
* Run **one server process** (e.g. `waitress-serve --port=8000 wsgi:app`). With several
  gunicorn workers, a settings change reaches only the worker that handled it until the others
  restart.

## What turning moderation OFF does
Only the moderation checks stop; every request is allowed and the result says
`decided_by: disabled`. Admin access, input validation, upload size/type checks and the model
reload endpoint are unchanged. When ON, `reject` and `revise` are never published, and a
moderator error publishes nothing.

## Upgrading from the email/password login
The `admin` and `reset_code` tables in an existing `data/admin.db` are no longer read or
written; they are left in place rather than deleted. `SECRET_KEY`, `SESSION_COOKIE_SECURE`,
`ADMIN_EMAIL`, `ADMIN_INITIAL_PASSWORD` and the `EMAIL_*` settings are no longer used and can be
removed from `.env`.
