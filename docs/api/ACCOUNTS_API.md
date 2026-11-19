# Accounts, authentication and authorization (Phase 3)

Live reference: Swagger UI `/api/docs/` (tags `auth`, `me`, `staff`). This page explains the model, security decisions and error handling. Conventions follow `API_CONVENTIONS.md` (no trailing slash, error envelope, `Cache-Control: private, no-store` on every account endpoint).

## 1. Decision recorded (D-6)
| Question | Outcome |
|---|---|
| Method | **Password login** with email *or* phone as identifier, as specified for Phase 3. Phase 1's proposed *SMS-OTP login* is **not implemented**; the OTP machinery built here (phone verification) can power it later. |
| Transport | **HttpOnly session cookie + CSRF header.** No bearer tokens, nothing in `localStorage`. |
| Verification providers | None assumed. Email uses Django's email backend (SMTP). SMS uses a pluggable backend that is **disabled by default in production** (`503 sms_unavailable`). Accounts work without either; `email_verified` / `phone_verified` are flags other features may require. |

Cookie auth requires the browser app and API to be **same-site** (e.g. `shop.example.com` + `api.example.com`; decision D-14). Cross-site deployments need `COOKIE_SAMESITE=None` (HTTPS only) and still work, but third-party-cookie blocking in browsers makes that fragile; prefer same-site. Required config: `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` (the frontend origin), `FRONTEND_BASE_URL`, optionally `SESSION_COOKIE_DOMAIN`/`CSRF_COOKIE_DOMAIN`.

## 2. Browser contract
1. On app start: `GET /auth/csrf` (with `credentials: "include"`) → `{csrf_token}`. Keep it **in memory**.
2. Every unsafe request (`POST/PUT/PATCH/DELETE`) sends `X-CSRFToken: <token>`, **including anonymous ones** (login, register, reset). Missing/wrong → `403 csrf_failed`.
3. `login`, `register` and `refresh` rotate the CSRF token and return the new one in the body: replace the stored token.
4. Cookies: `maral_sid` (session) and `maral_csrf`, both HttpOnly, `SameSite=Lax`; in production `__Secure-` prefixed and `Secure`.

## 3. Endpoints (`/api/v1`)
| Method & path | Auth | Purpose |
|---|---|---|
| `GET /auth/csrf` | – | issue CSRF cookie + token |
| `POST /auth/register` | CSRF | create account **and sign in** → `201 {user, csrf_token}` |
| `POST /auth/login` | CSRF | `{identifier, password}` → `{user, csrf_token}` |
| `POST /auth/logout` | user | revoke session → `204` |
| `POST /auth/refresh` | user | rotate session key + CSRF, extend idle expiry → `{csrf_token, expires_at}` |
| `POST /auth/password/change` | user | `{current_password, new_password}` → `204`; other sessions revoked |
| `POST /auth/password-reset/request` | CSRF | `{identifier}` (email) → always `202` |
| `POST /auth/password-reset/confirm` | CSRF | `{uid, token, new_password}` → `204`; all sessions revoked |
| `POST /auth/email/verification/request` | user | resend link → `202` (1/min) |
| `POST /auth/email/verification/confirm` | CSRF | `{token}` → `204` (works signed out) |
| `POST /auth/phone/verification/request` | user | SMS code → `202` (3 / 10 min / number) |
| `POST /auth/phone/verification/confirm` | user | `{code}` → `204` (5 attempts, 5 min) |
| `GET /auth/sessions` | user | signed-in devices (`is_current`, masked IP, user agent) |
| `DELETE /auth/sessions/{id}` | user | revoke one |
| `POST /auth/sessions/revoke-others` | user | sign out everywhere else |
| `GET/PATCH /me` | user | profile: names, `birth_date`, `language` (fa/en/ar/tr), `currency` (IRT/IRR/TRY/USD/EUR/AED) |
| `PUT /me/email`, `PUT /me/phone` | user | `{email|phone, current_password}`; resets that verification |
| `PUT/DELETE /me/avatar` | user | multipart `avatar` (JPEG/PNG/WebP ≤ 2 MB) |
| `GET/POST /me/addresses` | user | list (empty = `count: 0`) / create |
| `GET/PATCH/DELETE /me/addresses/{id}` | user | other customers' ids → `404` |
| `GET /staff/customers`, `/{id}` | `customers_view` | list (`status`, `q`) / detail (audited) |
| `POST /staff/customers/{id}/suspend` `{reason}`, `/reactivate` | `customers_suspend` | |
| `POST /staff/customers/{id}/unlock` | `customers_unlock` | clear lockout |
| `POST /staff/customers/{id}/revoke-sessions` | `customers_suspend` | |
| `GET /staff/audit-logs` | `auditlog_view` | filters `action` (prefix), `target_id` |

