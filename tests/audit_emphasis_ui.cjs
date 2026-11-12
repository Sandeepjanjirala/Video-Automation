// Automatic Telugu after "Split into scenes" and highlighted caption key words: real jQuery modules, mocked HTTP.
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
  const S = w.StudioState;
  const plain = v => JSON.parse(JSON.stringify(v));
  const wait = async test => { for (let i = 0; i < 200 && !test(); i++) await new Promise(r => setTimeout(r, 5)); assert.ok(test()); };

  // A fake server: saving, one fill job that writes Telugu and key words, applying it.
  let row, jobs, calls, failFill;
  const request = async (url, method, body) => {
    if (typeof method === 'object') [body, method] = [method, body];
    calls.push({url, method: method || 'GET', body: body && plain(body)});
    if (url === '/projects/p1' && method === 'PUT') { row = {...row, revision: row.revision + 1, project: plain(body.project)}; return plain(row); }
    if (url === '/projects/p1/jobs' && method === 'POST') {
      const job = {id: 'job' + (jobs.length + 1), kind: body.kind, state: failFill ? 'failed' : 'succeeded',
                   progress: failFill ? 'Gemini rate limit reached. Try again shortly.' : 'Complete', revision: row.revision};
      jobs.unshift(job);
      return job;
    }
    if (url === '/projects/p1/jobs') return plain(jobs);
    if (url.startsWith('/projects/p1/apply-job/')) {
      const p = plain(row.project);
      p.scenes.forEach((s, i) => {
        if (s.roman && !s.narration) s.narration = 'తెలుగు ' + s.roman;      // English letters -> Telugu narration
        if (s.narration && !s.roman) s.roman = 'Roman line ' + (i + 1);     // Telugu letters -> Roman captions
        Object.assign(s, {keywords: 'octopus', emphasis: [s.roman.split(' ')[0]]});
      });
      row = {...row, revision: row.revision + 1, project: p};
      return {project: plain(row), conflicts: []};
    }
    if (url.endsWith('/preflight')) return {ready: false, errors: [], warnings: []};
    if (url.startsWith('/assets/')) return {width: 1920, height: 1080, duration: 5, rotation: 0};
    return [];
  };
  w.StudioApi = {request, req: request, blob: async () => ({blob: new w.Blob(['x']), headers: {get: () => null}})};
  w.URL.createObjectURL = () => 'blob:x';
  w.confirm = () => true;
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); w.StudioFraming.init(); w.StudioCaptionStyle.init();
  w.StudioAutopilot.init();
  w.StudioAutopilot.setPollDelay(0);
  w.Projects.triggerAutosave = () => {};

  function load(changes) {
    const p = {...S.createEmptyProject(), ...changes};
    row = {id: 'p1', revision: 1, approved: false, project: p};
    jobs = []; calls = []; failFill = false;
    S.setProjects([plain(row)]);
    w.Projects.sync(plain(row));
  }
  const notice = () => $('#alert-notice-text').text();

  // New projects highlight key words; older projects (no field) keep plain captions.
  assert.equal(S.createEmptyProject().caption_emphasis, 'keywords');
  assert.deepEqual(plain(S.createEmptyScene().emphasis), []);

  // Split into scenes, then the Telugu narration is written automatically with one AI job.
  S.setCfg({online_enabled: true, gemini_key_configured: true});
  load({});
  $('#input-import-script').val('Octopus ki moodu hearts.\nHoney eppatiki paadavadu.');
  $('#btn-import-split').trigger('click');
  assert.match(notice(), /Writing the Telugu narration with AI/);
  await wait(() => /Telugu narration and Roman captions ready for 2 of 2 scene/.test(notice()));
  const fills = calls.filter(c => c.url === '/projects/p1/jobs' && c.method === 'POST');
  assert.deepEqual(plain(fills.map(c => [c.body.kind, c.body.fill])), [['draft', true]]);
  assert.deepEqual(plain(S.current.project.scenes.map(s => s.narration)), ['తెలుగు Octopus ki moodu hearts.', 'తెలుగు Honey eppatiki paadavadu.']);
  assert.deepEqual(plain(S.current.project.scenes.map(s => s.emphasis)), [['Octopus'], ['Honey']]);
  assert.equal($('#btn-import-split').prop('disabled'), false);

  // A failed AI job keeps the split scenes and says what to do next.
  load({});
  failFill = true;
  $('#input-import-script').val('Octopus ki moodu hearts.');
  $('#btn-import-split').trigger('click');
  await wait(() => /Telugu narration could not be written: Gemini rate limit reached/.test(notice()));
  assert.equal(S.current.project.scenes.length, 1);
  assert.equal(S.current.project.scenes[0].roman, 'Octopus ki moodu hearts.');

  // A script in Telugu letters with English words: each line is Telugu narration, the Roman captions box
  // (English letters only) is written automatically; English-letter lines still go to the Roman box.
  load({});
  $('#script-input-format').val('roman');
  $('#input-import-script').val('ఒక lake దగ్గరకు వెళ్లిన animals.\nTanzaniaలో Lake Natron అనే ఒక strange lake ఉంది.\nComment cheyyandi!');
  $('#btn-import-split').trigger('click');
  assert.deepEqual(plain(S.current.project.scenes.map(s => [s.narration, s.roman])),
    [['ఒక lake దగ్గరకు వెళ్లిన animals.', ''], ['Tanzaniaలో Lake Natron అనే ఒక strange lake ఉంది.', ''], ['', 'Comment cheyyandi!']]);
  assert.match(notice(), /Writing the Telugu narration and Roman captions with AI/);
  await wait(() => /ready for 3 of 3 scene/.test(notice()));
  assert.deepEqual(plain(S.current.project.scenes.map(s => [s.narration, s.roman])),
    [['ఒక lake దగ్గరకు వెళ్లిన animals.', 'Roman line 1'], ['Tanzaniaలో Lake Natron అనే ఒక strange lake ఉంది.', 'Roman line 2'],
     ['తెలుగు Comment cheyyandi!', 'Comment cheyyandi!']]);
  const saved = calls.filter(c => c.url === '/projects/p1' && c.method === 'PUT');
  assert.ok(saved.length && saved[0].body.project.scenes.every(s => !/[ఀ-౿]/.test(s.roman)), 'the Roman box is saved in English letters');

  // An autosaved draft from before this fix (Telugu in the Roman box) is repaired when saving.
  load({});
  S.current.project.scenes[0].roman = 'ఒక lake దగ్గరకు';
  await w.Projects.saveProject();
  assert.deepEqual(plain([S.current.project.scenes[0].narration, S.current.project.scenes[0].roman]), ['ఒక lake దగ్గరకు', '']);

  // Without Gemini the split still works and explains how to get Telugu.
  S.setCfg({online_enabled: false, gemini_key_configured: false});
  load({});
  $('#input-import-script').val('Octopus ki moodu hearts.');
  $('#btn-import-split').trigger('click');
  assert.match(notice(), /Script split into 1 scene\(s\)\. Turn on online services and add a Gemini key/);
  assert.equal(calls.filter(c => c.method === 'POST' && c.url === '/projects/p1/jobs').length, 0);

  // Highlighted key words: the switch, the per-scene words and the live caption preview.
  load({caption_emphasis: 'keywords'});
  const scene = S.current.project.scenes[0];
  scene.emphasis = ['Octopus', 'hearts'];
  w.StudioCaptionStyle.render();
  assert.equal($('#caption-emphasis-toggle').prop('checked'), true);
  const $cap = $('<div class="framecaption"></div>');
  w.StudioCaptionStyle.overlay($cap, 'Octopus ki moodu hearts 3 sarlu', S.current.project, scene);
  assert.deepEqual(plain($cap.find('.capem').map((i, el) => el.textContent).get()), ['Octopus', 'hearts', '3']);
  // Swatches show the highlight on "katha".
  assert.equal($('.stylesample .framecaption .capem').first().text(), 'katha');
  $('#caption-emphasis-toggle').prop('checked', false).trigger('change');
  assert.equal(S.current.project.caption_emphasis, 'none');
  w.StudioCaptionStyle.overlay($cap, 'Octopus ki moodu hearts 3 sarlu', S.current.project, scene);
  assert.equal($cap.find('.capem').length, 0);
  assert.equal($('.stylesample .framecaption .capem').length, 0);
  // Editing a scene's key words on the Captions tab.
  const $input = $(`<input class="scene-emphasis-input" data-scene-id="${scene.id}">`).appendTo('body');
  $input.val('Honey, jar  sweet').trigger('change');
  assert.deepEqual(plain(scene.emphasis), ['Honey', 'jar', 'sweet']);
  assert.equal(S.isDirty, true);

  dom.window.close();
  console.log('Automatic Telugu and caption highlight DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
