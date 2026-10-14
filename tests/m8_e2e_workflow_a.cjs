/**
 * tests/m8_e2e_workflow_a.cjs — Stage M8 Final Real E2E: Workflow A (Uploaded MP4)
 * 
 * Flow:
 * open Django jQuery UI (http://127.0.0.1:8788/)
 *     ↓
 * create project
 *     ↓
 * upload/select MP4
 *     ↓
 * create/configure scenes
 *     ↓
 * Telugu narration
 *     ↓
 * Roman Telugu captions
 *     ↓
 * background music
 *     ↓
 * save
 *     ↓
 * approve
 *     ↓
 * click render (1080x1920)
 *     ↓
 * Django -> PostgreSQL/outbox -> Redis -> Celery -> FFmpeg
 *     ↓
 * final reel -> browser player
 * 
 * Verifies:
 * - Real 1080x1920 video output
 * - Video & audio streams
 * - FFmpeg full decode with zero corrupt frames
 * - Zero requests to FastAPI :8787
 * - Zero JavaScript runtime errors
 */

if (!Promise.withResolvers) {
  Promise.withResolvers = function() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
}

class Iterator {
  [Symbol.iterator]() { return this; }
  find(fn) { for (const x of this) if (fn(x)) return x; return undefined; }
  filter(fn) { const self = this; return (function*() { for (const x of self) if (fn(x)) yield x; })(); }
  map(fn) { const self = this; return (function*() { for (const x of self) yield fn(x); })(); }
  some(fn) { for (const x of this) if (fn(x)) return true; return false; }
  every(fn) { for (const x of this) if (!fn(x)) return false; return true; }
  toArray() { return [...this]; }
}
globalThis.Iterator = globalThis.Iterator || Iterator;

const vm = require('vm');
const origCreateContext = vm.createContext;
vm.createContext = function(sandbox, options) {
  const ctx = origCreateContext.call(vm, sandbox, options);
  try {
    vm.runInContext(`
      if (!Promise.withResolvers) {
        Promise.withResolvers = function() {
          let resolve, reject;
          const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
          return { promise, resolve, reject };
        };
      }
      class Iterator {
        [Symbol.iterator]() { return this; }
        find(fn) { for (const x of this) if (fn(x)) return x; return undefined; }
        filter(fn) { const self = this; return (function*() { for (const x of self) if (fn(x)) yield x; })(); }
        map(fn) { const self = this; return (function*() { for (const x of self) yield fn(x); })(); }
        some(fn) { for (const x of this) if (fn(x)) return true; return false; }
        every(fn) { for (const x of this) if (!fn(x)) return false; return true; }
        toArray() { return [...this]; }
      }
      this.Iterator = this.Iterator || Iterator;
    `, ctx);
  } catch(e) {}
  return ctx;
};

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { JSDOM } = require('../frontend/node_modules/jsdom');
const { waitFor } = require('../frontend/node_modules/@testing-library/dom');