Example — login:
```http
POST /api/v1/auth/login   X-CSRFToken: …
{"identifier": "۰۹۱۲۱۲۳۴۵۶۷", "password": "…"}
→ 200 {"user": {"id": "…uuid", "email": null, "phone": "+989121234567", "first_name": "سارا", "email_verified": false,
        "phone_verified": false, "status": "active", "roles": [], "permissions": [], "created_at": "…Z", "last_login": "…Z",
        "profile": {"avatar_url": null, "birth_date": null, "language": "fa", "currency": "IRT"}}, "csrf_token": "…"}
```
Address body: `label, recipient_name, phone, country (ISO alpha-2), province, city, district, address_line1, address_line2, postal_code, delivery_instructions, is_default`. Persian digits are accepted for phone and postal code and stored normalised (E.164 / ASCII). Postal code is validated per country (IR 10 digits, TR/DE/FR/IQ 5, US ZIP, GB, CA, AF; AE optional; other countries a permissive generic rule).

## 4. Error codes (envelope `{"error": {"code","message","fields?","request_id"}}`)
| Status | `code` | When |
|---|---|---|
| 400 | `validation_error` | bad input; `fields` maps field → messages (`password`, `new_password`, `token`, `current_password`, `postal_code` …) |
| 400 | `otp_invalid`, `otp_expired` | phone code wrong/expired/exhausted (400, **not 401**, so the SPA does not treat it as a lost session) |
| 401 | `invalid_credentials` | wrong identifier/password (identical for unknown accounts) |
| 401 | `not_authenticated` | no/expired cookie, or session invalidated by a password change |
| 401 | `session_expired` | session revoked server-side or past the 30-day cap |
| 403 | `csrf_failed` | missing/invalid CSRF token |
| 403 | `permission_denied` | staff permission missing |
| 403 | `account_suspended`, `account_deactivated` | correct password, blocked account |
| 404 | `not_found` | also for other customers' resources |
| 409 | `already_exists`, `already_verified` | identifier taken / already verified |
| 422 | `address_limit_reached`, `no_phone`, `no_email` | business rules |
| 429 | `rate_limited` + `Retry-After` | lockout, IP throttle, OTP limits, resend cooldown |
| 503 | `sms_unavailable` | no SMS provider configured |

