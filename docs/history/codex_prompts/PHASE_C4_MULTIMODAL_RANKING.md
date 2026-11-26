CREATIVE ENGINE — PHASE C4

MULTIMODAL VIDEO UNDERSTANDING + SEMANTIC RANKING + BEST CLIP SELECTION



Read current C1/C2/C3 docs and status.



Require:



creative-c3-complete



Implement C4 ONLY.



Do not redesign completed architecture.

Do not change the user's script.

Do not change approved C2 shot timing.

Do not implement generative video yet.

Do not start C5.



==================================================

GOAL

==================================================



For every approved C2 shot:



C2 visual intent

        +

C3 multi-provider candidates

        ↓

actual video-content analysis

        ↓

semantic ranking

        ↓

best matching candidate

        ↓

best usable segment inside that candidate

        ↓

crop/framing suitability

        ↓

confidence score

        ↓

human review / optional high-confidence selection



Do NOT rank mainly from:



provider title

tags

filename

query position



Actually understand the video frames.



==================================================

1. LOCAL MULTIMODAL EMBEDDING ENGINE

==================================================



Add a provider-neutral local visual embedding layer.



Preferred architecture:



PyTorch

+

OpenCLIP / SigLIP-class vision-language model

+

cached embeddings



Evaluate available compatible open-source models and select one suitable for

the current machine.



The model must support:



text embedding:

    English C2 visual intent/query



image embedding:

    sampled video frames



Do not make Gemini the primary ranking engine.



Primary ranking should work locally without API quota.



Cache embeddings by:



model version

asset hash

frame timestamp

analysis version



Never recompute unchanged footage unnecessarily.



==================================================

2. VIDEO SAMPLING

==================================================



Do not analyse every frame.



For each candidate video:



- inspect duration/resolution/FPS with FFprobe

- extract representative frames

- detect useful scene changes where practical

- sample more densely around visual changes

- avoid redundant nearly-identical frames



For short stock clips, approximately 6–20 useful frames is normally enough,

depending on duration and scene variation.



Generate a lightweight contact sheet for debugging/review.



Store timestamps for every sampled frame.



==================================================

3. SEMANTIC MATCHING

==================================================



Compare each candidate against:



visual_intent_en

search_queries_en

subject

action

place

emotion

visual_role

authenticity requirement

camera/framing preference



Produce separate scores such as:



subject_match

action_match

location_match

context_match

emotion_match

visual_intent_match



Do not rely on one cosine similarity number alone.



==================================================

4. TEMPORAL / BEST-SEGMENT ANALYSIS

==================================================



A candidate may contain only 2 useful seconds inside a 15-second video.



Find the strongest temporal region.



Example:



candidate = 18 seconds



0–5 sec   weak match

5–8 sec   excellent match

8–18 sec  unrelated



Return:



best_segment_start

best_segment_end

best_segment_score



The selected segment must satisfy the C2 planned-shot duration when possible.



Prefer preserving meaningful actions rather than cutting them halfway.



==================================================

5. PROFESSIONAL QUALITY SCORES

==================================================



Score candidates for more than semantic relevance.



Include:



- semantic relevance

- shot-duration suitability

- motion/activity

- sharpness

- exposure

- stability

- useful composition

- crop suitability for 9:16

- resolution

- frame-rate suitability

- visual cleanliness

- duplicate/repetition penalty

- continuity with previous/next shots

- authenticity/licensing confidence



Detect obvious poor footage such as:



- black frames

- extreme blur

- frozen/static video

- very low resolution

- corrupted frames

- excessive borders

- unusable crop

- duplicate footage



Do not reject deliberate cinematic static shots merely because motion is low.



==================================================

6. VERTICAL REEL SUITABILITY

==================================================



Evaluate whether landscape footage can become a good 9:16 shot.



Estimate:



- subject location

- crop safety

- center/subject retention

- headroom

- important-object preservation



Store:



vertical_crop_score

recommended_crop

crop_warning



Do not permanently crop the source in C4.



Only recommend crop metadata.



==================================================

7. DYNAMIC RANKING SCORE

==================================================



Create an explainable ranking model.



Conceptually:



final_score =

    semantic_match

  + subject_match

  + action_match

  + context_match

  + shot_duration_fit

  + vertical_crop_fit

  + technical_quality

  + motion_quality

  + continuity

  + license_confidence

  - duplicate_penalty

  - repetition_penalty

  - quality_penalty

  - authenticity_penalty



Weights should depend on C2 style profile.



Example:



Gen Z:

    semantic + motion + novelty higher



Documentary:

    authenticity + subject correctness higher



Luxury:

    aesthetics + composition + slower-motion suitability higher



Mystery:

    mood + composition + continuity higher



Normal Telugu:

    balanced profile



Version all ranking policies.



==================================================

8. CONTINUITY AWARENESS

==================================================



Do not independently choose every shot.



Consider neighboring shots.



Penalize sequences such as:



three almost-identical drone clips

same person repeatedly

same camera angle repeatedly

same provider clip reused repeatedly

constant visual intensity

unintentional jump-cut appearance



Reward useful variation:



wide → medium → close

location → action → detail

establishing → subject → supporting detail



But do not introduce random variety that weakens script relevance.



==================================================

9. DUPLICATE / NEAR-DUPLICATE DETECTION

==================================================



Add visual duplicate detection using suitable techniques such as:



perceptual hashing

embedding similarity

