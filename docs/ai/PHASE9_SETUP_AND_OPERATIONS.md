# Phase 9 – Setup, configuration and operations

## Install
```bash
cd maral_backend
pip install -r requirements/dev.txt          # no new dependency was added in Phase 9
python manage.py migrate                      # adds ai_assistant tables + the accounts.ai_manage permission
python manage.py sync_roles                   # (also runs after migrate) grants ai_manage to Product Manager
```
Migrations: `apps/ai_assistant/migrations/0001_initial.py`, `0002_remove_show_external_images.py`, `apps/accounts/migrations/0003_alter_staffpermissions_options.py`
(metadata only). Super Admin (superuser) has every permission.

## Environment variables (new in Phase 9, all in `.env.example`)
| Variable | Default | Meaning |
|---|---|---|
| `AI_API_KEY` | empty | Anthropic API key. **Server-side only**; never stored in the database, never returned by an API. Empty → demo mode. |
| `AI_MODEL` | empty | Model id used when the staff config's `model` is blank. Empty (and no staff model) → demo mode. |
| `AI_BASE_URL` | `https://api.anthropic.com` | Must be `https://`. |
| `THROTTLE_RATE_AI_CHAT` | `20/min` | chat, recommendations, compare (per user) |
| `THROTTLE_RATE_AI_READ` | `120/min` | conversation / inquiry reads |
| `THROTTLE_RATE_AI_INQUIRY` | `10/hour` | creating purchase inquiries |

`AI_PROVIDER` (existing) is unrelated: the Phase 9 provider is chosen in the staff config (`provider`: `mock` or `anthropic`).

## Turning the assistant on
1. **Demo mode (default, no credentials):** nothing to do. `provider = mock`; the deterministic assistant searches the real
   catalogue and stored imports and says so (`demo_mode: true`).
2. **Real provider:** set `AI_API_KEY` and `AI_MODEL`, then as staff `PATCH /api/v1/staff/ai/config {"provider": "anthropic"}` and
   `POST /api/v1/staff/ai/provider-test`. Until the test succeeds the status shows `configured: true, verified: false`. If the key
   or model is missing the system stays in demo mode and says so (`provider.note`).
3. **Sources:** `enabled_sources` (default all three). Trendyol/Namshi show stored imports only; see the limitations document.
4. **Pricing:** external prices need a published Phase 6 rule set and fresh rates; otherwise they show as `incomplete`.

## Staff configuration (`/api/v1/staff/ai/config`, also the Django admin)
`enabled`, `provider`, `model`, `supported_languages`, `extra_instructions` (tone only; cannot relax the built-in rules),
`enabled_sources`, `daily_message_limit` (100), `max_input_chars` (1000), `context_messages` (8), `retention_days` (30),
`max_results` (6), `price_max_age_hours` (72), `provider_timeout_seconds` (20), `allow_fixture_snapshots` (false),
`expose_marketplace_urls` (false). Marketplace images are shown only when the source's `allows_image_copy` is true (no config switch). Every change is audited (`ai.config_updated`).

## Retention and deletion
* Customers delete a conversation (and its messages) with `DELETE /ai/conversations/{id}`.
* Conversations idle longer than `retention_days` are deleted by the Celery beat task `ai_assistant.purge_expired_conversations`
  (daily) or manually: `python manage.py purge_ai_conversations`.
* `UsageRecord` keeps counters only (no message text) and survives conversation deletion.
* Purchase inquiries are business records and are not deleted by retention (their conversation link is set to null).

## Monitoring
`GET /staff/ai/status`: provider mode/verification, per-source status and stored listing counts, 24 h / 7 d usage by status,
errors by code, tokens, suspected prompt injections, open inquiries. Logs never contain keys or message text beyond the
project's normal request logging.

## Tests
```bash
cd maral_backend
TEST_DATABASE=sqlite python -m pytest tests/test_ai_nlu.py tests/test_ai_discovery.py \
    tests/test_ai_chat_api.py tests/test_ai_inquiries_staff.py tests/test_ai_review_fixes.py tests/test_ai_untrusted_facts.py -q      # Phase 9 only (218 tests: 145 original + 47 review regressions + 26 untrusted-facts regressions)
TEST_DATABASE=sqlite python -m pytest -q                               # everything
ruff check apps/ai_assistant tests/test_ai_*.py tests/ai_helpers.py
```
All Phase 9 tests use the mock provider, an injected fake HTTP transport and synthetic database rows; none touches the network or a
marketplace. They are **mock tests**, not live integration tests (no live test exists).