## 5. Security model
* **Passwords**: Argon2id (PBKDF2 verifies legacy hashes), min length 10, common/numeric/similarity-to-identity validators, max 128 chars (hashing-DoS guard). Never logged or returned.
* **Identifiers**: stored canonical (email lower-cased NFKC; phone E.164); unique via filtered indexes; at least one required (CHECK).
* **Login abuse**: per-IP throttle (60/h) **plus** per-account progressive lockout — 5 failures → 1 min, doubling to 1 h; counters live in the DB for real accounts and in the cache for unknown identifiers so the response is identical (`429`). Trade-off: an attacker who knows an identifier can keep it locked; staff can unlock, the cap and IP throttle bound it. Admin login uses the same counters.
* **Enumeration**: login errors and timing are uniform; password-reset request always `202`; registration does reveal "already exists" (UX), but is throttled (10/h/IP).
* **Sessions**: server-side `UserSession` per login (also admin logins). Revoked on logout, password change/reset (all or all-but-current), suspension, manual revoke. Idle expiry 14 days (extended by `/auth/refresh`), absolute cap 30 days. Session key and CSRF token are rotated on login and refresh.
* **Reset tokens**: Django HMAC tokens bound to password hash, email and last login → single use, expire after 1 hour, die on password change or login. Reset confirms mailbox control (`email_verified`) and revokes every session. Only the email channel exists today.
* **Email verification**: signed link (24 h) bound to the current address. **Phone OTP**: 6 digits, stored only as HMAC, 5 min, 5 attempts, newest code only, 3 requests / 10 min / number.
* **Uploads**: avatar fully decoded, format allow-list, pixel cap, EXIF-transposed then re-encoded to 512×512 WebP with a random name (metadata and appended payloads dropped); SVG/GIF rejected.
* **Client IP**: `X-Forwarded-For` ignored unless `TRUSTED_PROXY_COUNT` says how many proxies to trust.
* **Audit log** (`audit_log`, append-only at ORM level): `auth.register|login.success|login.failed|login.blocked|logout`, `auth.account.locked`, `auth.password.changed|change_failed`, `auth.password_reset.requested|completed|invalid_token`, `account.email|phone.changed|verified|…`, `account.profile.updated`, `account.avatar.*`, `auth.session.revoked*`, `staff.customer.viewed|suspended|reactivated|unlocked|sessions_revoked`, `staff.user.created|access_changed`, `staff.groups.add|remove`. Stores ids, IP, user agent, request id and redacted metadata; never passwords, tokens, codes or raw identifiers (failed logins keep a 16-char hash). Production should also revoke UPDATE/DELETE on the table (D-5).

## 6. Authorization
Roles are Django groups built from `apps/accounts/roles.py` and re-synced after every `migrate` (`manage.py sync_roles`). **Super Admin = `is_superuser`** (all permissions).
| Permission (`accounts.*`) | Super Admin | Product | Import | Finance | Order | Support |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `customers_view` | ✔ | | | | ✔ | ✔ |
| `customers_suspend`, `customers_unlock` | ✔ | | | | | ✔ |
| `auditlog_view` | ✔ | | | | | |
| `catalog_manage` | ✔ | ✔ | | | | |
| `imports_manage`, `imports_review_matches` | ✔ | | ✔ | | | |
| `pricing_manage`, `payments_view`, `refunds_manage` | ✔ | | | ✔ | | |
| `orders_view` | ✔ | | | ✔ | ✔ | ✔ |
| `orders_manage` | ✔ | | | | ✔ | |

Catalogue/import/pricing/order permissions exist now so roles are final, but the endpoints they guard arrive with those phases. DRF classes: `IsStaff` + `HasStaffPermission` (view declares `required_permissions` per method; unlisted methods denied), `IsOwner` (object level), `CsrfRequired` (CSRF for anonymous POSTs). Customer data is always filtered by `request.user` in the queryset, so another customer's id is a `404`. `is_staff` alone grants nothing; staff cannot act on themselves or on other staff unless Super Admin. Assign roles in Django admin (`/admin/`, Super Admin) — changes are audited.

## 7. Operations
Run `python manage.py createsuperuser` (email + password) for the first Super Admin. Beat runs `accounts.cleanup_expired` daily (dead sessions, stale OTP rows; audit rows untouched). Tasks that send email (`accounts.send_*`) take only a user id and retry on SMTP errors.

## 8. Known gaps (need your decision)
1. **Staff two-factor authentication (D-6) is not implemented.** Staff sign in with password only. Do not enable staff accounts in production until 2FA exists.
2. Password reset works by **email only**; a phone-only customer who forgets the password cannot self-recover until an SMS provider and a phone-reset flow exist. *(Phase 1's OTP login would remove this problem.)*
3. No SMS provider is integrated; implement `SMSBackend.send` for the chosen provider and set `SMS_BACKEND`.
4. Guest checkout (Q-guest), guest cart/wishlist merge, loyalty tier («عضو طلایی»), account deletion/anonymisation and a privacy policy are undecided and not implemented.
5. Arabic and Turkish email/SMS text falls back to English.
6. Login lockout/unknown-identifier counters use the cache; if Redis is down they reset (per-account lockout stays durable in the DB).
7. **Migration reset:** `accounts/0001_initial` was **replaced** (Phase 2's version used a username-based user). Any database that applied the old file must be recreated (`docker compose down -v`, or drop and recreate the database). Nothing was in production.
