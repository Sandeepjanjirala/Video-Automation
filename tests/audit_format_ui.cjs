// Video format switch (Instagram Reel / YouTube video) on the rendered Django page: real jQuery modules, mocked HTTP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const { JSDOM } = await import('../frontend/node_modules/jsdom/lib/api.js');
  const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {runScripts: 'outside-only', url: 'http://localhost:8788/'});
  const w = dom.window;
  const root = path.join(__dirname, '..', 'studio', 'static');
  w.eval(fs.readFileSync(path.join(root, 'vendor/jquery.min.js'), 'utf8'));
  for (const name of ['state', 'workflow', 'captionstyle', 'framing', 'scenes', 'projects', 'voice', 'jobs', 'media', 'captions', 'autopilot']) {
    w.eval(fs.readFileSync(path.join(root, 'js', name + '.js'), 'utf8'));
  }
  const $ = w.jQuery;
  const plain = v => JSON.parse(JSON.stringify(v));
  const request = async url => {
    if (url.startsWith('/assets/')) return {width: 1920, height: 1080, duration: 12, rotation: 0};
    if (url.endsWith('/preflight')) return {ready: true, errors: [], warnings: []};
    return [];
  };
  w.StudioApi = {request, req: request, blob: async () => ({blob: new w.Blob(['x']), headers: {get: () => null}})};
  w.URL.createObjectURL = () => 'blob:x';
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); w.StudioFraming.init(); w.StudioCaptionStyle.init();
  w.Projects.triggerAutosave = () => {};
  const S = w.StudioState, P = w.Projects;

  function load(changes, count) {
    const p = {...S.createEmptyProject(), ...changes};
    p.scenes = Array.from({length: count || 1}, (_, i) => ({...S.createEmptyScene(), id: 's' + i, roman: 'Katha ' + i,
      visual: 'stock', video_asset: 'a'.repeat(32), rights_reviewed: true}));
    S.setProjects([{id: 'p1', revision: 1, approved: false, project: p}]);
    P.sync({id: 'p1', revision: 1, approved: false, project: p});
    w.Scenes.renderSceneVisuals();
  }
  const durations = () => $('#select-project-duration option').map((i, el) => +el.value).get();

  // New projects are Instagram Reels: vertical frame, reel durations.
  assert.equal(S.createEmptyProject().format, 'reel');
  load({});
  assert.equal($('#select-project-format').val(), 'reel');
  assert.deepEqual(plain(durations()), [30, 45, 60, 90, 180]);
  assert.equal($('#badge-format').text(), 'Reel 9:16');
  assert.equal($('body').hasClass('format-youtube'), false);
  assert.deepEqual(plain(w.StudioFraming.dims()), [1080, 1920]);
  assert.match($('.format-size').first().text(), /1080×1920/);

  // Choosing YouTube video switches the frame, durations and labels, and moves the target to 10 minutes.
  $('#select-project-format').val('youtube').trigger('change');
  const p = S.current.project;
  assert.equal(p.format, 'youtube');
  assert.equal(p.target_seconds, 600);
  assert.equal(S.isDirty, true);
  assert.deepEqual(plain(durations()), [300, 600, 900, 1200, 1800]);
  assert.equal($('#select-project-duration').val(), '600');
  assert.equal($('#badge-format').text(), 'YouTube 16:9');
  assert.equal($('body').hasClass('format-youtube'), true);
  assert.match($('.format-size').first().text(), /1920×1080/);
  assert.match($('.format-draft-size').first().text(), /960×540/);
  assert.match($('.format-name').first().text(), /YouTube video/);
  assert.match($('#format-hint').text(), /landscape, up to 400 scenes/);
  assert.match($('#projects-nav .project-item small, #project-list-nav .project-item small').first().text(), /YouTube · 10 min target/);
  assert.deepEqual(plain(w.StudioFraming.dims()), [1920, 1080]);
  assert.match($('.framing-bind[data-field="frame_aspect"] option[value="full"]').first().text(), /Full frame 16:9/);
  $('#select-project-duration').val('1200').trigger('change');
  assert.equal(p.target_seconds, 1200);
  assert.match($('#projects-nav .project-item small, #project-list-nav .project-item small').first().text(), /YouTube · 20 min target/);

  // Captions sit lower and wider in a landscape frame (studio/formats.py caption_margin).
  assert.equal(S.captionMargin(p), 84);
  const $cap = $('<div class="framecaption"></div>');
  w.StudioCaptionStyle.overlay($cap, 'Mana Bhoomi meedha unna samudralu chala lothuga unnayi mariyu rahasyalu', p);
  assert.equal($cap[0].style.bottom, (84 / 1080 * 100) + '%');
  assert.equal($cap.find('.capline').length, 2);                          // 48 characters a line, not 32

  // A long script splits into more than 30 scenes only for a YouTube video.
  const script = Array.from({length: 45}, (_, i) => `Vakyam number ${i}.`).join('\n');
  assert.equal(w.Scenes.splitScript(script, true).length, 45);
  $('#select-project-format').val('reel').trigger('change');
  assert.equal(p.format, 'reel');
  assert.equal(p.target_seconds, 60);                                       // back inside the reel range
  assert.throws(() => w.Scenes.splitScript(script, true), /Instagram Reel supports at most 30 scenes \(this script has 45\).*choose "YouTube video"/);

  // A project with more scenes than a reel allows cannot switch back to a reel.
  load({format: 'youtube', target_seconds: 1200}, 40);
  assert.equal($('#select-project-format').val(), 'youtube');
  $('#select-project-format').val('reel').trigger('change');
  assert.equal(S.current.project.format, 'youtube');
  assert.equal($('#select-project-format').val(), 'youtube');
  assert.match($('#alert-notice-text').text(), /Instagram Reel supports up to 30 scenes; this project has 40/);

  dom.window.close();
  console.log('Video format DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
