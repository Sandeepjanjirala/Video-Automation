CREATIVE ENGINE — PHASE C5

PROFESSIONAL TIMELINE RENDERER + SHORT-FORM EDITING ENGINE



Read current C1–C4 documentation and repository truth.



Require:



creative-c4-complete



Implement C5 ONLY.



Do not redesign the Django/Celery architecture.

Do not change the approved user script.

Do not regenerate C2 timing.

Do not redo C4 candidate ranking.

Do not begin analytics/learning work.



==================================================

GOAL

==================================================



Replace the old:



scene

→ one video

→ narration

→ render



behavior for C2-enabled projects with:



Approved script

    ↓

C2 dynamic shot timeline

    ↓

C4 selected video + best segment

    ↓

professional edit decisions

    ↓

9:16 reframing

    ↓

captions

    ↓

music + sound design

    ↓

color/visual finishing

    ↓

FFmpeg

    ↓

professional final reel



One narration sentence may contain multiple visual shots.



C2 shot start/end times are the timing authority.



Do NOT return to fixed 4–5 second clips.



==================================================

1. CREATE A RENDER MANIFEST

==================================================



Create a versioned RenderManifest between TimelinePlan and FFmpeg.



For every shot store:



shot_id

timeline_start

timeline_end

duration

selected_asset_id

source_start

source_end

source_speed

crop

scale

camera_motion

transition_in

transition_out

caption relationship

color treatment

audio/SFX instructions

render status



Also include global tracks:



narration

captions

background music

sound effects

graphics

master audio

color profile



The renderer should consume RenderManifest.



Do not make FFmpeg read creative business logic directly from raw project JSON.



==================================================

2. USE C4 BEST SEGMENTS

==================================================



For each shot use:



C4 selected candidate

+

best_segment_start

+

best_segment_end



Fit that segment to the exact C2 shot duration.



Preferred order:



1. natural source duration fits

2. extend using nearby meaningful frames/action

3. trim at action-safe point

4. small speed adjustment where visually safe

5. alternate candidate

6. freeze/loop only as a last resort and clearly flagged



Do not blindly stretch every source clip.



Avoid obvious slow-motion or speed-up artifacts.



==================================================

3. TIMELINE CONTINUITY

==================================================



The final visual track must continuously cover narration time.



Require:



- no unexplained black frames

- no accidental gaps

- no accidental overlaps

- no missing shot

- no shot outside timeline

- no cut inside protected narration phrase

- exact frame-aligned boundaries



Use the project FPS as the frame-time authority.



Default short-form output:



1080x1920

30 fps

H.264

AAC



Keep export profiles configurable.



==================================================

4. PROFESSIONAL CUT ENGINE

==================================================



Default transition should be:



clean hard cut



Use transitions only when they improve meaning.



Available transition grammar may include:



hard cut

match cut

J-cut

L-cut

short dissolve

dip-to-black

whip transition

motion-matched transition

graphic transition



Do NOT randomly apply transitions.



Do NOT use transition effects between every shot.



Transition choice must consider:



story beat

motion direction

camera movement

emotion

style profile

previous and next shot



For normal Telugu reels:



mostly clean cuts.



==================================================

5. EDITING RHYTHM

==================================================



Respect C2 dynamic durations.



For normal Telugu reels, the approximate average may remain around 2.5–3

seconds when the content supports it.



But never alter timing merely to hit that number.



Editing should react to:



narration idea

visual action

reveal

emotion

pause

motion

music

viewer attention



Do NOT:



change clip every 3 seconds mechanically

cut on every music beat

add random zooms to make the edit look active



==================================================

6. STYLE-BASED EDITING GRAMMAR

==================================================



Map C2 style profiles into rendering behavior.



NORMAL TELUGU:

balanced cuts

clean captions

moderate visual movement

restrained transitions



GEN Z / FAST FACTS:

faster cuts

selective punch-ins

strong keyword emphasis

higher visual novelty

restrained meme-like energy where context allows



MYSTERY:

controlled pacing

slow push-ins

dark/atmospheric transitions

intentional pauses

reveal emphasis



TECH / EDUCATIONAL:

clear visuals

diagrams/screens where useful

clean cuts

information-first captions



DOCUMENTARY:

authentic footage priority

minimal effects

location/date labels

natural pacing



