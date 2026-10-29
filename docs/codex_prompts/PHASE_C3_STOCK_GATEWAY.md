CREATIVE ENGINE — PHASE C3

MULTI-PROVIDER STOCK VIDEO SEARCH, LICENSING AND SHOT ASSIGNMENT







Implement C3 ONLY.



Do NOT implement multimodal video understanding.

Do NOT implement AI video ranking.

Do NOT implement generative video.

Do NOT change the user's script.

Do NOT change C2 shot timing.

Do NOT rewrite the FFmpeg renderer.

Do NOT begin C4.



==================================================

PRIMARY OBJECTIVE

==================================================



Search multiple licensed media providers for every approved C2 planned shot.



Input:



approved TimelinePlan

    ↓

approved shot

    ↓

search_queries_en

    ↓

multiple providers

    ↓

normalized candidates

    ↓

license and quality filtering

    ↓

human review

    ↓

selected candidate assigned to shot ID



Only use:



search_queries_en



Never use:



source_text_te

spoken_text_te

roman_caption



as provider search queries.



==================================================

1. PROVIDERS

==================================================



Keep the existing Pixabay integration.



Add:



1. Pexels video search.

2. Wikimedia Commons media search.

3. NASA Image and Video Library search.



Add Coverr only when an official supported API is verified from current

official documentation.



If no official Coverr API is available:



mark Coverr unavailable/manual-upload-only.



Do not scrape websites.



Do not add Internet Archive in C3.



==================================================

2. PROVIDER INTERFACE

==================================================



Create a provider-neutral interface such as:



stock_providers/

    base.py

    registry.py

    schemas.py

    pixabay.py

    pexels.py

    wikimedia.py

    nasa.py

    coverr.py            # only when officially supported

    licensing.py

    normalization.py

    health.py



Provider interface:



search(query, filters)

get_candidate(provider_asset_id)

get_preview(provider_asset_id)

download_selected(provider_asset_id, rendition)

get_license(provider_asset_id)

health()

quota_status()



Django views and Celery tasks must not contain provider-specific response

parsing.



==================================================

3. NORMALIZED CANDIDATE MODEL

==================================================



Normalize all provider results into one schema.



Each candidate must include:



- candidate ID

- provider

- provider asset ID

- TimelinePlan ID

- shot ID

- source query

- source query order

- title/description

- source page URL

- preview image URL

- preview video URL where allowed

- available download renditions

- width

- height

- duration

- orientation

- frame rate when available

- creator

- creator URL

- provider attribution

- license code

- license URL

- attribution required

- attribution text

- commercial use status

- derivative-use status

- editorial-only status

- AI-generated-content flag when known

- model/property/trademark warning

- fetched timestamp

- metadata hash

- selection status

- rejection reason

- user lock state



Do not call media “copyright free”.



Use:



licensed_for_intended_use

needs_rights_review

rejected



==================================================

4. LICENSE RULES

==================================================



Create provider-specific license adapters.



Pixabay and Pexels:

- preserve provider and creator metadata,

- preserve source-page links,

- follow current API attribution/linking requirements.



Wikimedia Commons:

- inspect every file's own licence,

- preserve author, licence code, licence URL, attribution, and ShareAlike,

- reject unknown or incompatible licences,

- do not assume every Commons item has the same licence.



NASA:

- preserve NASA metadata and source information,

- mark third-party material and usage warnings for review,

- do not automatically treat every search result as unrestricted.



Coverr:

- preserve source and licence metadata,

- flag trademarks, private property, and recognizable persons for review,

- never use Coverr assets for AI training or to build a competing stock library.



Generate later-exportable:



credits.txt

license-report.json

license-report.html



==================================================

5. SEARCH FILTERS

==================================================



Support provider-neutral filters:



- orientation: portrait preferred

- minimum width

- minimum height

- minimum duration

- maximum duration

- media type

- authentic/illustrative preference

- people allowed

- editorial allowed

- AI-generated allowed

- provider list

- safe-search level



For reels:



prefer portrait video.



Do not reject good landscape footage automatically.



Store a vertical-crop suitability warning.



==================================================

6. SEARCH ORCHESTRATION

==================================================



For each unlocked approved shot:



search every enabled compatible provider using the ordered

search_queries_en list.



Use the existing:



PostgreSQL

transactional outbox

Redis

Celery network workers



Search providers concurrently using Celery.



Celery messages contain IDs only.



Do not put media bytes into Redis.



Provider failure must not fail the complete search when other providers

succeed.



Return partial results with provider-specific errors.



==================================================

7. RATE LIMITS AND PROVIDER HEALTH

==================================================



Implement per-provider:



- request timeout

- bounded retries

- rate limiting

- cooldown/circuit breaker

- quota state

- last success

- last failure

- disabled/configuration state



Reuse existing C1 reliability patterns where suitable.



Do not repeatedly call an unavailable provider.



Expose safe provider diagnostics without exposing API keys.



==================================================

8. QUERY RULES

==================================================



Run the C2 English-query validator before every provider search.



Reject:



- Telugu Unicode queries,

- Roman Telugu markers,

- empty queries,

- duplicate queries,

- URLs,

- prompt-injection text,

- overlong queries,

- control characters.



A rejected query must not reach a provider.



Store validation failure against the shot for human correction.



==================================================

9. PREVIEW-FIRST DOWNLOAD POLICY

==================================================



Initially store only:



- metadata,

- thumbnails,

- provider preview URLs,

- small preview files when permitted.



Do not download every full-resolution candidate.



Download the final-quality file only after the user selects the candidate.



This must reduce:



- bandwidth,

- storage,

- provider traffic,

- duplicate files.



==================================================

