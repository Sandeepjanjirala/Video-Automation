// Framing preview on the rendered Django page: real jQuery modules, mocked HTTP and media.
// argv[3] is a JSON file of geometry cases computed by studio/framing.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const { JSDOM } = await import('../frontend/node_modules/jsdom/lib/api.js');
  const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {runScripts: 'outside-only', url: 'http://localhost:8788/'});
  const w = dom.window;
  const root = path.join(__dirname, '..', 'studio', 'static');
  w.eval(fs.readFileSync(path.join(root, 'vendor/jquery.min.js'), 'utf8'));
  for (const name of ['state', 'workflow', 'captionstyle', 'framing', 'scenes', 'projects', 'voice', 'jobs', 'media', 'captions']) {
    w.eval(fs.readFileSync(path.join(root, 'js', name + '.js'), 'utf8'));
  }
  const $ = w.jQuery;
  const F = w.StudioFraming;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const plain = value => JSON.parse(JSON.stringify(value));

  // The browser computes the renderer's geometry exactly -------------------------------
  const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  assert.ok(cases.length > 100);
  for (const c of cases) {
    assert.deepEqual(plain(F.geometry(...c.args)), c.expected, JSON.stringify(c.args));
  }
  assert.deepEqual(plain(F.frameWindow(1080, 1920, '4:5')), [0, 286, 1080, 1350]);
  assert.deepEqual(plain(F.segment({trim_start: 2, trim_end: 0}, 10)), {start: 2, end: 10});
  assert.deepEqual(plain(F.segment({trim_start: 2, trim_end: 12}, 10)), {start: 2, end: 10});
  assert.match(F.segment({trim_start: 4, trim_end: 3}, 10).error, /after trim start/);
  assert.match(F.segment({trim_start: 9.99, trim_end: 0}, 10).error, /before the end/);
  assert.match(F.segment({trim_start: 1, trim_end: 1.05}, 10).error, /at least 0.1/);
  assert.deepEqual(plain(F.wrap('one two three four five six seven eight nine', 32)), ['one two three four five six', 'seven eight nine']);
  assert.equal(F.captionSample({cues: [], roman: 'Idi oka chinna pariksha mana katha'}), 'Idi oka chinna pariksha');
  assert.equal(F.captionSample({cues: [{start: 0, end: 1, text: 'నమస్కారం'}], roman: 'x'}), 'నమస్కారం');

  // Editor ---------------------------------------------------------------------------
  const video = 'b'.repeat(32), image = 'c'.repeat(32);
  const sources = {
    [video]: {id: video, kind: 'video', width: 1920, height: 1080, rotation: 0, duration: 10},
    [image]: {id: image, kind: 'image', width: 1200, height: 1600, rotation: 0, duration: 0},
  };
  const calls = [];
  const request = async (url, method, body) => {
    if (typeof method === 'object') [body, method] = [method, body];
    calls.push({url, method, body});
    const m = url.match(/^\/assets\/([a-f0-9]+)\/framing$/);
    if (m) return plain(sources[m[1]]);
    return [];
  };
  let blobResult = null;
  const blobCalls = [];
  w.StudioApi = {request, req: request, blob: async (url, body) => {
    blobCalls.push({url, body: plain(body)});
    if (blobResult instanceof Error) throw blobResult;
    return blobResult;
  }};
  w.URL.createObjectURL = () => 'blob:frame-1';
  w.URL.revokeObjectURL = () => {};
  // jsdom has no media pipeline: give videos a controllable clock.
  Object.defineProperty(w.HTMLMediaElement.prototype, 'currentTime', {
    configurable: true, get() { return this._t || 0; }, set(v) { this._t = v; }});
  Object.defineProperty(w.HTMLMediaElement.prototype, 'paused', {configurable: true, get() { return !this._playing; }});
  w.HTMLMediaElement.prototype.play = function() { this._playing = true; return Promise.resolve(); };
  w.HTMLMediaElement.prototype.pause = function() { this._playing = false; };
  w.Projects.triggerAutosave = () => {};
  let preflights = 0;
  w.Projects.checkPreflight = () => { preflights += 1; };
  w.confirm = () => true;
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); F.init();

  const p = w.StudioState.createEmptyProject();
  const empty = w.StudioState.createEmptyScene();
  assert.equal(empty.zoom, 1);
  assert.equal(empty.crop_y, 0.5);
  assert.equal(empty.frame_aspect, 'full');
  assert.equal(empty.trim_end, 0);
  // '123456' would become a number through jQuery .data(); IDs are read as strings.
  p.scenes = [
    {...empty, id: '123456', roman: 'Idi oka chinna pariksha', visual: 'upload', video_asset: video, trim_start: 2},
    {...empty, id: 'pic', roman: 'Rendava', visual: 'image', video_asset: image},
    {...empty, id: 'title', roman: 'Title', visual: 'graphic', graphic_title: 'Heading'},
  ];
  // An older saved scene without the Stage 3 fields still previews with defaults.
  delete p.scenes[1].zoom; delete p.scenes[1].crop_y; delete p.scenes[1].frame_aspect;
  w.Projects.sync({id: 'fixture', revision: 1, approved: true, project: p});
  await tick(); await tick();

  const card = sid => $('.scene-visual-card').filter(function() { return $(this).attr('data-id') === sid; });
  const pos = ($el, prop) => parseFloat($el[0].style[prop]);
  assert.equal(card('123456').find('.framestage video.framemedia').length, 1);
  assert.equal(card('pic').find('.framestage img.framemedia').length, 1);
  assert.equal(card('title').find('.framestage').length, 0);
  assert.equal(card('pic').find('.framing-bind[data-field="trim_start"]').length, 0);
  assert.ok(calls.some(c => c.url === `/assets/${video}/framing`));

  // Landscape footage fills the vertical frame: scaled to 3414x1920, centred crop.
  const crop = card('123456').find('.framecrop'), media = card('123456').find('.framemedia');
  assert.equal(pos(crop, 'width'), 100);
  assert.equal(pos(crop, 'height'), 100);
  assert.ok(Math.abs(pos(media, 'width') - 3414 / 1080 * 100) < 1e-9);
  assert.ok(Math.abs(pos(media, 'left') + 1168 / 1080 * 100) < 1e-9);
  assert.match(card('123456').find('.framing-geometry').text(), /scaled to 3414×1920; showing 1080×1920 from \(1168, 0\)/);
  assert.equal(card('123456').find('.framing-bind[data-field="crop_y"]').prop('disabled'), true);
  assert.equal(card('123456').find('.framing-bind[data-field="crop_x"]').prop('disabled'), false);
  assert.match(card('123456').find('.framing-trim').text(), /Using 2.00–10.00 s of the 10.00 s clip/);
  assert.equal(card('123456').find('.framecaption').text(), 'Idi oka chinna pariksha');
  assert.equal(pos(card('123456').find('.framecaption'), 'bottom'), 300 / 1920 * 100);

  // Sliders preview immediately and commit on change.
  const scene = p.scenes[0];
  preflights = 0;
  card('123456').find('.framing-bind[data-field="crop_x"]').val(0).trigger('input');
  assert.equal(scene.crop_x, 0);
  assert.equal(w.StudioState.getDirty(), true);
  assert.equal(preflights, 0);
  assert.equal(pos(card('123456').find('.framemedia'), 'left'), 0);
  card('123456').find('.framing-bind[data-field="crop_x"]').trigger('change');
  assert.equal(preflights, 1);
  card('123456').find('.framing-bind[data-field="zoom"]').val(2).trigger('change');
  assert.equal(scene.zoom, 2);
  assert.equal(card('123456').find('.framing-bind[data-field="crop_y"]').prop('disabled'), false);
  assert.match(card('123456').find('.framing-out[data-for="zoom"]').text(), /2.00×/);
  card('123456').find('.framing-bind[data-field="frame_aspect"]').val('4:5').trigger('change');
  card('123456').find('.framing-bind[data-field="fit"]').val('contain').trigger('change');
  assert.equal(scene.frame_aspect, '4:5');
  assert.equal(scene.fit, 'contain');
  const g = F.sceneGeometry(scene, sources[video]);
  assert.ok(Math.abs(pos(card('123456').find('.framecrop'), 'top') - g.pad_y / 1920 * 100) < 1e-9);

  // Dragging pans within the overflow and commits once on release.
  const stage = card('123456').find('.framestage');
  assert.ok(stage.hasClass('pannable'));
  stage[0].getBoundingClientRect = () => ({width: 270, height: 480, left: 0, top: 0});
  const pointer = (type, x, y) => stage[0].dispatchEvent(new w.MouseEvent(type, {bubbles: true, clientX: x, clientY: y}));
  scene.crop_x = 0.5; scene.crop_y = 0.5;
  preflights = 0;
  pointer('pointerdown', 100, 100);
  pointer('pointermove', 90, 100);
  const moved = F.sceneGeometry(scene, sources[video]);
  assert.ok(scene.crop_x > 0.5);
  assert.equal(scene.crop_y, 0.5);
  assert.equal(preflights, 0);
  pointer('pointerup', 90, 100);
  assert.equal(preflights, 1);
  assert.ok(Math.abs(moved.crop_x - (moved.scale_w - moved.crop_w) * 0.5 - 40) <= 2);

  // Trim: playhead buttons, validation and loop playback within the trimmed range.
  const v = card('123456').find('video.framemedia')[0];
  v.currentTime = 3.25;
  card('123456').find('.btn-framing-mark[data-field="trim_end"]').trigger('click');
  assert.equal(scene.trim_end, 3.25);
  assert.equal(card('123456').find('.framing-bind[data-field="trim_end"]').val(), '3.25');
  assert.match(card('123456').find('.framing-trim').text(), /Using 2.00–3.25 s/);
  card('123456').find('.framing-bind[data-field="trim_start"]').val(4).trigger('input');
  assert.match(card('123456').find('.framing-trim').text(), /Trim end must be after trim start/);
  card('123456').find('.framing-bind[data-field="trim_start"]').val(2).trigger('change');
  assert.equal(v.currentTime, 2);
  card('123456').find('.btn-framing-play').trigger('click');
  assert.equal(v.paused, false);
  v.currentTime = 3.24;
  await new Promise(r => setTimeout(r, 60));
  assert.equal(v.currentTime, 2, 'loops back to trim start');
  scene.short_clip_mode = 'freeze';
  v.currentTime = 3.24;
  await new Promise(r => setTimeout(r, 60));
  assert.equal(v.paused, true, 'holds the last frame');
  assert.ok(Math.abs(v.currentTime - (3.25 - 1 / 30)) < 1e-9);
  card('123456').find('.btn-framing-clear-end').trigger('click');
  assert.equal(scene.trim_end, 0);

  // Exact frame: sends the unsaved scene, playhead and caption; shows the FFmpeg image.
  v.currentTime = 2.5;
  blobResult = {blob: new w.Blob(['jpeg'], {type: 'image/jpeg'}),
                headers: {get: () => JSON.stringify(F.sceneGeometry(scene, sources[video]))}};
  await F.renderExact('123456');
  const sent = blobCalls.at(-1);
  assert.equal(sent.url, '/projects/fixture/framing-preview');
  assert.equal(sent.body.source_time, 2.5);
  assert.equal(sent.body.scene.zoom, 2);
  assert.equal(sent.body.caption, 'Idi oka chinna pariksha');
  assert.equal(sent.body.quality, 'final');
  assert.equal(card('123456').find('.frameexact').prop('hidden'), false);
  assert.equal(card('123456').find('.frameexact').attr('src'), 'blob:frame-1');
  assert.equal(card('123456').find('.framelabel').text(), 'FFmpeg frame');
  assert.doesNotMatch(card('123456').find('.framing-status').text(), /differs/);
  // Any framing change returns to the live preview, since the frame is now stale.
  card('123456').find('.framing-bind[data-field="zoom"]').val(1.5).trigger('input');
  assert.equal(card('123456').find('.frameexact').prop('hidden'), true);
  blobResult = new Error('Trim start must be before the end of the video');
  await F.renderExact('123456');
  assert.match(card('123456').find('.framing-status').text(), /Could not render the frame: Trim start/);
  card('123456').find('.framing-captions').prop('checked', false).trigger('change');
  assert.equal(card('123456').find('.framecaption').prop('hidden'), true);

  // Reset restores the renderer's defaults; the image scene keeps defaults throughout.
  card('123456').find('.btn-framing-reset').trigger('click');
  assert.deepEqual([scene.fit, scene.frame_aspect, scene.zoom, scene.crop_x, scene.crop_y], ['crop', 'full', 1, 0.5, 0.5]);
  assert.match(card('pic').find('.framing-geometry').text(), /Source 1200×1600 → scaled to 1440×1920; showing 1080×1920 from \(180, 0\)/);
  assert.equal(p.scenes[1].zoom, undefined);

  dom.window.close();
  console.log('Framing preview DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
