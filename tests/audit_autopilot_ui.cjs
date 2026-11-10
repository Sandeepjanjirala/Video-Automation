// One-click draft on the rendered Django page: real jQuery modules, mocked HTTP and jobs.
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
  const A = w.StudioAutopilot;
  const plain = v => JSON.parse(JSON.stringify(v));

  // Local splitting removes chat formatting exactly like providers.clean_caption.
  const clean = w.Scenes.cleanCaption;
  assert.equal(clean('Title: 3 Mind-Blowing Random Facts!'), '3 Mind-Blowing Random Facts!');
  assert.equal(clean('**Fact Number 1:** Octopus ki moodu hearts untayi!'), 'Fact Number 1: Octopus ki moodu hearts untayi!');
  assert.equal(clean('## Scene 2: Meeku teliyani'), 'Meeku teliyani');
  assert.equal(clean('Comment cheyyandi! #facts'), 'Comment cheyyandi! #facts');
  assert.equal(clean('Hook – Meeku telusa?'), 'Meeku telusa?');
  assert.equal(clean('Titles are fun'), 'Titles are fun');

  // A tiny fake server: projects, jobs that succeed (or fail on demand), apply and approve.
  let row, jobs, calls, failKind;
  const server = {
    project: () => plain(row),
    job: (kind, body) => {
      const job = {id: 'job' + (jobs.length + 1), kind, state: kind === failKind ? 'failed' : 'succeeded', revision: row.revision,
                   progress: kind === failKind ? 'Narration generation failed (network)' : 'Complete', options: body, result: {duration: 9.5}};
      jobs.unshift(job);
      return job;
    },
  };
  const request = async (url, method, body) => {
    if (typeof method === 'object') [body, method] = [method, body];
    calls.push({url, method: method || 'GET', body: body && plain(body)});
    if (url === '/projects/p1' && method === 'PUT') { row = {...row, revision: row.revision + 1, approved: false, project: plain(body.project)}; return server.project(); }
    if (url === '/projects/p1/jobs' && method === 'POST') {
      assert.equal(body.revision, row.revision, 'jobs are queued for the saved revision');
      if (['voice', 'render'].includes(body.kind)) assert.equal(row.approved, true, `${body.kind} needs approval`);
      return server.job(body.kind, body);
    }
    if (url === '/projects/p1/jobs') return plain(jobs);
    if (url.startsWith('/projects/p1/apply-job/')) {
      const job = jobs.find(j => url.endsWith(j.id));
      const p = plain(row.project);
      if (job.kind === 'draft') p.scenes.forEach((s, i) => Object.assign(s, {narration: 'దృశ్యం', keywords: i === 2 ? 'nothing matches' : 'octopus', graphic_title: 'Heading ' + i}));
      if (job.kind === 'stock_all') p.scenes.forEach((s, i) => { if (i !== 2) Object.assign(s, {visual: 'stock', video_asset: String(i).repeat(32).slice(0, 32), rights_reviewed: false}); });
      row = {...row, revision: row.revision + 1, approved: false, project: p};
      return {project: server.project(), conflicts: []};
    }
    if (url === '/projects/p1/approve') { row = {...row, approved: true}; return server.project(); }
    if (url === '/projects/p1/preflight') return {ready: true, errors: [], warnings: []};
    if (url.startsWith('/assets/')) return {width: 1920, height: 1080, duration: 5, rotation: 0};
    return [];
  };
  w.StudioApi = {request, req: request, blob: async () => ({blob: new w.Blob(['x']), headers: {get: () => null}})};
  w.URL.createObjectURL = () => 'blob:x';
  w.Projects.triggerAutosave = () => {};
  w.Projects.checkPreflight = () => {};
  let confirms = 0, prompts = [];
  w.confirm = () => { confirms += 1; return true; };
  w.prompt = (message, value) => { prompts.push(value); return value; };
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); w.StudioFraming.init(); w.StudioCaptionStyle.init(); A.init();
  A.setPollDelay(0);
  const tabs = [];
  w.StudioApp = {switchTab: tab => tabs.push(tab)};

  function load(scenes) {
    const p = w.StudioState.createEmptyProject();
    p.scenes = scenes.map((s, i) => ({...w.StudioState.createEmptyScene(), id: 's' + i, ...s}));
    row = {id: 'p1', revision: 1, approved: false, project: p};
    jobs = []; calls = []; failKind = null;
    w.Projects.sync(plain(row));
  }
  const queued = () => calls.filter(c => c.url === '/projects/p1/jobs' && c.method === 'POST').map(c => c.body);
  const states = () => $('#autopilot-steps li').map((i, el) => el.getAttribute('data-state')).get();

  // Existing scenes (already split, like a pasted ChatGPT script): fill mode, then everything to a draft.
  load([{roman: 'Title: 3 Facts'}, {roman: 'Octopus ki moodu hearts'}, {roman: 'Comment cheyyandi'}]);
  assert.equal($('#btn-autopilot').length, 1);
  await A.start();
  assert.equal(confirms, 1, 'one confirmation for the whole run');
  assert.deepEqual(plain(prompts), ['Original script by me.'], 'the review note is asked for up front, prefilled');
  assert.equal(row.project.evidence_notes, 'Original script by me.');
  assert.equal($('#textarea-evidence-notes').val(), 'Original script by me.');
  assert.deepEqual(plain(queued().map(b => b.kind)), ['draft', 'stock_all', 'voice', 'render']);
  assert.equal(queued()[0].fill, true);
  assert.equal(queued()[0].script, undefined);
  assert.equal(queued()[3].quality, 'draft');
  const scenes = row.project.scenes;
  assert.ok(scenes.every(s => s.rights_reviewed), 'rights ticked automatically');
  assert.equal(scenes[2].visual, 'graphic', 'a scene with no matching clip becomes a title card');
  assert.equal(scenes[2].graphic_title, 'Heading 2');
  assert.equal(scenes[0].visual, 'stock');
  assert.ok(calls.some(c => c.url === '/projects/p1/approve'));
  assert.deepEqual(plain(states()), ['done', 'done', 'done', 'done', 'done']);
  assert.match($('#autopilot-steps li[data-step="footage"] .autopilot-detail').text(), /1 scene\(s\) without a matching clip use a title card/);
  assert.match($('#autopilot-status').text(), /draft is ready/);
  assert.deepEqual(plain(tabs), ['export']);
  assert.equal(A.isRunning(), false);
  assert.equal($('#btn-autopilot').prop('disabled'), false);

  // A new project with only a pasted script: split by the writer's own lines (formatting removed),
  // then the AI only completes what is missing. A saved review note means no prompt.
  load([{}]);
  w.StudioState.current.project.evidence_notes = 'Checked on Wikipedia';
  prompts = [];
  $('#input-import-script').val('Title: 3 Facts\n**Fact 1:** Octopus ki moodu hearts untayi! Meeku telusa?');
  await A.start();
  assert.equal(queued()[0].kind, 'draft');
  assert.equal(queued()[0].fill, true);
  assert.equal(queued()[0].script, undefined);
  assert.deepEqual(plain(row.project.scenes.map(s => s.roman)), ['3 Facts', 'Fact 1: Octopus ki moodu hearts untayi!', 'Meeku telusa?']);
  assert.equal(prompts.length, 0);
  assert.equal(row.project.evidence_notes, 'Checked on Wikipedia');

  // Nothing to work with: stops before any job, with a clear message.
  load([{}]);
  $('#input-import-script').val('');
  await A.start();
  assert.equal(queued().length, 0);
  assert.match($('#autopilot-status').text(), /Paste your script/);
  assert.deepEqual(plain(states()), ['failed', 'waiting', 'waiting', 'waiting', 'waiting']);

  // A failing step stops the run with the job's own message; later steps are not started.
  load([{roman: 'Octopus ki moodu hearts'}]);
  failKind = 'voice';
  await A.start();
  assert.deepEqual(plain(queued().map(b => b.kind)), ['draft', 'stock_all', 'voice']);
  assert.deepEqual(plain(states()), ['done', 'done', 'done', 'failed', 'waiting']);
  assert.match($('#autopilot-status').text(), /Stopped: Narration generation failed \(network\)/);

  // Cancelling the review-note prompt starts nothing.
  load([{roman: 'Octopus'}]);
  w.prompt = () => null;
  confirms = 0;
  await A.start();
  assert.equal(calls.length, 0);
  assert.equal(confirms, 0);

  // Declining the confirmation does nothing.
  load([{roman: 'Octopus'}]);
  w.confirm = () => false;
  await A.start();
  assert.equal(calls.length, 0);

  dom.window.close();
  console.log('One-click draft DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
