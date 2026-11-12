// Narration on the Voice tab: one recording for the whole video and one per scene (real jQuery modules, mocked HTTP).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const nodeCrypto = require('node:crypto');
const util = require('node:util');

(async () => {
  const { JSDOM } = await import('../frontend/node_modules/jsdom/lib/api.js');
  const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {runScripts: 'outside-only', url: 'http://localhost:8788/'});
  const w = dom.window;
  if (!w.TextEncoder) w.TextEncoder = util.TextEncoder;
  Object.defineProperty(w, 'crypto', {value: nodeCrypto.webcrypto, configurable: true});
  const root = path.join(__dirname, '..', 'studio', 'static');
  w.eval(fs.readFileSync(path.join(root, 'vendor/jquery.min.js'), 'utf8'));
  for (const name of ['state', 'ui', 'workflow', 'captionstyle', 'framing', 'scenes', 'projects', 'voice', 'jobs', 'media', 'captions',
                      'autopilot', 'narration']) {
    w.eval(fs.readFileSync(path.join(root, 'js', name + '.js'), 'utf8'));
  }
  const $ = w.jQuery;
  const S = w.StudioState;
  const plain = v => JSON.parse(JSON.stringify(v));
  const wait = async test => { for (let i = 0; i < 200 && !test(); i++) await new Promise(r => setTimeout(r, 5)); assert.ok(test()); };
  const A = 'a'.repeat(32), B = 'b'.repeat(32), C = 'c'.repeat(32);

  const analysis = id => ({asset_id: id, name: id === A ? 'take1.m4a' : 'take2.m4a', duration: 5.8, peaks_per_second: 50,
                           peaks: Array.from({length: 290}, (_, i) => (i % 25) * 4), pauses: [[1.0, 1.5], [3.5, 4.3]],
                           speech: [[0, 1], [1.5, 3.5], [4.3, 5.8]], loudness: -19.5, peak: -3, sample_rate: 44100, channels: 1,
                           warnings: id === A ? ['Background noise is loud compared with the voice.'] : []});
  let uploads = [], requests = [], nextUpload = A;
  const request = async (url, method, body) => {
    requests.push({url, method: method || 'GET', body: body && plain(body)});
    const m = url.match(/^\/assets\/([0-9a-f]{32})\/narration$/);
    if (m && (method || 'GET') === 'GET') return analysis(m[1]);
    if (m && body.action === 'place') return {starts: [0, 1.25, 3.9].slice(0, body.texts.length), duration: 5.8};
    if (m && body.action === 'split') return {starts: [0, 1.25, 3.9, 5.0], duration: 5.8};
    if (url.endsWith('/preflight')) return {ready: false, errors: [], warnings: []};
    return [];
  };
  w.StudioApi = {request, req: request, upload: async (url, fd) => {
    uploads.push({url, purpose: fd.get('purpose'), name: fd.get('file').name});
    const id = nextUpload;
    return {id, kind: 'audio', name: fd.get('file').name};
  }, blob: async () => ({blob: new w.Blob(['x']), headers: {get: () => null}})};
  w.confirm = () => true;
  w.Projects.init(); w.Scenes.init(); w.StudioMedia.init(); w.StudioNarration.init();
  w.Projects.triggerAutosave = () => {};
  w.Projects.checkPreflight = () => {};
  const notice = () => $('#alert-notice-text').text();

  const p = {...S.createEmptyProject(), scenes: ['Mana sareeram', 'Gunde roju laksha saarlu kottukuntundhi', 'Follow cheyyandi']
    .map(roman => ({...S.createEmptyScene(), roman}))};
  assert.equal(p.narration_mode, 'voice');
  assert.deepEqual(plain(p.narration_track), {asset: '', end: 0, gain_db: 0});
  assert.equal(p.scenes[0].track_start, null);
  w.Projects.sync({id: 'p1', revision: 1, approved: false, project: p});
  const project = () => S.current.project;
  w.StudioNarration.render();

  // The voice engine is the default; its panel shows, the recording boxes do not.
  assert.equal($('input[name="narration-source"]:checked').val(), 'voice');
  assert.equal($('#narration-track-box').prop('hidden'), true);
  assert.equal($('#voice-engine-panel').prop('hidden'), false);

  // One recording for the whole video: choose it, upload, and the scenes are placed by their text.
  $('input[name="narration-source"][value="track"]').prop('checked', true).trigger('change');
  assert.equal(project().narration_mode, 'track');
  assert.equal($('#voice-engine-panel').prop('hidden'), true);
  assert.equal($('#narration-track-box .track-no-recording').prop('hidden'), false);
  const file = name => new w.File(['x'], name, {type: 'audio/mp4'});
  const pick = (sel, f) => { Object.defineProperty($(sel)[0], 'files', {value: [f], configurable: true}); $(sel).trigger('change'); };
  pick('#input-narration-track', file('take1.m4a'));
  await wait(() => project().scenes.every(s => s.track_start != null));
  assert.deepEqual(uploads.map(u => [u.purpose, u.name]), [['narration', 'take1.m4a']]);
  assert.deepEqual(plain(project().narration_track), {asset: A, end: 0, gain_db: 0});
  assert.deepEqual(project().scenes.map(s => s.track_start), [0, 1.25, 3.9]);
  const place = requests.find(r => r.method === 'POST' && r.body.action === 'place');
  assert.deepEqual(place.body.texts, project().scenes.map(s => s.roman));
  assert.equal($('#narration-track-scenes tbody tr').length, 3);
  assert.match($('#narration-track-status').text(), /All 3 scene\(s\) placed/);
  assert.match($('#narration-track-info').text(), /take1\.m4a · 5\.80 s · 44\.1 kHz mono/);
  assert.match($('#narration-track-warnings').text(), /Background noise/);
  assert.equal($('#narration-track-audio').attr('src'), `/api/assets/${A}/media`);
  assert.equal(S.isDirty, true);

  // Typed start times keep the scenes in the recording's order (at least 0.3 s apart); empty means not placed.
  const row = k => $(`#narration-track-scenes tbody tr[data-k="${k}"]`);
  row(1).find('.track-start-input').val('4.5').trigger('change');
  assert.equal(project().scenes[1].track_start, 3.6);
  row(2).find('.track-start-input').val('').trigger('change');
  assert.equal(project().scenes[2].track_start, null);
  assert.match($('#narration-track-status').text(), /1 of 3 scene\(s\) not placed/);
  assert.ok(row(2).hasClass('track-row-bad'));
  $('#btn-track-place').trigger('click');
  await wait(() => project().scenes[2].track_start === 3.9);
  assert.deepEqual(project().scenes.map(s => s.track_start), [0, 1.25, 3.9]);
  assert.match(row(1).find('.track-length').text(), /2\.65 s/);

  // "At playhead" sets a start from the player; volume and the narration's end are settings.
  const audio = $('#narration-track-audio')[0];
  Object.defineProperty(audio, 'currentTime', {value: 1.4, writable: true, configurable: true});
  row(1).find('.btn-track-set-start').trigger('click');
  assert.equal(project().scenes[1].track_start, 1.4);
  $('#range-track-gain').val('3').trigger('change');
  assert.equal(project().narration_track.gain_db, 3);
  assert.equal($('#label-track-gain').text(), '+3 dB');
  $('#input-track-end').val('5.5').trigger('change');
  assert.equal(project().narration_track.end, 5.5);
  assert.match(row(2).find('.track-length').text(), /1\.60 s/);

  // A new take: scenes are placed again; the previous boundaries can be restored.
  nextUpload = B;
  pick('#input-narration-track-replace', file('take2.m4a'));
  await wait(() => project().narration_track.asset === B && project().scenes[1].track_start === 1.25);
  assert.equal(project().narration_track.end, 0);
  assert.equal($('#btn-track-restore').prop('hidden'), false);
  $('#btn-track-restore').trigger('click');
  assert.deepEqual(project().scenes.map(s => s.track_start), [0, 1.4, 3.9]);

  // With one recording, scenes follow its order: moving a scene is refused with a reason.
  const first = project().scenes[0].id;
  w.Scenes.moveSceneUp(project().scenes[1].id);
  assert.equal(project().scenes[0].id, first);
  assert.match(notice(), /scenes follow the order of the recording/);

  // No script yet: make scenes from the pauses (written scenes are only replaced after a confirmation).
  w.confirm = () => false;
  $('#btn-track-split').trigger('click');
  await new Promise(r => setTimeout(r, 20));
  assert.equal(project().scenes.length, 3);
  w.confirm = () => true;
  $('#btn-track-split').trigger('click');
  await wait(() => project().scenes.length === 4);
  assert.deepEqual(project().scenes.map(s => [s.track_start, s.roman]), [[0, ''], [1.25, ''], [3.9, ''], [5.0, '']]);
  assert.match(notice(), /4 scene\(s\) made at the pauses/);

  // One recording per scene: each scene uploads its own file, with trim and volume.
  $('input[name="narration-source"][value="scenes"]').prop('checked', true).trigger('change');
  assert.equal(project().narration_mode, 'voice');
  assert.equal($('#narration-scenes-box').prop('hidden'), false);
  assert.equal($('#voice-engine-panel').prop('hidden'), false);
  assert.equal($('#narration-scenes-list .scene-recording-row').length, 4);
  project().scenes[0].narration = 'మన శరీరం';
  nextUpload = C;
  pick('#narration-scenes-list .scene-recording-row[data-k="0"] .scene-upload-audio-input', file('scene1.wav'));
  await wait(() => project().scenes[0].audio_asset === C);
  const s0 = project().scenes[0];
  assert.equal(uploads[uploads.length - 1].purpose, 'narration');
  assert.equal(s0.audio_text_hash, nodeCrypto.createHash('sha256').update(JSON.stringify('మన శరీరం')).digest('hex'));
  assert.deepEqual([s0.audio_trim_start, s0.audio_trim_end, s0.audio_gain_db], [0, 0, 0]);
  await wait(() => $('#narration-scenes-list .scene-recording-row[data-k="0"] .scene-audio-trim').length === 2);
  const r0 = () => $('#narration-scenes-list .scene-recording-row[data-k="0"]');
  r0().find('.scene-audio-trim[data-field="audio_trim_start"]').val('0.45').trigger('change');
  r0().find('.scene-audio-trim[data-field="audio_trim_end"]').val('2.3').trigger('change');
  r0().find('.scene-audio-gain').val('-3').trigger('change');
  assert.deepEqual([s0.audio_trim_start, s0.audio_trim_end, s0.audio_gain_db], [0.45, 2.3, -3]);
  // Editing the text after recording asks for a confirmation instead of dropping the recording.
  s0.narration = 'మన శరీరం గురించి';
  w.StudioNarration.render();
  await wait(() => r0().find('.btn-confirm-scene-audio').length === 1);
  r0().find('.btn-confirm-scene-audio').trigger('click');
  await wait(() => s0.audio_text_hash === nodeCrypto.createHash('sha256').update(JSON.stringify('మన శరీరం గురించి')).digest('hex'));
  r0().find('.btn-remove-scene-audio').trigger('click');
  assert.deepEqual([s0.audio_asset, s0.audio_trim_start, s0.audio_trim_end, s0.audio_gain_db], ['', 0, 0, 0]);

  // The guide has a section for your own voice, opened from the panel's "How to use".
  assert.equal($('#guide-narration').length, 1);
  assert.equal($('#narration-panel .btn-open-guide').attr('data-guide'), 'narration');

  dom.window.close();
  console.log('Narration panel DOM regression passed');
})().catch(e => { console.error(e); process.exit(1); });
