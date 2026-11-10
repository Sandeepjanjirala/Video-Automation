// Captions on/off switch and extra shots on the rendered Django page: real jQuery modules, mocked HTTP.
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

  function load(projectChanges, scenes) {
    const p = {...w.StudioState.createEmptyProject(), ...projectChanges};
    p.scenes = scenes.map((s, i) => ({...w.StudioState.createEmptyScene(), id: 's' + i, roman: 'Katha', ...s}));
    w.Projects.sync({id: 'p1', revision: 1, approved: false, project: p});
    w.Scenes.renderSceneVisuals();
  }
  const toggles = () => $('.captions-enabled-toggle').map((i, el) => el.checked).get();

  // New projects have captions on; the switch is on the Captions and Preview & export tabs.
  assert.equal(w.StudioState.createEmptyProject().captions_enabled, true);
  load({}, [{visual: 'stock', video_asset: 'a'.repeat(32), extra_shots: ['b'.repeat(32), 'c'.repeat(32)]},
            {visual: 'image', video_asset: 'd'.repeat(32), extra_shots: ['e'.repeat(32)]}]);
  assert.deepEqual(plain(toggles()), [true, true]);
  assert.equal($('#tab-captions .captions-enabled-toggle').length, 1);
  assert.equal($('#tab-export .captions-enabled-toggle').length, 1);

  // Turning it off on one tab turns the project's captions off and updates the other switch.
  $('#tab-export .captions-enabled-toggle').prop('checked', false).trigger('change');
  assert.equal(w.StudioState.current.project.captions_enabled, false);
  assert.deepEqual(plain(toggles()), [false, false]);
  assert.equal(w.StudioState.isDirty, true);
  $('#tab-captions .captions-enabled-toggle').prop('checked', true).trigger('change');
  assert.equal(w.StudioState.current.project.captions_enabled, true);
  assert.deepEqual(plain(toggles()), [true, true]);

  // A saved project with captions off shows the switch off.
  load({captions_enabled: false}, [{}]);
  assert.deepEqual(plain(toggles()), [false, false]);

  // A stock scene lists its extra shots and can go back to its main clip only; an image scene ignores them.
  load({}, [{visual: 'stock', video_asset: 'a'.repeat(32), extra_shots: ['b'.repeat(32), 'c'.repeat(32)]},
            {visual: 'image', video_asset: 'd'.repeat(32), extra_shots: ['e'.repeat(32)]}]);
  const card = id => $(`.scene-visual-card[data-id="${id}"]`);
  assert.match(card('s0').find('.extrashots').text(), /\+2 more shot\(s\)/);
  assert.equal(card('s1').find('.extrashots').length, 0);
  card('s0').find('.btn-clear-extra-shots').trigger('click');
  assert.deepEqual(plain(w.StudioState.current.project.scenes[0].extra_shots), []);
  assert.equal(card('s0').find('.extrashots').length, 0);
  assert.equal(w.StudioState.isDirty, true);

  dom.window.close();
  console.log('Captions switch and extra shots DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
