// Caption style picker on the rendered Django page: real jQuery modules, mocked HTTP.
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
  const S = w.StudioCaptionStyle;
  const tick = () => new Promise(resolve => setImmediate(resolve));

  const video = 'b'.repeat(32);
  const request = async (url) => {
    if (url === `/assets/${video}/framing`) return {id: video, kind: 'video', width: 1920, height: 1080, rotation: 0, duration: 10};
    return [];
  };
  const blobCalls = [];
  w.StudioApi = {request, req: request, blob: async (url, body) => {
    blobCalls.push({url, body: JSON.parse(JSON.stringify(body))});
    return {blob: new w.Blob(['jpeg'], {type: 'image/jpeg'}), headers: {get: () => null}};
  }};
  let blobId = 0;
  w.URL.createObjectURL = () => 'blob:frame-' + (++blobId);
  w.URL.revokeObjectURL = () => {};
  w.Projects.triggerAutosave = () => {};
  w.Projects.checkPreflight = () => {};
  w.confirm = () => true;
  w.Projects.init(); w.Scenes.init(); w.StudioJobs.init(); w.StudioCaptions.init(); w.StudioFraming.init(); S.init();

  assert.equal(w.StudioState.createEmptyProject().caption_style, 'classic');
  // A project saved before Stage 4 has no caption_style: it shows and stays classic.
  const p = w.StudioState.createEmptyProject();
  delete p.caption_style;
  p.caption_size = 64;
  p.caption_bottom = 420;
  p.scenes = [
    {...w.StudioState.createEmptyScene(), id: 'v', roman: 'Idi oka chinna pariksha', visual: 'upload', video_asset: video,
     trim_start: 1.5, cues: [{start: 0, end: 1, text: 'నమస్కారం, శ్రీ క్షేత్రం'}]},
    {...w.StudioState.createEmptyScene(), id: 'g', roman: 'Title words here', visual: 'graphic', graphic_title: 'Heading'},
  ];
  w.Projects.sync({id: 'fixture', revision: 1, approved: true, project: p});
  await tick(); await tick();
  assert.equal(w.StudioState.getDirty(), false);
  assert.equal($('#caption-style-options .stylecard').length, 4);
  assert.deepEqual(JSON.parse(JSON.stringify($('#caption-style-options input').map((i, el) => el.value).get())),
                   ['classic', 'black_box', 'yellow_accent', 'translucent']);
  assert.equal($('#caption-style-options input:checked').val(), 'classic');
  assert.equal(p.caption_style, undefined);
  assert.equal($('#style-caption-size').val(), '64');
  assert.equal($('#style-caption-bottom').val(), '420');
  assert.equal($('#caption-style-scene option').length, 2);
  assert.match($('#caption-style-scene option').eq(1).text(), /title card/);
  assert.equal($('#caption-style-text').val(), 'నమస్కారం, శ్రీ క్షేత్రం');

  // Opening the tab renders the caption-free background for the chosen scene.
  w.StudioCaptions.onTabChange('captions');
  S.onTabChange('captions');
  await tick(); await tick();
  const bg = blobCalls.find(c => c.url === '/projects/fixture/caption-preview' && c.body.caption === '');
  assert.ok(bg, 'background requested');
  assert.equal(bg.body.scene.id, 'v');
  assert.equal(bg.body.source_time, 1.5);
  assert.match($('#caption-style-bg').attr('src'), /^blob:/);

  const stageCap = () => $('#caption-style-stage .framecaption');
  const sceneCap = () => $('.scene-visual-card[data-id="v"] .framecaption');
  assert.ok(stageCap().hasClass('capstyle-classic'));
  assert.equal(stageCap().attr('lang'), 'te');
  assert.equal(stageCap().text(), 'నమస్కారం, శ్రీ క్షేత్రం');
  assert.equal(parseFloat(stageCap()[0].style.bottom), 420 / 1920 * 100);
  assert.ok(sceneCap().hasClass('capstyle-classic'));

  // Choosing a style updates every live preview and marks the project unsaved; text and timings stay.
  const cues = JSON.stringify(p.scenes[0].cues);
  $('#caption-style-options input[value="black_box"]').prop('checked', true).trigger('change');
  assert.equal(p.caption_style, 'black_box');
  assert.equal(w.StudioState.getDirty(), true);
  assert.ok($('.stylecard[data-style="black_box"]').hasClass('selected'));
  // Swatches keep their own style whatever is selected.
  for (const id of ['classic', 'black_box', 'yellow_accent', 'translucent']) {
    assert.ok($(`.stylecard[data-style="${id}"] .framecaption`).hasClass('capstyle-' + id), id);
  }
  assert.ok(stageCap().hasClass('capstyle-black_box'));
  assert.ok(sceneCap().hasClass('capstyle-black_box'));
  assert.equal(stageCap().find('.capline').length, 1);
  assert.equal(JSON.stringify(p.scenes[0].cues), cues);
  assert.equal(p.caption_size, 64);
  $('#caption-style-text').val('one two three four five six seven eight nine ten').trigger('input');
  assert.equal(stageCap().find('.capline').length, 2);                 // one box per wrapped line
  $('#caption-style-options input[value="translucent"]').prop('checked', true).trigger('change');
  assert.equal(stageCap().find('.capblock').length, 1);                // one panel for the caption
  assert.equal(stageCap().find('.capblock .capline').length, 2);

  // Size and margin are the same project fields as on the Export tab, kept in sync both ways.
  $('#style-caption-size').val(72).trigger('input');
  assert.equal(p.caption_size, 72);
  assert.equal($('#range-caption-size').val(), '72');
  $('#range-caption-bottom').val(500).trigger('input');
  assert.equal(p.caption_bottom, 500);
  assert.equal($('#style-caption-bottom').val(), '500');
  assert.equal(parseFloat(stageCap()[0].style.bottom), 500 / 1920 * 100);

  // Exact caption: the style, size, margin, text and scene go to FFmpeg; the result replaces the live view.
  await S.renderExact();
  const exact = blobCalls.at(-1);
  assert.equal(exact.body.caption_style, 'translucent');
  assert.equal(exact.body.caption_size, 72);
  assert.equal(exact.body.caption_bottom, 500);
  assert.equal(exact.body.caption, 'one two three four five six seven eight nine ten');
  assert.equal($('#caption-style-exact').prop('hidden'), false);
  assert.match($('#caption-style-status').text(), /translucent background caption/);
  $('#caption-style-options input[value="yellow_accent"]').prop('checked', true).trigger('change');
  assert.equal($('#caption-style-exact').prop('hidden'), true);        // a stale exact frame is hidden

  // Title-card scenes can be previewed; the scene's framing exact frame also carries the style.
  $('#caption-style-scene').val('g').trigger('change');
  await tick(); await tick();
  assert.equal(blobCalls.at(-1).body.scene.id, 'g');
  assert.equal($('#caption-style-text').val(), 'Title words here');
  await w.StudioFraming.renderExact('v');
  assert.equal(blobCalls.at(-1).url, '/projects/fixture/framing-preview');
  assert.equal(blobCalls.at(-1).body.caption_style, 'yellow_accent');

  // Switching to another project resets the picker to that project's style.
  const other = w.StudioState.createEmptyProject();
  other.caption_style = 'black_box';
  w.Projects.sync({id: 'next', revision: 1, approved: false, project: other});
  assert.equal($('#caption-style-options input:checked').val(), 'black_box');
  dom.window.close();
  console.log('Caption style DOM regression passed');
})().catch(e => {console.error(e); process.exit(1);});
