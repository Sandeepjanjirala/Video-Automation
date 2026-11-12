// Caption editor on the rendered Django page: real jQuery modules, mocked HTTP and audio.
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
  const C = w.StudioCaptions;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  // jsdom arrays come from another realm; compare plain JSON copies.
  const same = (actual, expected) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);

  // Pure helpers --------------------------------------------------------------------
  assert.equal(C.parseTime('1.25'), 1.25);
  assert.equal(C.parseTime(' 0:01.50 '), 1.5);
  assert.equal(C.parseTime('1,5'), 1.5);
  assert.ok(Number.isNaN(C.parseTime('abc')));
  assert.ok(Number.isNaN(C.parseTime('1:75')));
  assert.ok(Number.isNaN(C.parseTime('-1')));
  const cue = (start, end, text = 'Mana katha') => ({start, end, text});
  const messages = v => v.rows.flat().concat(v.scene).map(x => x.message).join(' | ');
  let v = C.validate([cue('0.00', '1.00'), cue('0.80', '1.50')], 2);
  assert.equal(v.errors, 1);
  assert.match(messages(v), /Overlaps caption 1 by 0.20 s/);
  assert.equal(C.validate([cue('0.00', '1.00'), cue('1.00', '1.50')], 2).errors, 0);  // touching is allowed
  assert.match(messages(C.validate([cue('1.00', '1.00')], 2)), /End must be later/);
  assert.match(messages(C.validate([cue('x', '1.00')], 2)), /Start is not a valid time/);
  assert.equal(C.validate([cue('0.00', '2.10')], 2).errors, 0);  // renderer's 100 ms tolerance
  assert.match(messages(C.validate([cue('0.00', '2.11')], 2)), /Ends after the narration ends at 2.00 s/);
  assert.match(messages(C.validate([cue('2.00', '2.05')], 2)), /Starts after the narration/);
  assert.match(messages(C.validate([cue('0.00', '1.00', '  ')], 2)), /text is empty/);
  v = C.validate([cue('0.00', '0.20')], 2);
  assert.equal(v.errors, 0);
  assert.match(messages(v), /Very short/);
  assert.match(messages(C.validate([cue('0.00', '1.00')], null)), /not checked against audio/);

  const split = C.splitCue([cue('0.00', '2.00', 'one two three four')], 0, 0.5);
  same(split, [cue('0.00', '0.50', 'one'), cue('0.50', '2.00', 'two three four')]);
  same(C.splitCue([cue('0.00', '2.00', 'one two')], 0, NaN)[0], cue('0.00', '1.00', 'one'));
  assert.throws(() => C.splitCue([cue('0.00', '2.00', 'ఒకటి')], 0, 1), /two words/);
  same(C.mergeCue(split, 0), [cue('0.00', '2.00', 'one two three four')]);
  assert.throws(() => C.mergeCue(split, 1), /no following caption/);

  // Editor ------------------------------------------------------------------------
  const timing = {scenes: [
    {scene_id: 'a', status: 'measured', source: 'Prepared narration (job 12345678)', duration: 2.0,
     audio_url: '/api/jobs/j1/audio/a', automatic: {
       roman: {cues: [{start: 0, end: 0.95, text: 'Idi oka'}, {start: 0.95, end: 1.95, text: 'chinna pariksha'}],
               alignment: 'voice engine word timings, matched in word order; review required'},
       telugu: {cues: [{start: 0, end: 1.95, text: 'ఇది ఒక చిన్న పరీక్ష'}], alignment: 'voice engine word timings, matched in word order; review required'}}},
    {scene_id: 'b', status: 'unmeasured', message: 'Narration has not been prepared for the current text and voice.'}
  ]};
  const calls = [];
  const request = async (url, method, body) => {
    if (typeof method === 'object') [body, method] = [method, body];
    calls.push({url, method, body});
    if (url === '/projects/fixture/caption-timing') return JSON.parse(JSON.stringify(timing));
    return [];
  };
  w.StudioApi = {request, req: request};
  w.Projects.triggerAutosave = () => {};
  w.Projects.checkPreflight = () => {};
  let confirmAnswer = true;
  w.confirm = () => confirmAnswer;
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); C.init();

  const p = w.StudioState.createEmptyProject();
  p.scenes = [
    {...w.StudioState.createEmptyScene(), id: 'a', narration: 'ఇది ఒక చిన్న పరీక్ష', roman: 'Idi oka chinna pariksha'},
    {...w.StudioState.createEmptyScene(), id: 'b', narration: 'రెండవ', roman: 'Rendava',
     cues: [{start: 0, end: 1, text: 'Rendava'}]}
  ];
  let row = {id: 'fixture', revision: 1, approved: true, project: p};
  w.Projects.sync(row);
  C.onTabChange('captions');
  await tick();
  assert.ok(calls.some(c => c.url === '/projects/fixture/caption-timing' && c.body.project === p));

  const panel = sid => $(`.caption-scene[data-id="${sid}"]`);
  const input = (sid, i, field) => panel(sid).find('.cuerow').eq(i).find(`.cue-input[data-field="${field}"]`);
  const type = (sid, i, field, value) => input(sid, i, field).val(value).trigger('input');
  assert.equal(panel('a').find('.cuerow').length, 2);
  assert.equal(input('a', 1, 'text').val(), 'chinna pariksha');
  assert.match(panel('a').text(), /measured 2.00 s/);
  assert.equal(panel('a').find('audio.caption-audio').attr('src'), '/api/jobs/j1/audio/a');
  assert.equal(C.hasUnsaved(), false);
  assert.equal($('#btn-save-captions').prop('disabled'), true);
  // Unmeasured scenes keep text/time editing but cannot seek or reset.
  assert.equal(panel('b').find('.btn-cue-play').prop('disabled'), true);
  assert.equal(panel('b').find('.btn-caption-reset').prop('disabled'), true);
  assert.match(panel('b').find('.caption-scene-issues').text(), /not checked against audio/);
  assert.equal(panel('b').find('.caption-scene-status').text(), 'Not measured');

  // Typing keeps focus (no re-render) and marks the scene unsaved.
  const textInput = input('a', 0, 'text')[0];
  type('a', 0, 'text', 'ఇది ఒక');
  assert.equal(input('a', 0, 'text')[0], textInput);
  assert.equal(input('a', 0, 'text').attr('lang'), 'te');
  assert.equal(C.hasUnsaved(), true);
  assert.equal(panel('a').find('.caption-scene-status').text(), 'Unsaved changes');
  assert.equal($('#btn-save-captions').prop('disabled'), false);
  assert.match($('#captions-unsaved-badge').text(), /1 scene/);

  // Unsaved captions block silent loss: page unload, project switch, approval and rendering.
  const unload = new w.Event('beforeunload', {cancelable: true});
  w.dispatchEvent(unload);
  assert.equal(unload.defaultPrevented, true);
  confirmAnswer = false;
  const before = calls.length;
  w.Projects.switchProject('other');
  await w.Projects.approveRevision();
  $('#btn-render-preview').trigger('click');
  await tick();
  assert.equal(calls.length, before);
  assert.equal(w.StudioState.current.id, 'fixture');
  confirmAnswer = true;

  // Invalid and overlapping times are flagged and cannot be saved.
  type('a', 1, 'start', 'abc');
  assert.ok(panel('a').find('.cuerow').eq(1).hasClass('invalid'));
  assert.match(panel('a').find('.cuerow').eq(1).find('.cue-issues').text(), /not a valid time/);
  assert.equal(panel('a').find('.caption-scene-status').text(), '1 problem(s)');
  type('a', 1, 'start', '0.80');
  assert.match(panel('a').find('.cuerow').eq(1).find('.cue-issues').text(), /Overlaps caption 1 by 0.15 s/);
  let saved = null;
  w.Projects.saveProject = w.StudioProjects.save = async () => {
    saved = JSON.parse(JSON.stringify(w.StudioState.current.project));
    row = {...row, revision: row.revision + 1, approved: false, project: saved};
    w.Projects.sync(row);
    return row;
  };
  await C.saveAll();
  assert.equal(saved, null);
  assert.match($('#alert-notice-text').text(), /Scene 1: fix 1 caption problem/);
  same(p.scenes[0].cues, []);
  type('a', 1, 'start', '0:00.95');
  assert.equal(panel('a').find('.cuerow.invalid').length, 0);

  // Seek-based editing: play a caption, then set start/end from the playhead.
  const audio = panel('a').find('audio.caption-audio')[0];
  let played = 0;
  Object.defineProperty(audio, 'currentTime', {value: 0, writable: true});
  audio.play = () => { played++; return Promise.resolve(); };
  audio.pause = () => {};
  panel('a').find('.cuerow').eq(1).find('.btn-cue-play').trigger('click');
  assert.equal(audio.currentTime, 0.95);
  assert.equal(played, 1);
  audio.currentTime = 1.2;
  $(audio).trigger('timeupdate');
  assert.equal(panel('a').find('.caption-playhead').text(), '1.20');
  assert.ok(panel('a').find('.cuerow').eq(1).hasClass('playing'));
  audio.currentTime = 1.9;
  panel('a').find('.cuerow').eq(1).find('.btn-cue-set[data-field="end"]').trigger('click');
  assert.equal(input('a', 1, 'end').val(), '1.90');

  // Split at the playhead, merge back, delete; audio element survives re-renders.
  audio.currentTime = 1.4;
  panel('a').find('.cuerow').eq(1).find('.btn-cue-split').trigger('click');
  assert.equal(panel('a').find('.cuerow').length, 3);
  assert.equal(input('a', 1, 'end').val(), '1.40');
  assert.equal(input('a', 1, 'text').val(), 'chinna');
  assert.equal(input('a', 2, 'text').val(), 'pariksha');
  assert.equal(panel('a').find('audio.caption-audio')[0], audio);
  panel('a').find('.cuerow').eq(1).find('.btn-cue-merge').trigger('click');
  assert.equal(panel('a').find('.cuerow').length, 2);
  assert.equal(input('a', 1, 'text').val(), 'chinna pariksha');
  assert.equal(panel('a').find('.cuerow').last().find('.btn-cue-merge').prop('disabled'), true);
  panel('a').find('.cuerow').eq(1).find('.btn-cue-delete').trigger('click');
  assert.equal(panel('a').find('.cuerow').length, 1);

  // Reset to automatic timing from the Telugu narration, then from Roman (unchanged = clean).
  panel('a').find('.caption-reset-source').val('telugu');
  panel('a').find('.btn-caption-reset').trigger('click');
  assert.equal(input('a', 0, 'text').val(), 'ఇది ఒక చిన్న పరీక్ష');
  assert.equal(panel('a').find('.caption-reset-source').val(), 'telugu');
  panel('a').find('.caption-reset-source').val('roman');
  panel('a').find('.btn-caption-reset').trigger('click');
  assert.equal(C.hasUnsaved(), false);

  // Discard restores the saved manual captions.
  type('b', 0, 'text', 'Changed');
  panel('b').find('.btn-caption-discard').trigger('click');
  assert.equal(input('b', 0, 'text').val(), 'Rendava');
  assert.equal(C.hasUnsaved(), false);

  // Save: Telugu captions on scene a, edited Roman text on scene b, in one revision.
  panel('a').find('.caption-reset-source').val('telugu');
  panel('a').find('.btn-caption-reset').trigger('click');
  type('a', 0, 'end', '1.96');
  type('b', 0, 'text', '  Rendava katha  ');
  type('b', 0, 'end', '1.234');
  await C.saveAll();
  same(saved.scenes[0].cues, [{start: 0, end: 1.96, text: 'ఇది ఒక చిన్న పరీక్ష'}]);
  same(saved.scenes[1].cues, [{start: 0, end: 1.234, text: 'Rendava katha'}]);
  assert.equal(C.hasUnsaved(), false);
  assert.match($('#alert-notice-text').text(), /saved in revision v2/);
  assert.equal(input('a', 0, 'end').val(), '1.96');

  // Unedited automatic Roman timing is saved as automatic (empty manual cues).
  w.StudioState.current.project.scenes[0].cues = [];
  w.Projects.sync({...row, project: w.StudioState.current.project});
  type('a', 0, 'text', 'Idi oka!');
  type('a', 0, 'text', 'Idi oka');
  assert.equal(C.hasUnsaved(), false);

  // Changing scene text clears timings but keeps them restorable here.
  const project = w.StudioState.current.project;
  w.Scenes.renderScriptEditor();
  $('.scriptrow[data-id="b"] .scene-roman-input').val('Rendava katha marindi').trigger('input');
  same(project.scenes[1].cues, []);
  assert.match($('#alert-notice-text').text(), /restore them on the Captions tab/);
  C.render();
  assert.equal(panel('b').find('.cuerow').length, 0);
  panel('b').find('.btn-caption-restore').trigger('click');
  assert.equal(input('b', 0, 'text').val(), 'Rendava katha');
  assert.equal(C.hasUnsaved(), true);
  assert.equal(panel('b').find('.btn-caption-restore').length, 0);

  // A failed save keeps drafts and leaves the project's captions untouched.
  w.Projects.saveProject = w.StudioProjects.save = async () => { throw new Error('Network down'); };
  await C.saveAll();
  assert.equal(C.hasUnsaved(), true);
  same(project.scenes[1].cues, []);

  // Switching project after confirming discards drafts for the old project.
  w.Projects.sync({id: 'next', revision: 1, approved: false, project: w.StudioState.createEmptyProject()});
  assert.equal(C.hasUnsaved(), false);
  dom.window.close();
  console.log('Caption editor DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
