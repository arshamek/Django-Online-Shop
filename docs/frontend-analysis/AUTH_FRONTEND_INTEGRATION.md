# Auth & account — mapping to the React screens (Phase 3)

Source of truth for screens: `src/routes/account.tsx`, `src/routes/checkout.tsx`, `src/lib/shop-store.tsx` (Phase 0 inventory). **The frontend has no login, register, reset or verification screens** (FRONTEND_GAPS #12) — the account page is a permanent demo. Those screens must be built; this page defines their API contract. No frontend file was changed in Phase 3.

## 1. Existing screens → endpoints
| Screen / element (today) | Becomes | Endpoint | States to implement |
|---|---|---|---|
| Account header «سارا محمدی / عضو طلایی» | name from profile (tier is not modelled) | `GET /me` → `full_name` | skeleton while loading; 401 → login screen |
| Account ▸ اطلاعات حساب: نام، موبایل، ایمیل | editable fields | `GET/PATCH /me` (`first_name`,`last_name`); phone/email via `PUT /me/phone`, `PUT /me/email` (ask current password) | field errors from `error.fields`; success toast on 200; badge «تأیید نشده» from `phone_verified`/`email_verified` |
| Account ▸ تاریخ تولد | date field | `PATCH /me` `birth_date` (ISO date; the UI keeps Jalali, converts client-side) | future dates → 400 |
| Account ▸ زبان (`setLang`) | persisted preference | `PATCH /me` `language` (`fa|en|ar|tr`) (+ `currency` selector if wanted) | optimistic update, revert on error; guests keep local storage value |
| Account ▸ آدرس‌ها (one read-only block) | list + add/edit/delete + default | `GET/POST /me/addresses`, `PATCH/DELETE /me/addresses/{id}` | **empty list** (`count: 0`) → «هنوز آدرسی ندارید» + add button; spinner on save; default badge; limit 20 → 422 `address_limit_reached` |
| Account ▸ avatar circle «س» | uploaded image | `PUT/DELETE /me/avatar` (`FormData`, field `avatar`) | client-side size check (2 MB) + server errors under `fields.avatar`; fallback to initial letter when `avatar_url` is null |
| Account ▸ خروج + «ورود دوباره» screen | real logout, then the login screen | `POST /auth/logout` | disable button while pending; clear in-memory user/CSRF |
| Checkout step 0: نام و نام خانوادگی، موبایل، ایمیل (اختیاری) | prefill from `/me` when signed in | `GET /me` | keep the guest path unchanged (guest checkout is undecided, Q-guest) |
| Checkout step 1: شهر، آدرس کامل، کد پستی | choose saved address or create one | `GET/POST /me/addresses` | field names map: `city→city`, `addr→address_line1`, `zip→postal_code`; keep the existing client regex for mobile (`^(09|۰۹)…`) — the server accepts the same input and also international numbers |
| Header account icon | goes to `/account` or login | `GET /me` | `isAuthenticated` derived from a successful `/me` |

## 2. New screens required
| Screen | Fields | Calls | Notes |
|---|---|---|---|
| Login | identifier (email or mobile), password | `POST /auth/login` | 401 `invalid_credentials` → generic message; 429 → show countdown from `Retry-After`; 403 `account_suspended` → «با پشتیبانی تماس بگیرید» |
| Register | mobile and/or email, password, first/last name, language | `POST /auth/register` | 409 `already_exists` with `fields.email/phone`; password rules from `fields.password` (list of messages) |
| Forgot password | email | `POST /auth/password-reset/request` | always show «اگر حساب وجود دارد، ایمیل ارسال شد» |
| Reset password (`/reset-password?uid=&token=`) | new password | `POST /auth/password-reset/confirm` | `fields.token` error → «لینک نامعتبر یا منقضی» + link to request again; success → login screen |
| Verify email (`/verify-email?token=`) | – | `POST /auth/email/verification/confirm` | works signed out; show success/failure |
| Verify phone | 6-digit code | `POST /auth/phone/verification/request` then `/confirm` | 503 `sms_unavailable` → hide the option; resend cooldown from 429 `Retry-After`; `otp_invalid`/`otp_expired` inline |
| Change password / devices (account ▸ امنیت) | current, new / session list | `POST /auth/password/change`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `POST /auth/sessions/revoke-others` | mark `is_current`; after a password change the other devices are signed out |

## 3. Client behaviour (framework-agnostic)
```ts
// api.ts - cookies only, token kept in memory (never localStorage)
const API = import.meta.env.VITE_API_URL + "/api/v1";
let csrf: string | null = null;
export class ApiError extends Error {
  constructor(public status: number, public code: string, public fields?: Record<string, unknown>, public retryAfter?: number) { super(code); }
}
async function ensureCsrf() { if (!csrf) csrf = (await (await fetch(`${API}/auth/csrf`, { credentials: "include" })).json()).csrf_token; }

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const unsafe = method !== "GET" && method !== "HEAD";
  if (unsafe) await ensureCsrf();
  const res = await fetch(API + path, {
    ...init, method, credentials: "include",
    headers: { Accept: "application/json", ...(init.json !== undefined && { "Content-Type": "application/json" }), ...(unsafe && { "X-CSRFToken": csrf! }), ...init.headers },
    body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = data.error ?? {};
    if (e.code === "csrf_failed") csrf = null;                       // refetch once, then retry in the caller
    if (e.code === "session_expired" || (res.status === 401 && e.code === "not_authenticated")) onSignedOut(e.code);
    throw new ApiError(res.status, e.code ?? "unknown", e.fields, Number(res.headers.get("Retry-After")) || undefined);
  }
  if (data.csrf_token) csrf = data.csrf_token;                       // login / register / refresh rotate it
  return data;
}
```
* **Loading**: use the existing `ui/skeleton`/button spinner components for `/me`, addresses and form submits (Phase 0: not used anywhere yet).
* **Validation errors**: render `error.fields[name]` (array of strings, or nested for lists) under the matching input; `non_field_errors` as a form banner.
* **Expired session**: on `session_expired` show «نشست شما به پایان رسید، دوباره وارد شوید» and open Login; on plain `not_authenticated` just open Login. Both mean "signed out".
* **Keep-alive**: call `POST /auth/refresh` on app start when `/me` succeeds and then every ~12 h while the tab is open (it also rotates the CSRF token).
* **Unauthorized/forbidden**: 401 → login; 403 `permission_denied` → «دسترسی ندارید»; 404 on `/me/addresses/{id}` → remove the row from local state.
* **Rate limits**: 429 → disable the submit button and show `Retry-After` seconds.
* **Successful updates**: replace local user state with the response body of `PATCH /me`, address responses, etc. (each returns the full resource).

## 4. Deployment prerequisites for the frontend
Same-site hosting (e.g. `shop.example.com` → `api.example.com`), backend `CORS_ALLOWED_ORIGINS` + `CSRF_TRUSTED_ORIGINS` = the shop origin, `fetch(..., { credentials: "include" })` everywhere. If the SPA is served via the TanStack server (BFF, decision D-14), proxy `/api/*` through it and drop CORS entirely. See `docs/api/ACCOUNTS_API.md` §1.
