CREATIVE ENGINE — PHASE C1
RELIABLE AI MODEL GATEWAY & TOKEN BUDGET SYSTEM

Read current repository truth and operating guidelines:
- docs/UPGRADE_RULES.md
- docs/UPGRADE_STATUS.md

Implement C1 ONLY.

Do not change the user's script.
Do not redesign the Django/Celery architecture.
Do not start C2 shot planning.
Do not start multi-provider stock media search.
Do not start video rendering changes.

==================================================
GOAL
==================================================

Replace fragile single-model, substring-matching Gemini API calls with a resilient, observable, multi-model AI Gateway layer (`creative_ai/`).

Inputs:
Raw user prompt / script / topic
    ↓
creative_ai.gateway
    ↓
Task Policy & Token Budget Check
    ↓
Circuit Breaker & Fallback Chain
    ↓
Provider Adapter (Gemini REST)
    ↓
Pydantic Schema Validation & Repair
    ↓
Attempt Logging (`creative_ai_attempts`)
    ↓
Typed Result to Django / Celery Caller

==================================================
1. GATEWAY ARCHITECTURE & TASK POLICIES
==================================================

Create `creative_ai/` package:
- `creative_ai/gateway.py`
- `creative_ai/policies.py`
- `creative_ai/registry.py`
- `creative_ai/circuit_breaker.py`
- `creative_ai/token_budget.py`
- `creative_ai/usage.py`
- `creative_ai/schemas.py`
- `creative_ai/errors.py`
- `creative_ai/providers/gemini.py`

Task policies:
Define policies for:
- `research` (grounded web search)
- `storyboard_draft` (exact script storyboard generation)
- `script_analysis` (semantic beat & shot decomposition)
- `search_query_generation` (English visual query extraction)
- `candidate_reranking` (visual relevance scoring)
- `creative_review`

Each policy defines:
- Primary model ID & fallback model IDs
- Input/output token limits & temperature
- Grounding configuration (Google Search)
- Timeout seconds & max retries
- Schema validation class

==================================================
2. RESILIENCE, RETRIES & CIRCUIT BREAKER
==================================================

1. Dynamic Model Discovery:
   - Discover available Gemini models via `models.list`.
   - Cache capability and availability for 15 minutes by credential fingerprint.
   - Never run discovery at Django startup.

2. Retry Policy:
   - Explicit retry classification: retry ONLY 408 (Request Timeout), 429 (Rate Limit), 5xx (Server Errors), and network connection timeouts.
   - Bounded exponential backoff with jitter (e.g. 1s, 2s, 4s).
   - Fast-fail immediately on: 401/403 (Authentication/Permission), Quota exceeded / Payment required, 400 (Invalid argument / schema mismatch), and Safety blocks.
   - Safety blocks must NEVER switch models or retry.

3. Distributed Circuit Breaker:
   - State stored in Redis: CLOSED, OPEN, and HALF_OPEN.
   - 60-second cooldown period when tripped by consecutive failures.
   - Single-probe canary test in HALF_OPEN state before closing circuit.
   - Circuit outages must fail gracefully and fall back to local deterministic routines.

==================================================
3. TOKEN BUDGETING & EXACT SCRIPT INVARIANT
==================================================

1. Token Preflight:
   - Preflight inputs > 500 characters using Gemini `countTokens`.
   - Use conservative local token estimation if provider token counting is unavailable.
   - Context shedding: if prompt exceeds budget, shed non-authoritative context (e.g. research notes) first, then recount.
   - NEVER silently truncate the user's approved script text.

2. Exact Script Preservation Invariant:
   - When a user supplies an approved script, the concatenated narration text of all output scenes must preserve the EXACT wording, punctuation, and ordering of the source script.
   - If AI fails or alters words, trigger automatic local deterministic segmentation fallback.

==================================================
4. SCHEMA VALIDATION, REPAIR & CACHING
==================================================

- Validate all AI JSON outputs strictly with Pydantic.
- If output schema is invalid, allow at most ONE repair generation prompt before falling back.
- Result Caching: Cache successful structured results in Redis by `(task, prompt_version, model_order, input_hash, schema_version)`. Never cache failures.

==================================================
5. OBSERVABILITY & DATABASE PERSISTENCE
==================================================

Create Django model `creative_ai_attempts` in `core/models.py` (Migration `core.0002_creative_ai_gateway`):
- `id` (bigint PK)
- `logical_request_id` (varchar 64)
- `project_id` (FK to projects.id, nullable)
- `task` (varchar 48)
- `input_hash` (varchar 64)
- `prompt_version`, `schema_version` (varchar 40)
- `requested_model`, `actual_model` (varchar 128)
- `attempt` (smallint)
- `latency_ms`, `input_tokens`, `output_tokens`, `cached_tokens` (integer)
- `status` (varchar 24)
- `error_category` (varchar 48)
- `fallback_used` (boolean)
- `created_at` (timestamptz)

Composite indexes:
- `(project_id, created_at)`
- `(task, created_at)`
- `(actual_model, created_at)`

Do NOT log raw prompts, private text, or API secret keys to database or logs.

==================================================
6. SAFE DIAGNOSTICS & UI SETTINGS
==================================================

- Add endpoint `GET /api/ai/diagnostics`: returns configured model IDs, circuit states, task limits, and aggregated usage stats. Never leak raw API keys.
- Update Settings Modal in Studio UI to allow inspecting AI provider health, diagnostics, and primary/fallback model selection.

==================================================
7. TESTS & REGRESSION
==================================================

- Test gateway retry logic, backoff, and circuit transitions.
- Test token budgeting, context shedding, and exact script preservation invariant.
- Test Pydantic schema parsing and repair loop.
- Test diagnostic API and secret protection.
- Ensure 100% passing tests across existing Django suite.