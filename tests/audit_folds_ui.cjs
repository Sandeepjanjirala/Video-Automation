// Simpler screens: fold-outs and the "How to use" guide on the rendered Django page (real jQuery modules, mocked HTTP).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const { JSDOM } = await import('../frontend/node_modules/jsdom/lib/api.js');
  const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {runScripts: 'outside-only', url: 'http://localhost:8788/'});
  const w = dom.window;
  const root = path.join(__dirname, '..', 'studio', 'static');
  w.eval(fs.readFileSync(path.join(root, 'vendor/jquery.min.js'), 'utf8'));
  for (const name of ['state', 'ui', 'workflow', 'captionstyle', 'framing', 'scenes', 'projects', 'voice', 'jobs', 'media', 'captions', 'autopilot']) {
    w.eval(fs.readFileSync(path.join(root, 'js', name + '.js'), 'utf8'));
  }
  const $ = w.jQuery;
  let approveError = null;
  const request = async (url, method) => {
    if (url.endsWith('/approve')) { if (approveError) throw new Error(approveError); return {}; }
    if (url.startsWith('/assets/')) return {width: 1920, height: 1080, duration: 12, rotation: 0};
    if (url.endsWith('/preflight')) return {ready: false, errors: [], warnings: []};
    if (url.endsWith('/caption-timing')) return {scenes: []};
    return [];
  };
  w.StudioApi = {request, req: request, blob: async () => ({blob: new w.Blob(['x']), headers: {get: () => null}})};
  w.URL.createObjectURL = () => 'blob:x';
  w.Element.prototype.scrollIntoView = function() {};
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); w.StudioFraming.init(); w.StudioCaptionStyle.init();
  w.StudioFold.init(); w.StudioGuide.init();
  w.Projects.triggerAutosave = () => {};
  const switched = [];
  w.StudioApp = {switchTab: tab => switched.push(tab)};

  const p = w.StudioState.createEmptyProject();
  p.scenes = [{...w.StudioState.createEmptyScene(), id: 's1', roman: 'Octopus ki moodu hearts', narration: 'ఆక్టోపస్',
               visual: 'stock', video_asset: 'a'.repeat(32), keywords: 'octopus'}];
  w.Projects.sync({id: 'p1', revision: 1, approved: false, project: p});
  w.Scenes.renderScenes();
  const inClosedFold = sel => $(sel).first().parents('details').toArray().some(d => !d.open);

  // Nothing was removed: every control the studio had is still on the page.
  for (const id of ['input-project-title', 'select-project-category', 'input-project-topic', 'select-project-format', 'select-project-tone',
                    'select-project-duration', 'script-input-format', 'input-import-script', 'btn-import-split', 'btn-autopilot',
                    'textarea-evidence-notes', 'btn-toggle-research', 'select-voice-provider', 'select-voice-speaker', 'select-delivery-preset',
                    'range-speech-rate', 'check-voice-lock', 'btn-free-preview-voice', 'btn-upgrade-sarvam', 'select-voicelab-scene',
                    'btn-preview-candidate', 'btn-add-dict-entry', 'file-upload-music', 'range-music-gain', 'check-music-rights',
                    'btn-open-media-library', 'btn-save-captions', 'caption-emphasis-toggle', 'style-caption-size', 'caption-style-scene',
                    'range-caption-size', 'range-caption-bottom', 'btn-render-preview', 'btn-render-production', 'btn-add-scene']) {
    assert.equal($('#' + id).length, 1, `#${id} is still there`);
  }

  // Story tab: the script box, format and one-click button are open; project details and review notes start folded.
  for (const sel of ['#input-import-script', '#select-project-format', '#btn-autopilot', '#btn-import-split']) assert.equal(inClosedFold(sel), false, sel);
  for (const sel of ['#input-project-title', '#input-project-topic', '#textarea-evidence-notes']) assert.equal(inClosedFold(sel), true, sel);
  // A scene row: its two boxes and Convert to Telugu are visible; search words and other tools fold into "More".
  const row = $('.scriptrow[data-id="s1"]');
  assert.equal(inClosedFold(row.find('.scene-narration-input')), false);
  assert.equal(inClosedFold(row.find('.btn-scene-transliterate')), false);
  assert.equal(inClosedFold(row.find('.scene-keywords-input')), true);
  assert.equal(inClosedFold(row.find('.btn-remove-scene')), true);

  // Voice & visuals: the picture, a one-line summary, rights and captions stay; footage options fold.
  const card = $('.scene-visual-card[data-id="s1"]');
  assert.match(card.find('.visualnow').text(), /Showing stock footage/);
  assert.equal(inClosedFold(card.find('.framing')), false);
  assert.equal(inClosedFold(card.find('.scene-rights-checkbox')), false);
  for (const sel of ['.scene-visual-source-select', '.btn-find-stock', '.btn-open-library-single', '.framing-bind[data-field="zoom"]'])
    assert.equal(inClosedFold(card.find(sel)), true, sel);
  for (const sel of ['#select-voice-provider', '#select-voicelab-scene', '#file-upload-music', '#range-caption-size']) assert.equal(inClosedFold(sel), true, sel);
  assert.equal(inClosedFold('[data-kind="voice"]'), false);
  assert.equal(inClosedFold('#btn-render-production'), false);

  // An opened fold stays open when the scene cards are redrawn, and after a reload (remembered in this browser).
  const fold = card.find('details[data-fold="scene-footage-s1"]')[0];
  fold.open = true;
  fold.dispatchEvent(new w.Event('toggle'));
  w.Scenes.renderSceneVisuals();
  assert.equal($('.scene-visual-card[data-id="s1"] details[data-fold="scene-footage-s1"]')[0].open, true);
  assert.ok(JSON.parse(w.localStorage.getItem('studio.openFolds')).includes('scene-footage-s1'));
  const again = $('.scene-visual-card[data-id="s1"] details[data-fold="scene-footage-s1"]')[0];
  again.open = false;
  again.dispatchEvent(new w.Event('toggle'));
  assert.equal(JSON.parse(w.localStorage.getItem('studio.openFolds')).includes('scene-footage-s1'), false);

  // "Edit captions & timing" opens that scene on the Captions tab.
  w.StudioCaptions.render();
  const capScene = $('#caption-scenes-container .caption-scene[data-id="s1"]')[0];
  assert.equal(capScene.tagName, 'DETAILS');
  assert.equal(capScene.open, false);
  $('.btn-open-captions[data-scene-id="s1"]').trigger('click');
  assert.equal($('#caption-scenes-container .caption-scene[data-id="s1"]')[0].open, true);

  // Approval without review notes opens them on the Story tab.
  approveError = 'Add your factual review notes, or label fictional content, before approval';
  await w.Projects.approveRevision();
  assert.equal($('details[data-fold="story-review"]')[0].open, true);
  assert.ok(switched.includes('write'));

  // Production jobs is folded, and opens by itself (without being remembered) when a result waits to be applied.
  const jobsPanel = $('#panel-production-jobs')[0];
  assert.equal(jobsPanel.tagName, 'DETAILS');
  assert.equal(jobsPanel.open, false);
  w.StudioJobs.renderJobs([{id: 'j1', kind: 'stock_all', state: 'succeeded', applied: false, revision: 1, progress: 'Complete',
                             result: {selections: []}}]);
  await new Promise(r => setTimeout(r, 20));
  assert.equal(jobsPanel.open, true);
  assert.equal($('#jobs-pending-badge').text(), '1 result(s) to apply');
  assert.equal(JSON.parse(w.localStorage.getItem('studio.openFolds') || '[]').includes('jobs'), false);

  // How to use: shown once on a first visit, then only from the buttons; Esc or Got it closes it.
  assert.equal($('#guide-modal').css('display'), 'none');
  w.StudioGuide.welcome();
  assert.equal($('#guide-modal').css('display'), 'flex');
  $('#btn-close-guide-bottom').trigger('click');
  assert.equal($('#guide-modal').css('display'), 'none');
  w.StudioGuide.welcome();
  assert.equal($('#guide-modal').css('display'), 'none', 'not shown again');
  assert.ok($('.btn-open-guide').length >= 4, 'header, sidebar, welcome and tab buttons');
  $('#btn-open-guide').trigger('click');
  assert.equal($('#guide-modal').css('display'), 'flex');
  $(w.document).trigger($.Event('keydown', {key: 'Escape'}));
  assert.equal($('#guide-modal').css('display'), 'none');
  for (const id of ['guide-setup', 'guide-reel', 'guide-youtube', 'guide-improve', 'guide-phone', 'guide-help']) assert.equal($('#' + id).length, 1, id);
  assert.match($('#guide-reel').text(), /Create draft video automatically/);

  dom.window.close();
  console.log('Fold-outs and guide DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