10. BASIC DETERMINISTIC ORDERING

==================================================



C3 may use deterministic candidate ordering based on:



- provider query order,

- portrait orientation,

- useful resolution,

- duration suitability,

- licence completeness,

- preview availability,

- duplicate penalty,

- previously rejected candidate penalty.



Do NOT claim semantic visual matching.



Do NOT inspect video meaning with Gemini in C3.



Do NOT use CLIP/video embeddings in C3.



That belongs to C4.



==================================================

11. DEDUPLICATION

==================================================



Deduplicate candidates using:



- provider + provider asset ID,

- normalized source URL,

- metadata hash,

- downloaded content SHA-256 when available.



Do not download the same provider file repeatedly.



Do not delete existing user assets automatically.



Mark near-duplicate detection as a future C4 capability.



==================================================

12. DATABASE MODELS

==================================================



Create versioned durable models or equivalent for:



StockSearchRun

StockSearchProviderAttempt

StockCandidate

ShotCandidateAssignment

CandidateLicenseSnapshot



Tie every search to:



- project

- project revision

- TimelinePlan

- plan version

- shot ID

- query

- provider configuration version



A new plan version must not silently reuse incompatible candidates.



==================================================

13. HUMAN REVIEW UI

==================================================



Add a Django Template + jQuery candidate-review interface.



For each shot show:



- exact Telugu source span

- Roman Telugu caption

- English visual intent

- English query

- shot start/end/duration

- provider

- thumbnail/video preview

- orientation

- resolution

- duration

- creator

- licence badge

- attribution requirement

- rights warnings

- AI-generated flag

- source link



Allow the user to:



- preview candidate

- select candidate

- reject candidate

- lock selection

- change provider

- rerun one query

- edit English query

- upload own footage

- choose graphic/manual visual

- clear assignment



Never auto-select uncertain-rights candidates.



==================================================

14. SHOT ASSIGNMENT

==================================================



Selected candidates must attach to:



TimelinePlan version

+

shot ID



Do not modify the Telugu source script.



Do not modify Roman captions.



Do not change shot timing.



Preserve:



- locked shots,

- manually uploaded assets,

- existing user selections.



Downloading the selected final asset must create or reuse the normal Asset

record with provider, source, licence, creator, and hash metadata.



==================================================

15. CURRENT RENDERER COMPATIBILITY

==================================================



C3 does not need to make TimelinePlan shots drive FFmpeg yet.



Keep the existing scene renderer operational.



Provide an explicit compatibility action if useful:



Use selected candidate as legacy scene media



Do not automatically flatten all C2 shots into legacy scenes.



Full multi-shot timeline rendering belongs to a later phase.



==================================================

16. SECURITY

==================================================



Provider API keys remain server-side.



Do not expose keys in:



- HTML,

- jQuery,

- API responses,

- logs,

- database candidate metadata,

- error messages.



Validate all remote URLs.



Prevent SSRF.



Allow downloads only from verified provider domains returned by the provider

adapter.



Enforce:



- response-size limits,

- content-type checks,

- FFprobe validation,

- timeouts,

- safe generated filenames.



==================================================

17. TESTS

==================================================



Test:



- Pixabay adapter normalization

- Pexels adapter normalization

- Wikimedia per-file licence handling

- NASA search/metadata handling

- Coverr disabled when official API is unavailable

- provider timeout with partial success

- provider rate limit

- provider circuit breaker

- invalid English query rejection

- Roman Telugu query rejection

- duplicate candidate removal

- preview-first behavior

- selected-only final download

- licence metadata persistence

- attribution generation

- wrong-plan/shot assignment rejection

- locked shot preservation

- manual upload preservation

- duplicate Celery delivery idempotency

- SSRF prevention

- malicious remote URL rejection

- jQuery candidate grid

- zero JavaScript runtime errors



Mock provider responses for automated tests.



Do not consume provider quotas in test loops.



==================================================

18. ACCEPTANCE RUN

==================================================



Use the approved corrected C2 timeline.



For all 13 planned shots:



- search enabled providers,

- store normalized candidates,

- validate licences,

- show results in the UI.



Acceptance should demonstrate:



- valid English queries only,

- at least Pixabay and Pexels searched,

- Wikimedia/NASA searched when relevant,

- provider failures do not lose other results,

- candidates retain source/licence metadata,

- no duplicate downloads,

- manual asset/lock preservation,

- user can select one candidate for each shot.



Do not require the user to select weak or irrelevant footage.



Shots may remain:



needs_manual_footage

graphic_preferred

no_suitable_candidate



Create:



test-outputs/creative-c3/<timestamp>/

    search-summary.json

    provider-attempts.json

    candidates.csv

    license-report.json

    credits.txt

    acceptance-report.md



==================================================

19. REGRESSION

==================================================



Run:



- complete controlled Django test suite,

- C1 gateway tests,

- C2 planner tests,

- C3 provider tests,

- Celery/outbox tests,

- media/upload tests,

- voice tests,

- existing render tests,

- jQuery smoke.



Existing video workflows must remain functional.



==================================================

20. MIGRATION AND CLOSEOUT

==================================================



Use standard backup/restore/migration safety from UPGRADE_RULES.md.



Apply the C3 migration live only after:



- disposable migration tests pass,

- full regression passes,

- jQuery smoke passes,

- acceptance report is reviewable.



Update:



docs/UPGRADE_STATUS.md

docs/CREATIVE_C3_STOCK_GATEWAY.md



Commit:



creative-engine: add multi-provider stock gateway



Tag:



creative-c3-complete



STOP AFTER C3.



Do not begin C4.

Do not implement semantic video ranking.

Do not implement generative video.

Do not change the renderer automatically.