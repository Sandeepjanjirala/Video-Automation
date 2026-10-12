import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
import shutil
from studio.models import Project, Scene
from studio import store
from studio.config import DATA

clip1_id = '77ee26ea90654ae3985b1f1cc6ee49fd'
clip2_id = 'a257a20e79534de3a903286f708b5f41'
bgm_id = 'a8bf9d890428481096fd7befafdca724'

scenes = [
    Scene(
        id='scene_home_1',
        narration='నమస్కారం! తెలుగు రీల్ స్టూడియో మీ హోమ్ లాప్‌టాప్‌లో విజయవంతంగా రన్ అవుతోంది. అన్ని కాన్ఫిగరేషన్లు సిద్ధంగా ఉన్నాయి.',
        roman='Namaskaram! Telugu Reel Studio mee home laptop lo vijayavanthamga run avuthondi. Anni configurations siddhamga unnayi.',
        visual='upload',
        video_asset=clip1_id,
        fit='crop',
        hold_seconds=0.5,
        rights_reviewed=True
    ),
    Scene(
        id='scene_home_2',
        narration='పోస్ట్‌గ్రేస్‌క్యూఎల్ మరియు రెడ్డిస్ ఆర్కిటెక్చర్ ఆధారంగా సెలరీ వర్కర్లు బ్యాక్‌గ్రౌండ్‌లో వీడియోలను రెండర్ చేస్తున్నాయి.',
        roman='PostgreSQL mariyu Redis architecture aadharamga Celery workerlu background lo videolanu render chesthunnayi.',
        visual='upload',
        video_asset=clip2_id,
        fit='crop',
        hold_seconds=0.5,
        rights_reviewed=True
    )
]

p = Project(
    title='Home Laptop Phase 3 Reel Verification',
    evidence_notes='Phase 3 end-to-end integration and render verification on home laptop.',
    scenes=scenes,
    music_asset=bgm_id,
    music_rights_reviewed=True
).model_dump()

print('[1/4] Saving project to PostgreSQL...')
row = store.save_project(p)
pid = row['id']
rev = row['revision']
print(f'Project saved with ID: {pid}, Revision: {rev}')

print('[2/4] Approving revision...')
store.approve(pid, rev)

print('[3/4] Enqueuing render job...')
job_info = store.enqueue(pid, rev, 'render', {'tts_provider': 'edge', 'quality': 'final'})
jid = job_info['id']
print(f'Job enqueued: {jid}')

print('[4/4] Monitoring job execution via Celery worker and PostgreSQL state...')
start_time = time.time()
last_progress = ''
while time.time() - start_time < 120:
    j = store.job(jid)
    st = j.get('state')
    pr = j.get('progress') or ''
    if pr != last_progress:
        print(f'[{time.time()-start_time:.1f}s] State: {st} | Progress: {pr}')
        last_progress = pr
    if st == 'succeeded':
        print(f'Job SUCCEEDED in {time.time()-start_time:.2f}s!')
        break
    elif st == 'failed':
        print(f'Job FAILED: {j.get("error_message")}')
        break
    time.sleep(1)

out_file = DATA / 'exports' / jid / 'reel.mp4'
print(f'Output export path: {out_file}')
print(f'Exists: {out_file.exists()}')
if out_file.exists():
    dest_dir = Path('test-outputs/phase-3-home')
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out_file, dest_dir / 'phase3_home_reel.mp4')
    if (out_file.parent / 'captions.srt').exists():
        shutil.copy2(out_file.parent / 'captions.srt', dest_dir / 'captions.srt')
    if (out_file.parent / 'source-report.json').exists():
        shutil.copy2(out_file.parent / 'source-report.json', dest_dir / 'source-report.json')
    if (out_file.parent / 'project.json').exists():
        shutil.copy2(out_file.parent / 'project.json', dest_dir / 'project.json')
    print(f'Copied artifacts to {dest_dir} successfully!')
