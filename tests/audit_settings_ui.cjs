// Settings: API keys from .env are defaults that can be replaced (and restored) on the page (real jQuery modules, mocked HTTP).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const { JSDOM } = await import('../frontend/node_modules/jsdom/lib/api.js');
  const dom = new JSDOM(fs.readFileSync(process.argv[2], 'utf8'), {runScripts: 'outside-only', url: 'http://localhost:8788/'});
  const w = dom.window;
  const root = path.join(__dirname, '..', 'studio', 'static');
  w.eval(fs.readFileSync(path.join(root, 'vendor/jquery.min.js'), 'utf8'));
  for (const name of ['state', 'ui', 'settings']) w.eval(fs.readFileSync(path.join(root, 'js', name + '.js'), 'utf8'));
  const $ = w.jQuery;
  const sent = [];
  w.StudioApi = {req: async (url, body, method) => { sent.push({url, body: JSON.parse(JSON.stringify(body)), method}); return w.StudioState.getCfg(); }};
  w.eval('StudioSettings.init()');
  const source = (key, value, envDefault) => ({[key + '_source']: value, [key + '_configured']: !!value, [key + '_env_default']: envDefault});
  w.StudioState.setCfg({online_enabled: true, gemini_model: 'gemini-2.5-flash', daily_ai_calls: 75,
                        ...source('gemini_key', 'env', true), ...source('pixabay_key', 'settings', true),
                        ...source('pexels_key', '', false), ...source('sarvam_key', 'settings', false), ...source('azure_key', '', false)});
  $('#btn-open-settings').first().trigger('click');

  // Every key field can be typed in; none says to edit the .env file.
  for (const id of ['gemini', 'pixabay', 'pexels', 'sarvam', 'azure']) {
    assert.equal($(`#setting-${id}-key`).prop('disabled'), false, id);
    assert.equal($(`#setting-${id}-key`).val(), '', `${id} never holds a key`);
  }
  assert.doesNotMatch($('#settings-modal').text(), /Set in the \.env file|change it there/);
  assert.match($('#setting-gemini-key').attr('placeholder'), /Default key from \.env in use/);
  assert.match($('#hint-gemini-key').text(), /Using the default key from your \.env file; a key typed here replaces it/);
  assert.match($('#setting-pixabay-key').attr('placeholder'), /saved here/);
  assert.match($('#hint-pixabay-key').text(), /instead of the \.env default/);
  assert.equal($('#setting-pexels-key').attr('placeholder'), 'Enter Pexels key');
  // "Use the default key from .env" appears only where a typed key replaces a .env default.
  assert.deepEqual(['gemini', 'pixabay', 'pexels', 'sarvam', 'azure'].map(id => $(`#row-${id}-key-default`).prop('hidden')),
                   [true, false, true, true, true]);

  // Saving: a typed key is sent; the default box sends the key back to the .env default.
  $('#setting-gemini-key').val('my-new-gemini-key');
  $('#setting-pixabay-key-default').prop('checked', true);
  await w.StudioSettings.saveSettings();
  const body = sent[sent.length - 1].body;
  assert.equal(body.gemini_key, 'my-new-gemini-key');
  assert.deepEqual(body.use_default_keys, ['pixabay_key']);
  assert.equal('pixabay_key' in body, false);

  dom.window.close();
  console.log('Settings key defaults DOM regression passed');
})().catch(e => { console.error(e); process.exit(1); });
