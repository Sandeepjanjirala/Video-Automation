// Deleting projects on the rendered Django page: real jQuery modules, mocked HTTP.
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
  const P = w.Projects;
  const plain = v => JSON.parse(JSON.stringify(v));
  const flush = () => new Promise(resolve => setTimeout(resolve, 0));

  // A fake server holding projects; DELETE can be refused like a project with a running job.
  let rows, calls, refuse;
  const row = (id, title) => {
    const p = w.StudioState.createEmptyProject();
    p.title = title;
    p.scenes = [{...w.StudioState.createEmptyScene(), id: 's1', roman: 'Katha'}];
    return {id, revision: 1, approved: false, project: p};
  };
  const request = async (url, method, body) => {
    if (typeof method === 'object') [body, method] = [method, body];
    calls.push({url, method: method || 'GET'});
    const id = (url.match(/^\/projects\/([^/]+)/) || [])[1];
    if (method === 'DELETE' && url === '/projects/' + id) {
      if (refuse) throw new Error('This project has a job in progress.');
      const gone = rows.find(r => r.id === id);
      rows = rows.filter(r => r.id !== id);
      return {deleted: id, title: gone.project.title, jobs: 0};
    }
    if (url === '/projects/' + id) return plain(rows.find(r => r.id === id));
    if (url.endsWith('/preflight')) return {ready: false, errors: [], warnings: []};
    if (url.endsWith('/draft')) return null;
    return [];
  };
  w.StudioApi = {request, req: request, blob: async () => ({blob: new w.Blob(['x']), headers: {get: () => null}})};
  let confirms = [], answer = true;
  w.confirm = message => { confirms.push(message); return answer; };
  P.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); w.StudioFraming.init(); w.StudioCaptionStyle.init(); w.StudioAutopilot.init();

  function start() {
    rows = [row('p1', 'Alpha'), row('p2', 'Beta <b>'), row('p3', 'Gamma')];
    calls = []; confirms = []; answer = true; refuse = false;
    w.StudioState.setProjects(plain(rows));
    P.sync(plain(rows[0]));
  }
  const listed = () => $('#projects-nav .project-item, #project-list-nav .project-item').map((i, el) => $(el).data('id')).get();

  // Every sidebar project has its own bin; the header has one for the open project.
  start();
  assert.deepEqual(plain(listed()), ['p1', 'p2', 'p3']);
  assert.equal($('.project-delete').length, 3);
  assert.equal($('.project-delete[data-id="p2"]').attr('aria-label'), 'Delete Beta <b>');
  assert.equal($('#btn-delete-project').length, 1);

  // The sidebar bin deletes that project only, after one confirmation naming it; the open project stays.
  $('.project-delete[data-id="p2"]').trigger('click');
  for (let i = 0; i < 5; i++) await flush();
  assert.equal(confirms.length, 1);
  assert.match(confirms[0], /^Delete "Beta <b>" permanently\?/);
  assert.match(confirms[0], /cannot be undone\. Clips in the media library are kept\./);
  const deletes = () => calls.filter(c => c.method === 'DELETE').map(c => c.url);
  assert.deepEqual(plain(deletes()), ['/projects/p2']);
  assert.ok(!calls.some(c => c.url === '/projects/p2' && c.method === 'GET'), 'the bin does not open the project');
  assert.deepEqual(plain(listed()), ['p1', 'p3']);
  assert.equal(w.StudioState.current.id, 'p1');
  assert.equal($('#alert-notice-text').text(), 'Deleted "Beta <b>".');

  // Deleting the open project opens the next one.
  calls = [];
  assert.equal(await P.deleteProject('p1'), true);
  assert.deepEqual(plain(deletes()), ['/projects/p1']);
  assert.ok(calls.some(c => c.method === 'GET' && c.url === '/projects/p3'));
  assert.equal(w.StudioState.current.id, 'p3');
  assert.deepEqual(plain(listed()), ['p3']);
  assert.equal(w.StudioState.isBusy, false);

  // Declining the confirmation, or a refusal from the server, keeps the project.
  calls = []; answer = false;
  $('#btn-delete-project').trigger('click');
  await flush();
  assert.equal(deletes().length, 0);
  answer = true; refuse = true;
  assert.equal(await P.deleteProject('p3'), false);
  assert.match($('#alert-notice-text').text(), /job in progress/);
  assert.deepEqual(plain(listed()), ['p3']);
  assert.equal(w.StudioState.current.id, 'p3');

  // Deleting the last project shows the welcome screen.
  refuse = false;
  assert.equal(await P.deleteProject('p3'), true);
  assert.equal(w.StudioState.current, null);
  assert.deepEqual(plain(listed()), []);
  assert.equal($('#welcome-panel').css('display'), 'block');
  assert.equal($('#project-editor-workspace').css('display'), 'none');

  // While the one-click draft runs for the open project, deleting it is refused before asking.
  start();
  w.StudioAutopilot.isRunning = () => true;
  confirms = [];
  assert.equal(await P.deleteProject('p1'), false);
  assert.equal(confirms.length, 0);
  assert.match($('#alert-notice-text').text(), /Wait for "Create draft video automatically"/);

  // Phones: the project list opens as a drawer from the top bar and closes after choosing a project.
  start();
  const open = () => $('body').hasClass('drawer-open');
  $('#btn-mobile-projects').trigger('click');
  assert.equal(open(), true);
  assert.equal($('#btn-mobile-projects').attr('aria-expanded'), 'true');
  $('#projects-nav .project-item, #project-list-nav .project-item').eq(1).trigger('click');
  assert.equal(open(), false);
  $('#btn-mobile-projects').trigger('click');
  $(w.document).trigger($.Event('keydown', {key: 'Escape'}));
  assert.equal(open(), false);
  $('#btn-mobile-projects').trigger('click');
  $('main').trigger('click');                                       // tapping outside closes it
  assert.equal(open(), false);
  $('#btn-mobile-projects').trigger('click');
  $('#btn-open-settings').trigger('click');
  assert.equal(open(), false);

  dom.window.close();
  console.log('Project delete DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