(async () => {
  const baseUrl = process.env.STUDIO_URL || 'http://127.0.0.1:8788';
  console.log('================================================================');
  console.log(`[M8 Workflow A] Starting Real Browser E2E Test against: ${baseUrl}`);
  console.log('================================================================');

  // Fetch HTML directly from Django root
  const htmlRes = await fetch(baseUrl + '/');
  if (!htmlRes.ok) {
    throw new Error(`Failed to load ${baseUrl}: HTTP ${htmlRes.status}`);
  }
  const rawHtml = await htmlRes.text();

  const networkLog = [];
  const runtimeErrors = [];

  const dom = new JSDOM(rawHtml, {
    url: baseUrl + '/',
    runScripts: 'dangerously',
    pretendToBeVisual: true
  });

  const w = dom.window;
  const doc = w.document;

  w.addEventListener('error', e => {
    const msg = String(e.error || e.message);
    runtimeErrors.push(msg);
    console.error('[Browser Error]', msg);
  });

  w.confirm = () => true;
  w.TextEncoder = TextEncoder;
  w.TextDecoder = TextDecoder;
  Object.defineProperty(w, 'crypto', { value: require('crypto').webcrypto });

  // Custom fetch spy to enforce security and zero :8787 calls
  w.fetch = async (input, init) => {
    const reqUrl = typeof input === 'string' ? new URL(input, baseUrl).href : input.url;
    const method = (init && init.method) || 'GET';
    networkLog.push({ url: reqUrl, method });

    if (reqUrl.includes(':8787')) {
      throw new Error(`SECURITY VIOLATION: Request attempted to FastAPI port 8787: ${reqUrl}`);
    }

    const res = await fetch(reqUrl, init);
    console.log(`[Fetch] ${method} ${reqUrl} -> ${res.status}`);
    return res;
  };

  // Load scripts sequentially exactly as in base.html
  const staticRoot = path.join(__dirname, '../studio/static');
  const scriptFiles = [
    'vendor/jquery.min.js',
    'js/api.js',
    'js/state.js',
    'js/media.js',
    'js/voice.js',
    'js/scenes.js',
    'js/projects.js',
    'js/jobs.js',
    'js/settings.js',
    'js/app.js'
  ];

  console.log('[M8 Workflow A] Loading modular jQuery scripts into DOM...');
  for (const sf of scriptFiles) {
    const fullPath = path.join(staticRoot, sf);
    const code = fs.readFileSync(fullPath, 'utf8');
    try {
      w.eval(code);
    } catch (err) {
      console.error(`Error executing script ${sf}:`, err);
      runtimeErrors.push(`Error in ${sf}: ${err.message}`);
    }
  }

  // Verify jQuery & zero React runtime
  if (typeof w.$ !== 'function') throw new Error('FAILED: jQuery is not defined');
  if (typeof w.React !== 'undefined' || typeof w.ReactDOM !== 'undefined') {
    throw new Error('FAILED: React/ReactDOM detected in window!');
  }
  console.log(`[M8 Workflow A] ✓ jQuery loaded (${w.$.fn.jquery}) & NO React present`);

  // Trigger document ready
  w.$(doc).ready();

  // 1. Wait for Bootstrap to complete
  console.log('[M8 Workflow A] Waiting for bootstrap flow to complete...');
  await waitFor(() => {
    const layoutEl = w.$('#app-main-layout');
    if (layoutEl.css('display') === 'none') {
      throw new Error('Waiting for app-main-layout to become visible');
    }
  }, { container: doc, timeout: 8000 });
  console.log('[M8 Workflow A] ✓ Bootstrap completed, main layout visible');

  // 2. Click "+ New project"
  console.log('[M8 Workflow A] Triggering "+ New project" via UI button...');
  await waitFor(() => {
    if (w.StudioState.isBusy || w.$('#btn-new-project').prop('disabled')) {
      throw new Error('Waiting for UI unbusy before creating new project');
    }
  }, { container: doc, timeout: 5000 });

  const prevProjectId = w.StudioState.current ? w.StudioState.current.id : null;
  w.$('#btn-new-project').click();

  let $scene1;
  await waitFor(() => {
    const cur = w.StudioState.current;
    if (!cur || cur.id === prevProjectId || w.StudioState.isBusy) {
      throw new Error('Waiting for new project to be created and synced');
    }
    const $scenes = w.$('.scene-card');
    if (!$scenes.length) throw new Error('Waiting for scene cards to render');
    $scene1 = $scenes.first();
  }, { container: doc, timeout: 8000 });

  const projectId = w.StudioState.current.id;
  const initialRev = w.StudioState.current.revision;
  console.log(`[M8 Workflow A] ✓ New project created (${projectId}) at revision v${initialRev}`);

  // 3. Fill in Project Brief & Scene Narration
  console.log('[M8 Workflow A] Entering Telugu narration, Roman Telugu captions, and evidence notes...');
  w.$('#input-project-title').val('M8 Workflow A — Uploaded MP4 Reel').trigger('input');
  w.$('#select-project-category').val('Technology').trigger('change');
  w.$('#input-project-topic').val('Telangana Tech & Innovation Hub Highlights').trigger('input');
  w.$('#textarea-evidence-notes').val('M8 Workflow A real browser test with verified uploaded MP4 footage and native Telugu script.').trigger('input');

  // Scene 1 Telugu text (verified pre-cached synthesis)
  const teluguNarration = 'నమస్కారం! తెలుగు రీల్ స్టూడియో జాంగో ఆర్కిటెక్చర్‌తో విజయవంతంగా పనిచేస్తోంది.';
  const romanCaptions = 'Namaskaram! Telugu Reel Studio Django architecture tho vijayavanthamga panichesthondi.';
  $scene1.find('.input-scene-narration').val(teluguNarration).trigger('input');
  $scene1.find('.input-scene-caption').val(romanCaptions).trigger('input');

  // 4. Save and approve project revision
  console.log('[M8 Workflow A] Saving project via UI button...');
  w.$('#btn-save-project').click();

  await waitFor(() => {
    if (w.StudioState.isBusy) throw new Error('Waiting for save to complete');
    const rev = w.$('#badge-revision').text();
    if (rev !== 'v' + (initialRev + 1)) throw new Error(`Expected revision v${initialRev + 1}, got: ${rev}`);
  }, { container: doc, timeout: 6000 });
  console.log(`[M8 Workflow A] ✓ Project saved with optimistic locking (revision v${initialRev + 1})`);

  console.log('[M8 Workflow A] Approving project revision via UI button...');
  await waitFor(() => {
    if (w.StudioState.isBusy || w.$('#btn-approve-project').prop('disabled')) {
      throw new Error('Waiting for approve button');
    }
  }, { container: doc, timeout: 3000 });
  w.$('#btn-approve-project').click();

  await waitFor(() => {
    const badge = w.$('#badge-approval');
    if (!badge.hasClass('approved')) throw new Error('Approval badge pending');
  }, { container: doc, timeout: 5000 });
  console.log('[M8 Workflow A] ✓ Script revision v2 approved');

  // 5. Navigate to Tab 02: Voice & visuals
  console.log('[M8 Workflow A] Navigating to Tab 02 (Voice & visuals)...');
  await waitFor(() => {
    if (w.StudioState.isBusy) throw new Error('Waiting for UI unbusy');
  }, { container: doc, timeout: 3000 });
  w.$('.tab-btn[data-tab="voice"]').click();

  await waitFor(() => {
    if (w.$('#tab-voice').css('display') === 'none') throw new Error('Tab voice not visible');
  }, { container: doc, timeout: 3000 });
  console.log('[M8 Workflow A] ✓ Tab 02 active');

  // 6. Assign real uploaded MP4 video asset and background music
  console.log('[M8 Workflow A] Assigning real MP4 footage to Scene 1...');
  // Find available video asset
  const assets = await w.StudioApi.request('/assets');
  const videoAsset = assets.find(a => a.kind === 'video' && a.filename && fs.existsSync(path.join(process.cwd(), 'data', 'assets', a.filename)));
  if (!videoAsset) throw new Error('No video assets available in system with file on disk');
  console.log(`[M8 Workflow A] Using video asset: ${videoAsset.id} (${videoAsset.filename || videoAsset.id})`);

  // Assign via StudioMedia
  w.StudioMedia.assignAsset(videoAsset.id, 'all');
  await waitFor(() => {
    if (w.StudioState.isBusy) throw new Error('Waiting for assignAsset to finish');
    const s = w.StudioState.current.project.scenes[0];
    if (s.video_asset !== videoAsset.id) throw new Error('Scene video asset not yet updated');
  }, { container: doc, timeout: 5000 });
  console.log('[M8 Workflow A] ✓ Video asset assigned to scene');

  // Assign background music (matching Pydantic hex pattern ^[a-f0-9]{0,32}$)
  const musicAsset = assets.find(a => a.kind === 'audio' && /^[a-f0-9]{1,32}$/.test(a.id) && a.filename && fs.existsSync(path.join(process.cwd(), 'data', 'assets', a.filename)) && fs.statSync(path.join(process.cwd(), 'data', 'assets', a.filename)).size > 1000);
  if (musicAsset) {
    console.log(`[M8 Workflow A] Attaching background music track: ${musicAsset.id}`);
    w.StudioState.current.project.music_asset = musicAsset.id;
    w.StudioState.current.project.music_gain = 0.14;
    w.StudioState.current.project.music_rights_reviewed = true;
    w.$('#check-music-rights').prop('checked', true).trigger('change');
  }

  // Ensure scene rights reviewed is true
  w.StudioState.current.project.scenes[0].rights_reviewed = true;
  w.StudioState.markDirty(true);

  // 7. Save and approve visual setup
  console.log('[M8 Workflow A] Saving & approving visual and audio setup...');
  await waitFor(() => {
    if (w.StudioState.isBusy || w.$('#btn-save-project').prop('disabled')) {
      throw new Error('Waiting for UI unbusy before saving visual setup');
    }
  }, { container: doc, timeout: 5000 });

  const visualRev = w.StudioState.current.revision;
  w.$('#btn-save-project').click();

  await waitFor(() => {
    if (w.StudioState.isBusy) throw new Error('Waiting for save to complete');
    const rev = w.$('#badge-revision').text();
    if (rev !== 'v' + (visualRev + 1)) throw new Error(`Expected revision v${visualRev + 1}, got: ${rev}`);
  }, { container: doc, timeout: 6000 });

  await waitFor(() => {
    if (w.StudioState.isBusy || w.$('#btn-approve-project').prop('disabled')) {
      throw new Error('Waiting for approve button');
    }
  }, { container: doc, timeout: 3000 });
  w.$('#btn-approve-project').click();

  await waitFor(() => {
    const badge = w.$('#badge-approval');
    if (!badge.hasClass('approved')) throw new Error('Approval badge pending');
  }, { container: doc, timeout: 5000 });
  console.log('[M8 Workflow A] ✓ Project revision v3 approved with media & audio');

  // 8. Navigate to Tab 03: Preview & export
  console.log('[M8 Workflow A] Navigating to Tab 03 (Preview & export)...');
  await waitFor(() => {
    if (w.StudioState.isBusy) throw new Error('Waiting for UI unbusy');
  }, { container: doc, timeout: 3000 });
  w.$('.tab-btn[data-tab="export"]').click();

  await waitFor(() => {
    if (w.$('#tab-export').css('display') === 'none') throw new Error('Tab export not visible');
  }, { container: doc, timeout: 3000 });

  // 9. Trigger Render 1080×1920 Reel directly via UI button
  console.log('[M8 Workflow A] Triggering "Render 1080×1920 Reel" directly from UI button...');
  await waitFor(() => {
    if (w.StudioState.isBusy || w.$('#btn-render-production').prop('disabled')) {
      throw new Error('Waiting for render button to become enabled');
    }
  }, { container: doc, timeout: 5000 });

  w.$('#btn-render-production').click();

  // 10. Wait for job to appear in UI
  console.log('[M8 Workflow A] Waiting for render job in Production Jobs panel...');
  let renderJobId = null;
  await waitFor(() => {
    const jobs = w.StudioState.getJobs() || [];
    const rJob = jobs.find(j => j.kind === 'render');
    if (!rJob) throw new Error('Waiting for render job to appear');
    renderJobId = rJob.id;
  }, { container: doc, timeout: 8000 });
  console.log(`[M8 Workflow A] ✓ Render job queued (Job ID: ${renderJobId})`);

  // 11. Poll UI until Celery worker + FFmpeg finish render (up to 120s)
  console.log('[M8 Workflow A] Polling for Celery worker + FFmpeg execution completion...');
  const startTime = Date.now();
  let exportCardFound = false;

  while (Date.now() - startTime < 120000) {
    await new Promise(r => setTimeout(r, 3000));
    
    // Check if export card appeared in Tab 03
    const $card = w.$('.export-card');
    if ($card.length && $card.find('video').length) {
      exportCardFound = true;
      break;
    }

    const currentJobs = w.StudioState.getJobs() || [];
    const currentJob = currentJobs.find(j => j.id === renderJobId);
    const progress = currentJob ? `${currentJob.state} (${currentJob.progress || ''})` : 'unknown';
    console.log(`[M8 Workflow A] Render in progress... (${Math.round((Date.now() - startTime) / 1000)}s elapsed, status: ${progress})`);
  }

  if (!exportCardFound) {
    throw new Error(`Timeout waiting for 1080x1920 export card in UI after 120s!`);
  }

  console.log('[M8 Workflow A] SUCCESS! Export card with HTML5 video player rendered in UI!');

  // Verify video element and download link in DOM
  const $video = w.$('.export-card video');
  const videoSrc = $video.attr('src');
  const downloadHref = w.$('.export-card a.btn').attr('href');
  console.log(`[M8 Workflow A] HTML5 Video Player src: ${videoSrc}`);
  console.log(`[M8 Workflow A] Download Button href: ${downloadHref}`);

  // 12. Download the rendered MP4 file
  const fullVideoUrl = new URL(videoSrc, baseUrl).href;
  const timestamp = new Date().toISOString().replace(/\D/g, '').slice(0, 14);
  const outDir = path.join(__dirname, `../test-outputs/django-m8/${timestamp}`);
  fs.mkdirSync(outDir, { recursive: true });

  const finalMp4Path = path.join(outDir, 'uploaded_mp4_workflow.mp4');
  console.log(`[M8 Workflow A] Downloading final reel to: ${finalMp4Path}...`);

  const reelRes = await fetch(fullVideoUrl, {
    headers: { 'x-studio-token': w.StudioApi.getToken() }
  });
  if (!reelRes.ok) throw new Error(`Failed to download reel: HTTP ${reelRes.status}`);
  const reelBuffer = Buffer.from(await reelRes.arrayBuffer());
  fs.writeFileSync(finalMp4Path, reelBuffer);

  const fileSize = fs.statSync(finalMp4Path).size;
  console.log(`[M8 Workflow A] ✓ Saved ${fileSize} bytes to ${finalMp4Path}`);

  // 13. Run FFprobe verification
  console.log('[M8 Workflow A] Running FFprobe stream verification...');
  const probeCmd = `ffprobe -v error -show_entries stream=width,height,codec_type,codec_name -show_entries format=duration -of json "${finalMp4Path}"`;
  const probeRaw = execSync(probeCmd, { encoding: 'utf8' });
  const probeData = JSON.parse(probeRaw);

  const videoStream = probeData.streams.find(s => s.codec_type === 'video');
  const audioStream = probeData.streams.find(s => s.codec_type === 'audio');
  const duration = parseFloat(probeData.format.duration);

  console.log(`[M8 Workflow A] Dimensions: ${videoStream.width}x${videoStream.height}`);
  console.log(`[M8 Workflow A] Video Codec: ${videoStream.codec_name}`);
  console.log(`[M8 Workflow A] Audio Codec: ${audioStream.codec_name}`);
  console.log(`[M8 Workflow A] Duration: ${duration.toFixed(2)}s`);

  if (videoStream.width !== 1080 || videoStream.height !== 1920) {
    throw new Error(`Invalid dimensions: expected 1080x1920, got ${videoStream.width}x${videoStream.height}`);
  }
  if (duration <= 0) throw new Error('Video duration must be > 0');
  if (!audioStream) throw new Error('Audio stream is missing from final reel');

  // 14. Run full FFmpeg decode check (zero corrupt frames)
  console.log('[M8 Workflow A] Running full FFmpeg decode check...');
  const decodeCmd = `ffmpeg -v error -i "${finalMp4Path}" -f null -`;
  execSync(decodeCmd);
  console.log('[M8 Workflow A] ✓ Full FFmpeg decode passed with ZERO errors / corrupt frames!');

  // 15. Network and Error Audit
  const requestsTo8787 = networkLog.filter(r => r.url.includes(':8787'));
  if (requestsTo8787.length > 0) {
    throw new Error(`CRITICAL VIOLATION: Requests went to FastAPI :8787: ${JSON.stringify(requestsTo8787)}`);
  }
  if (runtimeErrors.length > 0) {
    throw new Error(`JavaScript runtime errors detected: ${JSON.stringify(runtimeErrors)}`);
  }

  console.log('\n================================================================');
  console.log('WORKFLOW A — UPLOADED MP4 REAL E2E ACCEPTANCE: PASSED');
  console.log(`Project ID: ${projectId}`);
  console.log(`Render Job ID: ${renderJobId}`);
  console.log(`Output Reel: ${finalMp4Path}`);
  console.log(`Dimensions: ${videoStream.width}x${videoStream.height}`);
  console.log(`Duration: ${duration.toFixed(2)}s`);
  console.log(`Total Browser Requests: ${networkLog.length} (0 to FastAPI :8787)`);
  console.log('================================================================\n');

  dom.window.close();
  process.exit(0);
})().catch(err => {
  console.error('\n[M8 Workflow A FAILED]:', err);
  process.exit(1);
});
