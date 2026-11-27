# Phase 9 – independent code review: findings and fixes

Scope: the five findings of the review of the first Phase 9 ZIP. No frontend file, no previous-phase behaviour and no live
marketplace call was touched or added; no integration is claimed as verified.

| # | Finding | Root cause (confirmed) | Fix |
|---|---|---|---|
| 1 | Snapshot access control | `chat._load()` re-read external listings by id with only `source__is_active`; it ignored `allow_fixture_snapshots`, the staff source switch, listing status and pipeline state. `inquiries._find_product` also lacked the pipeline rule. | New `snapshots.visible_listings(config, code)` is the single rule; used by `search`, `_load` and `_find_product`. `_load` also skips any source (incl. Maral) staff disabled. State-remembered refs go through the same path. |
| 2 | Inquiry throttling | `self.throttle_scope = "ai_inquiry"` was assigned inside `post()`, after DRF had already run throttling with `ai_read`. | `InquiryListView.throttle_scope` is a property resolved from the HTTP method (POST → `ai_inquiry`, else `ai_read`) before the handler runs. |
| 3 | Availability validation too broad | `any_in_stock` accepted "X is in stock" for a mixed result. | `validate_reply(all_in_stock, in_stock_sizes)`: sentence-level; an availability claim needs *every* product `IN_STOCK`, and a named size must be in stock for every product. Facts carry `in_stock_sizes` per product (empty for snapshots). Availability/selection replies were already backend-only. |
| 4 | Image licensing | `if source.allows_image_copy or config.show_external_images` let a staff switch override the source licence. | `_images` checks only `source.allows_image_copy`. The `show_external_images` field is removed (migration `0002`); API/config no longer expose it. |
| 5 | History safety | History replayed both roles, unsanitised and unflagged, as `role: text` lines; flagged current messages were sent to the model. | History = earlier *customer* messages of this conversation only; flagged ones (flag stored on the message + re-check on read) dropped; sanitised, 300-char cap; no assistant/marketplace text. Anthropic adapter quotes them as JSON in `<earlier_customer_messages>`, `<`/`>` neutralised, single user turn; system prompt rule added; flagged current message replaced by a placeholder. |

## Files changed (relative to the reviewed ZIP)
Code (`maral_backend/`):
* `apps/ai_assistant/services/sources/snapshots.py` – `visible_listings`, licence-only `_images`
* `apps/ai_assistant/services/chat.py` – `_load` visibility, `_history`, placeholder for flagged message, flag persisted, new `validate_reply` arguments
* `apps/ai_assistant/services/inquiries.py` – uses `visible_listings`
* `apps/ai_assistant/services/safety.py` – sentence-level, size-aware availability validation
* `apps/ai_assistant/services/replies.py` – `in_stock_sizes` in the facts
* `apps/ai_assistant/services/providers/anthropic.py`, `providers/prompts.py`, `providers/base.py` – quoted-history presentation, defanging, prompt rule
* `apps/ai_assistant/views/public.py` – throttle scope property
* `apps/ai_assistant/models.py` – `show_external_images` removed
* `apps/ai_assistant/migrations/0002_remove_show_external_images.py` – new

Tests: `tests/test_ai_review_fixes.py` (new, 47 tests), `tests/test_ai_discovery.py` (one test rewritten: it asserted the old override behaviour).

Docs: `docs/ai/PHASE9_ARCHITECTURE_AND_DECISIONS.md`, `PHASE9_SETUP_AND_OPERATIONS.md`, `PHASE9_API.md`, `PHASE9_FRONTEND_INTEGRATION.md`, `PHASE9_SOURCES_AND_LIMITATIONS.md`, this file, `docs/PHASE9_SUMMARY_AND_STATUS.md`.

## Regression tests (`tests/test_ai_review_fixes.py`)
Run against the *unfixed* code, 38 of the 47 fail (the other 9 are positive controls that must keep passing).
1. Visibility: hidden-by-{fixture, disabled source, inactive source, inactive listing, failed pipeline, archived pipeline} through compare, chat availability/select/compare, remembered conversation state, Maral switch/visibility, inquiries; fixture allowed when staff allow; one shared function.
2. Throttle: POST gets 429 on the third request (before validation) while GETs keep `ai_read`; scope per method; rates exist.
3. Availability: mixed/any-sentence/size-specific/negation cases; end-to-end fallback with a fake model; snapshots never contribute in-stock sizes; availability replies make no model call.
4. Images: config model has no switch; staff PATCH cannot enable; images only for `allows_image_copy` sources; migration present.
5. History: flagged excluded (and flag stored); customer-only; unflagged legacy rows re-checked; sanitised/truncated; flagged current message withheld; Anthropic payload structure (single user turn, one of each delimiter, JSON-quoted list, no `<`, system prompt rule); no system-prompt injection.