LUXURY / CINEMATIC:

longer confident shots

smooth movement

minimal typography

subtle transitions

controlled color treatment



Style must affect the entire edit coherently.



==================================================

7. 9:16 INTELLIGENT REFRAMING

==================================================



Use C4 crop recommendations.



Implement temporal 9:16 reframing.



For landscape clips:



track the important subject over time where possible.



Preserve:



faces

eyes

hands when action-relevant

important objects

text

main subject

direction of movement



Avoid:



subject jumping across the crop

rapid crop movement

cutting faces

cropping important action



Smooth crop movement over time.



Use OpenCV / MediaPipe / existing local vision tools where useful.



If automatic crop confidence is low:



use a safe static crop

or mark for human review.



==================================================

8. CAMERA MOTION / PUNCH-IN ENGINE

==================================================



Allow subtle synthetic camera treatment where appropriate:



slow push-in

slow pull-out

subtle pan

controlled crop tracking

small emphasis punch-in



Do not add motion to every shot.



Do not use aggressive zoom effects on already moving footage.



Motion must be tied to:



emphasis

reveal

subject movement

style profile



Keep transformations deterministic and versioned.



==================================================

9. CAPTION ENGINE UPGRADE

==================================================



Use actual narration timing.



Roman Telugu remains display text.



Support phrase-level caption groups rather than giant sentences.



Provide style-aware captions:



NORMAL:

clean readable phrase captions



GEN Z:

selective keyword emphasis



MYSTERY:

controlled reveal



DOCUMENTARY:

clean subtitles + factual lower thirds



LUXURY:

minimal typography



Requirements:



safe margins

maximum readable line length

no UI obstruction

no face obstruction where known

no excessive animation

consistent typography

frame-accurate timing



Do not highlight every word unless the selected caption style explicitly

requires word tracking.



Use ASS/libass or an equivalent deterministic renderer.



==================================================

10. CAPTION COLLISION AVOIDANCE

==================================================



Use available subject/face metadata to avoid covering important visual areas.



Support:



bottom

lower-middle

center

top



placement when necessary.



Keep placement stable unless there is a genuine collision.



Do not move captions around randomly between shots.



==================================================

11. BACKGROUND MUSIC ENGINE

==================================================



Treat BGM as a timeline track.



Analyse:



duration

energy

beats

sections

intro

build

drop

outro



Use beat information only as an editing signal.



Do NOT force visual cuts on every beat.



Music should follow the story:



hook

build

middle

reveal

ending



Support:



music trim

loop with clean boundary

fade

crossfade

energy section selection



Preserve the existing user-selected BGM workflow.



==================================================

12. PROFESSIONAL VOICE + MUSIC MIX

==================================================



Narration remains highest priority.



Implement/master:



voice gain

music ducking

sidechain compression

EQ where justified

compression

limiting

fade handling

final loudness normalization



Avoid audible pumping.



Do not simply keep music at one low constant volume.



Music can rise during narration pauses and fall naturally during speech.



Prevent clipping.



==================================================

13. SOUND EFFECT TRACK

==================================================



Add an optional SFX track.



Support categories such as:



subtle whoosh

impact

riser

click

transition sweep

ambient layer

reveal hit



Do NOT automatically put an SFX on every cut.



Use SFX only for important:



reveals

graphics

camera transitions

UI/technology moments

dramatic emphasis



Allow users to disable SFX completely.



C5 should work perfectly without SFX.



==================================================

14. VISUAL QUALITY NORMALIZATION

==================================================



Before creative grading, normalize obvious differences between clips.



Handle where safe:



exposure differences

contrast

white balance

saturation

black level



Do not aggressively alter source footage.



Avoid skin-tone damage.



Do not apply the same LUT blindly to every source.



Then apply a restrained style look where selected.



==================================================

15. SHOT-TO-SHOT VISUAL CONSISTENCY

==================================================



Prevent the reel from looking like unrelated stock footage stitched together.



Analyse adjacent clips for:



brightness

temperature

saturation

contrast

motion

dominant composition



Apply restrained matching where needed.



Do not destroy intentional visual differences.



==================================================

16. DUPLICATE AND REPETITION GUARD

==================================================



Before rendering, check the complete timeline.



Warn or fail review when:



same source segment is reused unintentionally

near-identical clips occur close together