frame similarity



Detect:



same footage from different providers

different resolutions of same clip

near-identical stock clips



Group duplicates.



Prefer one canonical candidate.



==================================================

10. OPTIONAL GEMINI MULTIMODAL JUDGE

==================================================



Use Gemini only as a bounded SECOND-STAGE judge when helpful.



Do NOT send every candidate video to Gemini.



Workflow:



local ranking

    ↓

top 3–5 ambiguous candidates

    ↓

representative frames/contact sheet

    ↓

C1 AI gateway

    ↓

structured comparison



Gemini may answer:



- which candidate best supports the narration visually

- whether footage is misleading

- action correctness

- contextual mismatch

- tie-breaking



Use the C1 fallback/model gateway.



Budget this strictly.



If Gemini quota is unavailable:



local ranking continues normally.



==================================================

11. AUTHENTICITY / FACTUAL SAFETY

==================================================



For factual shots distinguish:



literal_required

illustrative_allowed

graphic_preferred



Examples:



Narration:

"Octopus skin contains chromatophores"



Prefer:

actual octopus skin / chromatophore footage



Do not confidently select:

random colorful fish footage



Narration:

"AI is changing our lives"



Illustrative technology footage is acceptable.



Store an authenticity score and reason.



Low-confidence factual matches must require human review.



==================================================

12. AUTOMATIC DECISION STATES

==================================================



For each shot return:



recommended

alternative

needs_review

no_good_match

graphic_preferred

manual_footage_required



Do not force bad stock footage just because results exist.



Confidence levels:



high

medium

low



Allow optional automatic selection ONLY when:



- confidence is high

- licensing is safe

- authenticity requirements pass

- user has enabled auto-select



Default should remain reviewable.



==================================================

13. HUMAN REVIEW UI

==================================================



Enhance the C3 candidate UI.



For every candidate show:



preview

provider

semantic score

quality score

vertical score

continuity score

license status

best segment

final score

confidence

ranking explanation



Show:



Why this clip?

Why not the others?



Allow user to:



select another candidate

change best segment

lock candidate

reject candidate

mark footage wrong

change crop

rerun ranking

request more search results



User feedback should be stored for future ranking improvements.



==================================================

14. LEARNING FROM USER CHOICES

==================================================



Do NOT train a deep model immediately.



First collect structured feedback:



shown candidate

ranking

selected/rejected

manual override

reason

style profile

shot metadata



Build a dataset for future learning-to-rank.



Later phases can train a personalized ranking model.



For now use deterministic/versioned ranking weights.



==================================================

15. CELERY PIPELINE

==================================================



Use existing outbox/Celery architecture.



Recommended stages:



candidate_metadata_ready

frames_extracted

embeddings_generated

semantic_scores_generated

quality_scores_generated

segments_ranked

continuity_scored

ranking_complete

review_ready



Heavy visual analysis belongs on an appropriate worker queue.



Do not put frames/video bytes in Redis.



Pass IDs/storage references only.



==================================================

16. PERFORMANCE

==================================================



Avoid analysing the same media repeatedly.



Cache:



FFprobe metadata

sampled-frame hashes

visual embeddings

quality metrics

segment scores



If 20 shots use the same candidate asset:



analyse the asset once.



Reuse embeddings.



Keep model loaded within worker lifetime where practical.



==================================================

17. DATABASE

==================================================



Persist versioned analysis such as:



MediaAnalysis

MediaFrameEmbedding

CandidateSemanticScore

CandidateSegment

CandidateRanking

ShotRankingDecision

RankingFeedback



Tie analysis to:



asset hash

embedding model/version

ranking-policy version

TimelinePlan version

shot ID



Old ranking results must remain reproducible.



==================================================

18. ACCEPTANCE TEST

==================================================



Use the approved C2 timeline and real C3 candidate pool.



For the reel's planned shots:



1. Analyse actual candidate videos.

2. Generate local embeddings.

3. Rank candidates.

4. Identify best temporal segments.

5. Evaluate vertical suitability.

6. Apply continuity-aware reranking.

7. Display recommendations in UI.



Manually inspect the recommendations.



Verify that selected footage genuinely matches:



- subject

- action

- context

- narration meaning



Compare C4 ranking against simple C3 provider/query ordering.



The C4 result should clearly improve visual relevance.



Create:



test-outputs/creative-c4/<timestamp>/

    ranking-summary.json

    shot-rankings.csv

    segment-rankings.csv

    quality-report.json

    continuity-report.json

    feedback.json

    acceptance-report.md



==================================================

19. TESTS

==================================================



Cover:



- embedding reproducibility

- semantic ranking

- irrelevant candidate rejection

- best-segment detection

- vertical suitability

- blur/black/corrupt rejection

- duplicate detection

- continuity reranking

- factual/authenticity handling

- style-profile weighting

- cache reuse

- Gemini unavailable fallback

- user lock preservation

- human override persistence

- Celery idempotency

- existing C1/C2/C3 regressions

- jQuery UI



Run the full controlled regression before closeout.



==================================================

20. CLOSEOUT

==================================================



Document:



docs/CREATIVE_C4_MULTIMODAL_RANKING.md



Update:



docs/UPGRADE_STATUS.md



After acceptance and regression pass:



commit:



creative-engine: add multimodal video ranking



tag:



creative-c4-complete



STOP AFTER C4.



Do not begin C5.

Note: any requirements or doubts ask me