## Results (SQLite smoke mode, no `mssql-django`/`pyodbc`)
| | Passed | Failed |
|---|---|---|
| Original ZIP (baseline) | 934 | 8 |
| Phase 9 first delivery | 1079 | 8 |
| **After review fixes** | **1126** | **8** |

Pre-existing failures (identical to baseline, unrelated): `tests/test_user_model.py` ×4 (`create_user()` gets `email` twice) and
`tests/test_settings.py` ×4 (need `mssql-django`). **Newly introduced failures: none.**
Ruff: Phase 9 files and `config/`, `apps/accounts` – all checks passed; Phase 9 files formatted. Whole backend: 30 findings, identical to the
original ZIP (none in Phase 9). `makemigrations --check`: no changes.
Still true: no live marketplace search and no real AI provider call has been verified; SQL Server was not available.


---

# Round 2 – hardening: untrusted product data passed to Anthropic

**Finding.** Customer messages and history were defanged, but the serialised `facts` object carried product-derived text
(`replies.product_facts()`: title, brand, sizes, colours; filter words; comparison rows) without equivalent protection, for both
internal catalogue products and imported snapshots, and was serialised with plain `json.dumps`.

**Fix (no change to API responses or to any earlier behaviour).**
1. `replies.py`: every free-text value moves under `untrusted_text` / `untrusted_values` (products, applied filters, comparison),
   sanitised by the new `safety.safe_fact_text`; authoritative values keep their original keys and values. Free filter words are dropped
   when the customer message was flagged as an injection. `in_stock_sizes` use the strict `safety.safe_size_token` allow-list.
2. `safety.py`: new `safe_fact_text`, `safe_size_token`, `safe_json` (escapes `<`, `>`, `&`, U+2028/2029) and `WITHHELD`.
3. `providers/anthropic.py`: facts and history are serialised with `safe_json`; `_prepare_facts` re-sanitises at the provider
   boundary, relocates legacy flat text fields, re-applies the size allow-list and strips `template_text`.
4. `providers/prompts.py`: new rule describing `untrusted_text` / `untrusted_values` as data only.
5. `chat.py`: passes `suspected_injection` and uses `replies.comparison_facts` for compare turns.

**Regression tests** – `tests/test_ai_untrusted_facts.py` (26 tests; 23 fail on the previous code, 3 are controls):
six attack payloads (fake delimiters, role lines, JSON forgery, Unicode line/bidi separators, tag/role tokens, an injection phrase)
applied to the title, brand, size and colour of an imported snapshot **and** of an internal catalogue product, asserted on the real
HTTP body sent through the Anthropic adapter (one of each delimiter, no angle brackets/newlines in the facts block, authoritative price /
status / availability identical to the API card, forged values only as inert strings under `untrusted_text`); withheld injection
phrases; size allow-list; filters and flagged-message keyword dropping; comparison rows for both product kinds; the provider boundary
with raw/legacy-shaped facts; `safe_fact_text` / `safe_json` properties; model replies still cannot override facts.

**Files changed (relative to the ZIP reviewed in round 2).**
`maral_backend/apps/ai_assistant/services/safety.py`, `services/replies.py`, `services/chat.py`,
`services/providers/anthropic.py`, `services/providers/prompts.py`; new `maral_backend/tests/test_ai_untrusted_facts.py`;
docs: this file, `ai/PHASE9_ARCHITECTURE_AND_DECISIONS.md`, `ai/PHASE9_SETUP_AND_OPERATIONS.md`, `ai/PHASE9_FRONTEND_INTEGRATION.md`,
`PHASE9_SUMMARY_AND_STATUS.md`. No migration, no frontend file, no live call.

**Results (SQLite smoke mode):** 1126 → **1152 passed**, **8 failed** (the same pre-existing 8: `test_user_model.py` ×4,
`test_settings.py` ×4); newly introduced failures: none. Ruff: Phase 9 files, `config/`, `apps/accounts` all passed; whole backend 30
findings, identical to the original ZIP. `makemigrations --check`: no changes.