too many consecutive shots use the same composition

same provider clip is repeated excessively



Locked/manual user choices override warnings.



==================================================

17. GRAPHICS PLACEHOLDER TRACK

==================================================



Support timeline graphics entries for:



title cards

location labels

numbers

facts

quotes

simple callouts

simple progress/timeline graphics



Implement clean FFmpeg/ASS-based graphics for C5.



Do not introduce a large new motion-graphics platform unless actually needed.



Advanced motion graphics can be a later phase.



==================================================

18. RENDER PIPELINE

==================================================



Recommended render flow:



validate manifest

    ↓

prepare selected source segments

    ↓

reframe/crop

    ↓

speed/timing adjustment

    ↓

shot-level normalization

    ↓

shot render

    ↓

timeline assembly

    ↓

transitions

    ↓

captions/graphics

    ↓

narration

    ↓

BGM

    ↓

SFX

    ↓

ducking/mastering

    ↓

final encode

    ↓

FFprobe validation

    ↓

full FFmpeg decode validation



Use temporary files safely and publish final artifacts atomically.



Keep existing Celery render worker architecture.



==================================================

19. RENDER CACHE

==================================================



Cache reusable results by render contract.



Shot cache key should include:



source asset hash

source segment

target duration

crop

speed

resolution

FPS

color treatment

graphics version

renderer version



If only captions change:



do not unnecessarily recompute expensive unrelated analysis.



If one shot changes:



rerender only that shot and dependent assembly stages where possible.



==================================================

20. LEGACY COMPATIBILITY

==================================================



Existing non-C2 projects must still render.



Use:



legacy scene renderer



for old projects.



Use:



TimelinePlan → RenderManifest → C5 renderer



for approved C2/C4 projects.



Do not silently migrate old projects.



==================================================

21. HUMAN PREVIEW

==================================================



Add a low-cost draft preview mode.



Example:



540x960

lower bitrate

fast encode preset



Allow the user to review:



shot order

cuts

crop

captions

music

SFX

timing



before full 1080x1920 final render.



Allow shot-level fixes without rebuilding the entire plan manually.



==================================================

22. QUALITY GATES

==================================================



Before marking final render successful validate:



resolution

FPS

video codec

audio codec

duration

audio presence

no corrupt frames

no black-frame stretches

timeline coverage

caption bounds

audio clipping

missing shots

missing selected media



Store a machine-readable quality report.



==================================================

23. C5 ACCEPTANCE

==================================================



Use a real approved C2+C4 project.



Render the COMPLETE reel through C5.



Do not use the old one-scene/one-video render path for the acceptance reel.



Acceptance must prove:



- multiple shots inside narration sentences

- real C2 dynamic durations

- C4-selected best segments

- no fixed-duration cutting

- intelligent 9:16 crops

- clean professional cuts

- correct Roman Telugu captions

- Telugu narration

- natural music ducking

- optional restrained SFX

- visual consistency

- no black gaps

- no corrupt frames



Create:



test-outputs/creative-c5/<timestamp>/



final_reel.mp4

draft_reel.mp4

render-manifest.json

timeline-report.json

shot-report.csv

crop-report.json

audio-report.json

quality-report.json

acceptance-report.md



==================================================

24. HUMAN PLAYBACK REVIEW

==================================================



Actually watch the final reel.



Review:



Does every visual match the narration?



Do cuts feel natural?



Does any shot stay too long?



Does any shot change unnecessarily?



Are crop/framing choices professional?



Are captions readable?



Does voice sound natural?



Is music helping instead of distracting?



Are transitions restrained?



Does the entire reel feel like one professionally edited video instead of

random stock clips?



Record the review honestly.



==================================================

25. REGRESSION

==================================================



Run the full controlled suite.



Verify:



C1 gateway

C2 planner

C3 providers

C4 ranking

C5 renderer

Celery/outbox

voice

media

jQuery UI

legacy rendering



No regressions.



==================================================

26. CLOSEOUT

==================================================



Create:



docs/CREATIVE_C5_PROFESSIONAL_RENDERER.md



Update:



docs/UPGRADE_STATUS.md



After real-video acceptance and regression pass:



commit:



creative-engine: add professional timeline renderer



tag:



creative-c5-complete



STOP AFTER C5.



Do not automatically start the next creative phase.