CREATIVE ENGINE — PHASE C2
SCRIPT-FIRST SMART SHOT PLANNER

Read current repository truth and operating guidelines:
- docs/UPGRADE_RULES.md
- docs/UPGRADE_STATUS.md
- docs/CREATIVE_AI_GATEWAY.md

Require:
creative-c1-complete

Implement C2 ONLY.

Do not change the approved user script wording.
Do not redesign the Django/Celery architecture.
Do not start stock media search (strictly Phase C3).
Do not implement multimodal video ranking (strictly Phase C4).
Do not rewrite FFmpeg video renderer yet (strictly Phase C5).

==================================================
GOAL
==================================================

Transform an approved Telugu script into an immutable, versioned, multi-shot visual timeline plan (`TimelinePlan`) with semantic beats, dynamic shot durations, visual intentions, and validated English stock queries.

Approved Script (Immutable UTF-8)
    ↓
Pass 1: Semantic Beat & Shot Decomposition (AI or Local Fallback)
    ↓
Pass 2: Audio-Aligned Timing Reflow (TTS Word Boundaries)
    ↓
Dynamic Programming Optimizer (`c2-dp-v2`)
    ↓
Versioned `TimelinePlan` (JSON IR v1)
    ↓
Human Review UI (Story Tab) & Approval

==================================================
1. IMMUTABLE SCRIPT SOURCE PERSISTENCE
==================================================

Add Django model `core.models.ScriptSourceVersion` (Migration `core.0003_creative_c2_timeline`):
- `project` (FK to Project)
- `source_text` (UTF-8 text)
- `source_hash` (SHA-256 digest of exact script)
- `language` (default 'te')
- `created_at`, `approved_at`
- An approved script source hash is strictly immutable. Planning never modifies `Project.body` or scene narration text directly.

==================================================
2. TIMELINE IR V1 & SCHEMA SPECIFICATION
==================================================

Create `TimelinePlan` model in `core/models.py`:
- `project` (FK to Project)
- `script_source` (FK to ScriptSourceVersion)
- `version` (integer version counter)
- `state` ('draft', 'reviewed', 'approved', 'superseded')
- `plan_ir` (JSONField storing full Timeline IR v1)
- `stats` (duration, shot count, cuts per minute)

Timeline IR v1 JSON structure:
- `ir_version`: '1.0'
- `script_hash`: SHA-256
- `style_profile`: 'normal_telugu', 'gen_z', 'documentary', 'cinematic', etc.
- `beats`: List of semantic beats containing:
  * `beat_id`: Stable identifier
  * `source_span`: [start_char, end_char] in exact script
  * `source_text_te`: Exact Telugu text for this beat
  * `visual_purpose`, `emotion`, `importance`
- `shots`: List of planned visual shots:
  * `shot_id`: Stable identifier
  * `beat_id`: Parent beat reference
  * `visual_intent_en`: Concise visual intention in English
  * `search_queries_en`: List of 2-5 word English search queries
  * `framing`, `movement`, `intensity`
  * `timing`: `start_time`, `end_time`, `duration` (quantized to 30 fps)
  * `is_locked`: Boolean flag preserving human overrides

==================================================
3. STRICT TEXT DOMAIN SEPARATION & ENGLISH QUERY VALIDATOR
==================================================

Strictly maintain 5 distinct text domains:
1. `source_text_te`: Exact approved Telugu script.
2. `spoken_text_te`: Approved spoken representation for TTS.
3. `roman_caption`: Display-only transliteration for captions.
4. `visual_intent_en`: English visual description.
5. `search_queries_en`: Strict English stock search terms.

English Query Validator (`studio/stock_queries.py`):
- Validate all search queries before saving.
- REJECT any query containing:
  * Telugu Unicode characters (U+0C00 to U+0C7F).
  * Obvious Roman Telugu markers or transliterations.
  * Punctuation, URLs, or AI instruction keywords.
  * Empty strings or queries exceeding 5 words.
- All provider queries MUST be clean English visual terms (e.g. "farmer in green field", "crowded hyderabad traffic", "modern technology laboratory").

==================================================
4. TWO-PASS TIMING & DETERMINISTIC OPTIMIZER
==================================================

1. Pass 1 (Provisional Timing):
   - Estimates provisional shot durations based on Telugu syllable/character counts.
2. Pass 2 (Audio Reflow):
   - Aligns shot boundaries with actual TTS audio word boundaries from Celery voice jobs.
   - Only maps words if they match the approved script in exact order.
3. Deterministic DP Optimizer (`c2-dp-v2` in `studio/shot_planner.py`):
   - Quantizes all cuts to project FPS (30 fps).
   - Never cuts in the middle of a spoken word.
   - Cuts inside natural sentence pauses, punctuation marks, or semantic beat transitions.
   - Eliminates gaps: ensures continuous visual coverage with zero blank frames.
   - Enforces style profile duration constraints (e.g. min 1.5s, max 5.0s per shot).

==================================================
5. CELERY WORKFLOW & TRANSACTIONAL OUTBOX
==================================================

Add Celery jobs in `studio/tasks.py`:
- `shot_plan`: Validates script -> decomposes beats -> creates provisional plan.
- `shot_reflow`: Consumes voice timing -> runs DP optimizer -> updates plan.
- Use PostgreSQL transactional outbox pattern. Pass only job IDs to Celery.

==================================================
6. HUMAN REVIEW UI (STORY TAB)
==================================================

In Django templates (`templates/studio/partials/tab_story.html`) & jQuery (`studio/static/js/shot-plan.js`):
- Script confirmation textarea with UTF-8 hash calculation.
- Style profile selector.
- Plan visualizer: displays beats, shot cards, visual intents, English queries, and timing chips.
- Interactive controls:
  * Edit shot descriptions and search queries.
  * Split / merge beats and shots.
  * Lock / unlock shots against automatic reflow.
  * Reflow timing button.
  * Save & Approve Plan button.
- Legacy Scene Compatibility Bridge: Ability to map planned shots back to legacy scenes if desired.

==================================================
7. TESTS & REGRESSION
==================================================

- Test ScriptSourceVersion hash immutability and exact text preservation.
- Test Timeline IR v1 schema validation.
- Test English Query Validator against Telugu Unicode, Roman Telugu, and malformed inputs.
- Test DP optimizer timing quantization, zero-gap coverage, and word boundary preservation.
- Test Celery job execution and human review API endpoints (`GET/PATCH /api/projects/{id}/shot-plan`).
- Ensure full automated regression pass across the test suite.