import React, {useEffect, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type Any = any;
let token = '';
async function api(path: string, body?: Any, method?: string) {
  const form = body instanceof FormData;
  const response = await fetch('/api' + path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    headers: {'x-studio-token': token, ...(body !== undefined && !form ? {'Content-Type': 'application/json'} : {})},
    body: body === undefined ? undefined : form ? body : JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail || result));
  return result;
}
const uid = () => crypto.randomUUID().replaceAll('-', '').slice(0, 12);
const scene = () => ({
  id: uid(), narration: '', spoken_text: '', roman: '', keywords: '', visual: 'upload', video_asset: '',
  audio_asset: '', audio_text_hash: '', trim_start: 0, fit: 'crop', crop_x: .5, hold_seconds: 0,
  graphic_title: '', rights_reviewed: false, narration_block_id: '', take_id: '', delivery_preset: 'conversational', cues: []
});
const empty = () => ({
  title: 'My next story', topic: '', category: 'Storytelling', target_seconds: 60,
  duration_mode: 'natural', tts_provider: 'edge', voice: 'te-IN-ShrutiNeural', speech_rate: 0,
  delivery_preset: 'conversational', pronunciation_dict: {}, voice_locked: false, voice_profile: {}, narration_blocks: [],
  scenes: [scene()], evidence_notes: '', research_report: '', research_sources: [], research_grounding: {},
  music_asset: '', music_gain: .12, music_rights_reviewed: false, caption_size: 56, caption_bottom: 300,
  script_approved_hash: '', draft_script: ''
});
async function hashText(text: string) {
  const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(text)));
  return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, '0')).join('');
}
function Field({label, children, hint}: Any) {
  return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
function Link({url, children}: Any) {
  return /^https?:\/\//.test(url || '') ? <a href={url} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>;
}

function App() {
  const [projects, setProjects] = useState<Any[]>([]);
  const [row, setRow] = useState<Any>(null);
  const [p, setP] = useState<Any>(null);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState('write');
  const [jobs, setJobs] = useState<Any[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState(false);
  const [cfg, setCfg] = useState<Any>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [health, setHealth] = useState<Any>(null);
  const [script, setScript] = useState('');
  const [stock, setStock] = useState<Any>({});
  const [cueEdit, setCueEdit] = useState<Any>({});
  const [history, setHistory] = useState<Any[] | null>(null);
  const [researchOpen, setResearchOpen] = useState(false);

  // Phase 1 UI state
  const [preflight, setPreflight] = useState<Any>(null);
  const [draftRecoverable, setDraftRecoverable] = useState<Any>(null);
  const [showMediaLibrary, setShowMediaLibrary] = useState(false);
  const [mediaAssets, setMediaAssets] = useState<Any[]>([]);
  const [libraryTargetScene, setLibraryTargetScene] = useState<string>('all');
  const [conflicts, setConflicts] = useState<Any[]>([]);
  const autosaveTimer = useRef<Any>(null);

  // Phase 4 Voice Lab UI state
  const [voiceCapabilities, setVoiceCapabilities] = useState<Any[]>([]);
  const [takeHistory, setTakeHistory] = useState<Any[]>([]);
  const [candidateTake, setCandidateTake] = useState<Any>(null);
  const [voiceLabSceneId, setVoiceLabSceneId] = useState<string>('');
  const [explicitRegenerate, setExplicitRegenerate] = useState(false);
  const [rewritePreview, setRewritePreview] = useState<Any>(null);
  const [newDictKey, setNewDictKey] = useState('');
  const [newDictVal, setNewDictVal] = useState('');
  const [normTestText, setNormTestText] = useState('');
  const [normTestResult, setNormTestResult] = useState<Any>(null);

  const tell = (s: string, bad = false) => { setMessage(s); setError(bad); };
  async function action(fn: () => Promise<any>) {
    setBusy(true);
    try { await fn(); } catch (e: any) { tell(e.message, true); } finally { setBusy(false); }
  }

  function load(r: Any) {
    setRow(r);
    setP(r.project);
    setScript(r.project.draft_script || '');
    setDirty(false);
    setJobs([]);
    setStock({});
    setCueEdit({});
    setHistory(null);
    setConflicts([]);
    setCandidateTake(null);
    setRewritePreview(null);
    if (r.project.scenes?.length) {
      setVoiceLabSceneId(r.project.scenes[0].id);
    }
    api('/projects/' + r.id + '/takes').then(t => setTakeHistory(t)).catch(() => {});
    // Check for autosaved server draft
    api('/projects/' + r.id + '/draft').then(d => {
      if (d && d.updated && (!r.updated || new Date(d.updated).getTime() > new Date(r.updated).getTime())) {
        setDraftRecoverable(d);
      } else {
        setDraftRecoverable(null);
      }
    }).catch(() => {});
  }

  function sync(r: Any) {
    setRow(r);
    setP(r.project);
    setScript(r.project.draft_script || '');
    setDirty(false);
    setDraftRecoverable(null);
    setProjects(old => [r, ...old.filter(x => x.id !== r.id)]);
  }

  useEffect(() => {
    api('/bootstrap').then(d => {
      token = d.token;
      setProjects(d.projects);
      setCfg(d.settings);
      if (d.projects.length) load(d.projects[0]);
    }).catch(e => tell(e.message, true));
    api('/voice/capabilities').then(caps => setVoiceCapabilities(caps)).catch(() => {});
  }, []);

  async function generateVoicePreview() {
    if (!row || !p) return;
    const currentScene = p.scenes.find((s: Any) => s.id === (voiceLabSceneId || p.scenes[0]?.id)) || p.scenes[0];
    if (!currentScene) return;
    const textToSynthesize = currentScene.spoken_text || currentScene.narration;
    if (!textToSynthesize.trim()) {
      tell('Enter Telugu narration before generating a voice preview.', true);
      return;
    }
    setBusy(true);
    try {
      const take = await api('/voice/preview', {
        project_id: row.id,
        scene_id: currentScene.id,
        text: textToSynthesize,
        provider: p.tts_provider || 'edge',
        voice: p.voice || 'te-IN-ShrutiNeural',
        preset: p.delivery_preset || 'conversational',
        rate: p.speech_rate || 0,
        pitch: 0,
        dictionary: p.pronunciation_dict || {},
        regenerate: explicitRegenerate
      });
      setCandidateTake(take);
      tell(`Candidate take generated (${take.duration.toFixed(1)}s, ${take.display_name}).`);
      const updatedTakes = await api('/projects/' + row.id + '/takes');
      setTakeHistory(updatedTakes);
    } catch (e: any) {
      tell(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function acceptTake(takeId: string) {
    if (!row) return;
    setBusy(true);
    try {
      const res = await api('/projects/' + row.id + '/takes/' + takeId + '/accept');
      sync(res.project);
      tell('Voice take accepted and applied to project (v' + res.project.revision + ').');
      const updatedTakes = await api('/projects/' + row.id + '/takes');
      setTakeHistory(updatedTakes);
    } catch (e: any) {
      tell(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function checkConversationalRewrite() {
    const currentScene = p.scenes.find((s: Any) => s.id === (voiceLabSceneId || p.scenes[0]?.id)) || p.scenes[0];
    if (!currentScene || !currentScene.narration.trim()) {
      tell('Scene has no Telugu narration to rewrite.', true);
      return;
    }
    setBusy(true);
    try {
      const res = await api('/voice/rewrite_preview', {text: currentScene.narration});
      setRewritePreview(res);
      if (!res.changed) {
        tell('Narration already has natural spoken cadence. No colloquial changes needed.');
      }
    } catch (e: any) {
      tell(e.message, true);
    } finally {
      setBusy(false);
    }
  }

  function applyRewrite() {
    if (!rewritePreview || !rewritePreview.proposed) return;
    const currentScene = p.scenes.find((s: Any) => s.id === (voiceLabSceneId || p.scenes[0]?.id)) || p.scenes[0];
    if (!currentScene) return;
    edit(currentScene.id, {narration: rewritePreview.proposed, spoken_text: rewritePreview.proposed});
    setRewritePreview(null);
    tell('Colloquial spoken phrasing approved and applied to scene narration.');
  }

  function addDictEntry() {
    if (!newDictKey.trim() || !newDictVal.trim()) return;
    const updatedDict = {...(p.pronunciation_dict || {}), [newDictKey.trim()]: newDictVal.trim()};
    change('pronunciation_dict', updatedDict);
    setNewDictKey('');
    setNewDictVal('');
    tell(`Pronunciation rule added for "${newDictKey.trim()}".`);
  }

  function removeDictEntry(k: string) {
    const updatedDict = {...(p.pronunciation_dict || {})};
    delete updatedDict[k];
    change('pronunciation_dict', updatedDict);
    tell(`Pronunciation rule "${k}" removed.`);
  }

  async function testNormalize() {
    if (!normTestText.trim()) return;
    try {
      const res = await api('/voice/normalize', {text: normTestText, dictionary: p.pronunciation_dict});
      setNormTestResult(res);
    } catch (e: any) {
      tell(e.message, true);
    }
  }

  // Poll jobs
  useEffect(() => {
    if (!row) return;
    let active = true;
    const poll = () => api('/projects/' + row.id + '/jobs').then(j => { if (active) setJobs(j); }).catch(() => {});
    poll();
    const timer = setInterval(poll, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [row?.id]);

  // Preflight check
  useEffect(() => {
    if (!row || !p) return;
    api('/projects/' + row.id + '/preflight').then(pf => setPreflight(pf)).catch(() => {});
  }, [row?.id, row?.revision, dirty]);

  // Recoverable autosave debounce
  useEffect(() => {
    if (!row || !p || !dirty) return;
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      api('/projects/' + row.id + '/draft', p, 'PUT').catch(() => {});
    }, 1500);
    return () => { if (autosaveTimer.current) clearTimeout(autosaveTimer.current); };
  }, [p, dirty]);

  function change(key: string, value: Any) {
    setP((x: Any) => ({...x, [key]: value}));
    setDirty(true);
  }

  function edit(id: string, changes: Any) {
    setP((x: Any) => ({...x, scenes: x.scenes.map((s: Any) => s.id === id ? {...s, ...changes} : s)}));
    setDirty(true);
  }

  async function save() {
    if (!row) return;
    const r = await api('/projects/' + row.id, {revision: row.revision, project: p}, 'PUT');
    sync(r);
    return r;
  }

  async function create() {
    if (dirty && !confirm('You have unsaved edits. Create a new project without saving them?')) return;
    const r = await api('/projects', empty());
    sync(r);
    setTab('write');
    tell('Project created. Start with a topic or your own script.');
  }

  async function queue(kind: string, extra: Any = {}) {
    const r = dirty ? await save() : row;
    const j = await api('/projects/' + r.id + '/jobs', {revision: r.revision, kind, ...extra});
    setJobs(x => [j, ...x]);
    tell('Job queued. Keep the launcher running.');
  }

  async function approve() {
    const r = dirty ? await save() : row;
    const updated = await api('/projects/' + r.id + '/approve', {revision: r.revision});
    sync(updated);
    tell('This script revision is approved. You can prepare narration or render.');
  }

  async function upload(file: File, id?: string, kind = 'video') {
    if (!file) return;
    const f = new FormData();
    f.append('file', file);
    const a = await api('/assets', f);
    if (a.kind !== kind) throw new Error('Please select a ' + kind + ' file.');
    if (!id) {
      change('music_asset', a.id);
      change('music_rights_reviewed', false);
    } else if (kind === 'audio') {
      const s = p.scenes.find((s: Any) => s.id === id);
      edit(id, {audio_asset: a.id, audio_text_hash: await hashText(s.narration), cues: []});
    } else {
      edit(id, {video_asset: a.id, visual: 'upload', rights_reviewed: true, trim_start: 0});
    }
    tell('Uploaded. Save the project to keep this selection.');
  }

  // Server-side idempotent result application
  async function applyJob(j: Any, force = false) {
    const res = await api('/projects/' + row.id + '/apply-job/' + j.id + (force ? '?force=true' : ''), {}, 'POST');
    sync(res.project);
    if (res.conflicts && res.conflicts.length > 0) {
      setConflicts(res.conflicts);
      tell('Result applied with ' + res.conflicts.length + ' manual edit(s) preserved.', true);
    } else {
      setConflicts([]);
      tell('Result applied successfully to project (v' + res.project.revision + ').');
    }
    const updatedJobs = await api('/projects/' + row.id + '/jobs');
    setJobs(updatedJobs);
  }

  async function openLibrary(targetSceneId = 'all') {
    setLibraryTargetScene(targetSceneId);
    try {
      const assets = await api('/assets');
      setMediaAssets(assets);
      setShowMediaLibrary(true);
    } catch (e: any) {
      tell(e.message, true);
    }
  }

  async function assignLibraryAsset(assetId: string) {
    if (libraryTargetScene === 'all') {
      const updated = await api('/projects/' + row.id + '/assign-media', {asset_id: assetId, scene_ids: ['all']});
      sync(updated);
      tell('Clip assigned to all scenes.');
    } else {
      edit(libraryTargetScene, {video_asset: assetId, visual: 'upload', rights_reviewed: true, trim_start: 0});
      tell('Clip assigned to scene.');
    }
    setShowMediaLibrary(false);
  }

  // Voice preview reuse across unrelated edits
  const latestVoice = jobs.find(j => j.kind === 'voice' && j.state === 'succeeded');
  const exports = jobs.filter(j => j.kind === 'render' && j.state === 'succeeded');
  const pending = jobs.filter(j => j.state === 'queued' || j.state === 'running').length;

  const scriptApproved = row?.approved && !dirty;
  const visualsReviewed = p?.scenes?.every((s: Any) => s.rights_reviewed);
  const renderReady = preflight?.ready;

  if (!cfg) return <div className="loading">{message || 'Opening your studio…'}{message && <p>Check that START_WINDOWS.bat is still running, then reload this page.</p>}</div>;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brandicon">▶</span>
          <div>Telugu<span>REEL STUDIO</span></div>
        </div>
        <button className="new" onClick={() => action(create)} disabled={busy}>＋ New story</button>
        <div className="sidebarlabel">YOUR PROJECTS</div>
        <nav>
          {projects.map(r => (
            <button key={r.id} className={row?.id === r.id ? 'project active' : 'project'} onClick={() => {
              if (!dirty || confirm('Discard unsaved edits and switch project?')) load(r);
            }}>
              <b>{r.project.title}</b>
              <small>{r.project.category} · {r.project.target_seconds}s target</small>
            </button>
          ))}
        </nav>
        <div className="sidebarfoot">
          <span className="dot"/> Local workspace
          <p>Telugu voice<br/>Roman Telugu captions</p>
          <button className="ghost" onClick={() => setShowSettings(true)}>⚙ Settings & providers</button>
        </div>
      </aside>

      <main>
        <header>
          <div>
            <span className="eyebrow">YOUR DAILY STORY, YOUR EDITORIAL CONTROL</span>
            <h1>{p ? p.title : 'Make something worth watching.'}</h1>
          </div>
          <div className="topactions">
            {row && (
              <>
                <span className={'badge ' + (dirty ? '' : scriptApproved ? 'approved' : 'amber')}>
                  {dirty ? 'Unsaved edits' : scriptApproved ? 'Approved · v' + row.revision : 'Draft · v' + row.revision}
                </span>
                <span className={'badge ' + (visualsReviewed ? 'approved' : 'amber')}>
                  {visualsReviewed ? 'Visuals Reviewed' : 'Visuals Review Pending'}
                </span>
                <span className={'badge ' + (renderReady ? 'approved' : 'red')}>
                  {renderReady ? 'Render Ready' : 'Preflight Issues'}
                </span>
                <button onClick={() => action(async () => { await save(); tell('Project saved.'); })} disabled={busy || !dirty}>
                  Save project
                </button>
              </>
            )}
          </div>
        </header>

        {draftRecoverable && (
          <div className="notice" role="alert" style={{background: '#fef3c7', borderColor: '#fde68a', color: '#92400e'}}>
            <div>
              <b>Unsaved draft found</b> from {new Date(draftRecoverable.updated).toLocaleString()}. Would you like to recover it?
            </div>
            <div className="actions" style={{margin: 0}}>
              <button className="primary" onClick={() => {
                setP(draftRecoverable.body);
                setDirty(true);
                setDraftRecoverable(null);
                tell('Autosaved draft restored. Review and save project.');
              }}>Restore draft</button>
              <button onClick={() => action(async () => {
                await api('/projects/' + row.id + '/draft', undefined, 'DELETE');
                setDraftRecoverable(null);
                tell('Draft discarded.');
              })}>Discard</button>
            </div>
          </div>
        )}

        {conflicts.length > 0 && (
          <div className="notice" role="alert" style={{background: '#fef3c7', borderColor: '#fde68a', color: '#92400e'}}>
            <div>
              <b>Conflict-aware merge preserved your manual edits:</b>
              <ul>{conflicts.map((c, i) => <li key={i}>{c.scene_id}: {c.reason}</li>)}</ul>
            </div>
            <button onClick={() => setConflicts([])}>×</button>
          </div>
        )}

        {message && (
          <div className={'notice ' + (error ? 'error' : '')} role="status">
            {message}
            <button onClick={() => setMessage('')}>×</button>
          </div>
        )}

        {!p ? (
          <section className="welcome panel">
            <span className="eyebrow">FROM AN IDEA TO A FINISHED REEL</span>
            <h2>Your next story starts here.</h2>
            <p>Research a topic or bring your own script. Shape the narration, choose the visuals, and export when you are happy with the result.</p>
            <button className="primary" onClick={() => action(create)}>Create your first story →</button>
            <p className="muted">No account required. Online services are off until you enable them.</p>
          </section>
        ) : (
          <>
            <div className="tabs">
              {[
                ['write', '01', 'Story & evidence'],
                ['scenes', '02', 'Voice & visuals'],
                ['export', '03', 'Preview & export']
              ].map(([id, n, title]) => (
                <button className={tab === id ? 'selected' : ''} onClick={() => setTab(id)} key={id}>
                  <span>{n}</span>{title}
                </button>
              ))}
            </div>

            {tab === 'write' && (
              <>
                <section className="panel">
                  <div className="sectionhead">
                    <div>
                      <span className="eyebrow">THE BRIEF</span>
                      <h2>What is today's story?</h2>
                    </div>
                    <button className="ghost" onClick={() => action(async () => setHistory(await api('/projects/' + row.id + '/history')))}>
                      Revision history
                    </button>
                  </div>
                  <div className="grid two">
                    <Field label="Project title">
                      <input value={p.title} onChange={e => change('title', e.target.value)} />
                    </Field>
                    <Field label="Editorial preset">
                      <select value={p.category} onChange={e => change('category', e.target.value)}>
                        {['Storytelling', 'Facts', 'Mystery', 'History', 'Actors news', 'Cricket news', 'AI', 'Technology', 'Other'].map(x => <option key={x}>{x}</option>)}
                      </select>
                    </Field>
                  </div>
                  <Field label="Topic or angle">
                    <textarea rows={2} placeholder="An unusual story, a question, or a topic you want to explain…" value={p.topic} onChange={e => change('topic', e.target.value)} />
                  </Field>
                  <div className="grid two">
                    <Field label="Target duration (seconds)" hint="10–180 seconds. Guides script generation; final duration follows your selected mode.">
                      <input type="number" min="10" max="180" value={p.target_seconds} onChange={e => change('target_seconds', +e.target.value)} />
                    </Field>
                    <Field label="Duration mode">
                      <select value={p.duration_mode} onChange={e => change('duration_mode', e.target.value)}>
                        <option value="natural">Natural narration pace</option>
                        <option value="target">Fit target — modest speed adjustment only</option>
                      </select>
                    </Field>
                  </div>
                  <div className="actions">
                    <button disabled={busy} onClick={() => action(() => queue('research'))}>Research / check claims</button>
                    <button disabled={busy || !p.research_report} onClick={() => action(() => queue('draft'))}>Draft from research</button>
                  </div>
                  <small className="muted">Online research and drafting need a Gemini key and model ID. Grounded search may incur provider charges.</small>
                </section>

                <section className="panel">
                  <h2>Start with your own script</h2>
                  <p className="muted">Paste native Telugu or Roman Telugu. AI can segment and transliterate it into editable scenes; you review the result before using it.</p>
                  <textarea rows={4} value={script} onChange={e => {
                    setScript(e.target.value);
                    change('draft_script', e.target.value);
                  }} placeholder="Paste your script here…" />
                  <div className="actions">
                    <button disabled={busy || !script.trim()} onClick={() => action(() => queue('draft', {script}))}>Prepare my script with AI</button>
                    <button disabled={busy} onClick={() => {
                      change('scenes', [...p.scenes, scene()]);
                      tell('Blank scene added below. No AI required.');
                    }}>＋ Add scene manually</button>
                  </div>
                </section>

                <section className="panel">
                  <div className="sectionhead">
                    <h2>Script editor</h2>
                    <span className="badge">{p.scenes.length} scenes</span>
                  </div>
                  <p className="muted">The left field is spoken aloud. The right field appears on screen. Roman Telugu is transliteration, not English translation.</p>
                  {p.scenes.map((s: Any, i: number) => (
                    <div className="scriptrow" key={s.id}>
                      <div className="scenenumber">{String(i + 1).padStart(2, '0')}</div>
                      <div className="scriptbody">
                        <div className="grid two">
                          <Field label="Telugu narration">
                            <textarea lang="te" rows={4} value={s.narration} onChange={e => edit(s.id, {narration: e.target.value, cues: []})} placeholder="తెలుగులో మీ కథ…" />
                          </Field>
                          <Field label="Roman Telugu captions">
                            <textarea rows={4} value={s.roman} onChange={e => edit(s.id, {roman: e.target.value, cues: []})} placeholder="Telugulo mee katha…" />
                          </Field>
                        </div>
                        <div className="rowtools">
                          <button disabled={i === 0} onClick={() => {
                            const a = [...p.scenes];
                            [a[i - 1], a[i]] = [a[i], a[i - 1]];
                            change('scenes', a);
                          }}>Move up</button>
                          <button onClick={() => change('scenes', p.scenes.filter((x: Any) => x.id !== s.id))}>Remove scene</button>
                          {s.audio_asset && <span className="muted">Uploaded narration attached; re-attach after text changes.</span>}
                        </div>
                      </div>
                    </div>
                  ))}
                  <button onClick={() => change('scenes', [...p.scenes, scene()])}>＋ Add scene</button>
                </section>

                <section className="panel">
                  <div className="sectionhead">
                    <h2>Evidence & editorial review</h2>
                    <button className="ghost" onClick={() => setResearchOpen(!researchOpen)}>{researchOpen ? 'Hide' : 'Show'} research</button>
                  </div>
                  {researchOpen && (
                    <>
                      <pre className="research">{p.research_report || 'No research report applied yet. Completed jobs appear below.'}</pre>
                      <div className="sources">{p.research_sources.map((s: Any, i: number) => <Link url={s.url} key={i}>{i + 1}. {s.title}</Link>)}</div>
                      {p.research_grounding?.searchEntryPoint?.renderedContent && (
                        <iframe title="Google Search suggestions" sandbox="allow-popups allow-popups-to-escape-sandbox" srcDoc={p.research_grounding.searchEntryPoint.renderedContent} />
                      )}
                    </>
                  )}
                  <Field label="Your review notes / source links" hint="Record what you checked, unresolved claims, or that the story is fictional. Approval records your review; it does not certify facts.">
                    <textarea rows={4} value={p.evidence_notes} onChange={e => change('evidence_notes', e.target.value)} placeholder="Sources checked, corrections made, and any uncertainty…" />
                  </Field>
                  <button className="primary" disabled={busy} onClick={() => action(approve)}>Save & approve this revision →</button>
                </section>
              </>
            )}

            {tab === 'scenes' && (
              <>
                <section className="panel">
                  <div className="sectionhead">
                    <div>
                      <span className="eyebrow">PHASE 4 · TELUGU VOICE LAB & SOUND PROFILE</span>
                      <h2>Telugu Narration & Delivery Presets</h2>
                    </div>
                    <div className="actions" style={{margin: 0}}>
                      <button onClick={() => openLibrary('all')}>Reusable media library</button>
                      <button disabled={busy} onClick={() => action(() => queue('stock_all'))}>Find footage for unassigned scenes</button>
                    </div>
                  </div>

                  {/* Provider, Voice & Locking */}
                  <div className="grid two">
                    <Field label="TTS Provider" hint="Sarvam AI specialist, Edge zero-cost, Azure SDK/REST, or Human Upload">
                      <select value={p.tts_provider || 'edge'} onChange={e => {
                        const newProv = e.target.value;
                        const defaultVoiceForProv =
                          newProv === 'sarvam' ? 'pavithra' :
                          newProv === 'azure' ? 'te-IN-ShrutiNeural' :
                          newProv === 'upload' ? 'human_upload' : 'te-IN-ShrutiNeural';
                        setP((prev: Any) => ({...prev, tts_provider: newProv, voice: defaultVoiceForProv}));
                        setDirty(true);
                      }}>
                        <option value="edge">Edge Neural (Free · te-IN voices)</option>
                        <option value="sarvam">Sarvam AI (Bulbul Neural · Telugu specialist)</option>
                        <option value="azure">Azure Speech (REST / SDK · Cloud)</option>
                        <option value="upload">Upload Narration (Human Voice)</option>
                      </select>
                    </Field>

                    <Field label="Voice">
                      <select value={p.voice || 'te-IN-ShrutiNeural'} onChange={e => change('voice', e.target.value)}>
                        {(p.tts_provider === 'sarvam') && (
                          <>
                            <option value="pavithra">Pavithra · Bulbul v3 (Telugu Female)</option>
                            <option value="meera">Meera · Bulbul v3 (Telugu Female)</option>
                            <option value="arvind">Arvind · Bulbul v3 (Telugu Male)</option>
                            <option value="amartya">Amartya · Bulbul v3 (Telugu Male)</option>
                          </>
                        )}
                        {(p.tts_provider === 'edge' || p.tts_provider === 'azure') && (
                          <>
                            <option value="te-IN-ShrutiNeural">Shruti · Telugu female</option>
                            <option value="te-IN-MohanNeural">Mohan · Telugu male</option>
                          </>
                        )}
                        {(p.tts_provider === 'upload') && (
                          <option value="human_upload">Uploaded Audio (Human Voice)</option>
                        )}
                      </select>
                    </Field>
                  </div>

                  {/* Verified Capabilities & Limits Badge Strip */}
                  {(() => {
                    const currentCap = voiceCapabilities.find((c: Any) => c.voice_id === p.voice && c.provider === p.tts_provider)
                      || voiceCapabilities.find((c: Any) => c.voice_id === p.voice)
                      || voiceCapabilities[0];
                    if (!currentCap) return null;
                    return (
                      <div style={{margin: '12px 0'}}>
                        <small className="muted" style={{display: 'block', marginBottom: 4}}>VERIFIED PROVIDER CAPABILITIES & CONSTRAINTS:</small>
                        <div className="cap-badges">
                          {currentCap.supported_controls?.map((ctrl: string) => (
                            <span key={ctrl} className="badge-cap-ok">✓ {ctrl.toUpperCase()}</span>
                          ))}
                          {currentCap.unsupported_controls?.map((ctrl: string) => (
                            <span key={ctrl} className="badge-cap-no" title="Unsupported controls are visibly disabled and not sent">✕ {ctrl}</span>
                          ))}
                          <span className="badge-cap-info">Format: {currentCap.audio_formats?.[0] || 'mp3'}</span>
                          <span className="badge-cap-info">Timing: {currentCap.timing_metadata}</span>
                          <span className="badge-cap-info">Limit: {currentCap.max_characters_per_request} chars</span>
                          <span className="badge-cap-info">Est. Cost: ₹{(currentCap.estimated_cost_per_1k_chars * 1000).toFixed(2)}/1M chars</span>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Voice Locking Toggle */}
                  <label className="check">
                    <input type="checkbox" checked={p.voice_locked || false} onChange={e => change('voice_locked', e.target.checked)} />
                    <b>🔒 Lock voice profile to project</b> (Mid-project provider/speaker switches require explicit approval)
                  </label>

                  {/* Delivery Presets & Speech Rate */}
                  <div className="grid two" style={{marginTop: 10}}>
                    <Field label="Delivery Preset" hint="Mapped strictly to verified provider controls and natural punctuation breathing pauses">
                      <select value={p.delivery_preset || 'conversational'} onChange={e => change('delivery_preset', e.target.value)}>
                        <option value="conversational">Conversational (Natural, casual storytelling flow)</option>
                        <option value="documentary">Documentary (Measured, authoritative cadence)</option>
                        <option value="curious">Curious (Inquisitive pitch lift & lively pace)</option>
                        <option value="suspense">Suspense (Dramatic pauses & lower pitch)</option>
                      </select>
                    </Field>

                    <Field label={'Speech rate: ' + p.speech_rate + '%'} hint={p.tts_provider === 'upload' ? 'Rate adjustment not applicable to uploaded audio' : 'Restrained tempo adjustment'}>
                      <input
                        type="range"
                        min="-25"
                        max="25"
                        disabled={p.tts_provider === 'upload'}
                        value={p.speech_rate || 0}
                        onChange={e => change('speech_rate', +e.target.value)}
                      />
                    </Field>
                  </div>

                  {/* Action buttons */}
                  <div className="actions">
                    <button disabled={busy} onClick={() => action(approve)}>Save & approve changes</button>
                    <button className="primary" disabled={busy} onClick={() => action(() => queue('voice'))}>Prepare voice previews</button>
                  </div>

                  {/* Pronunciation Dictionary Editor */}
                  <details style={{marginTop: 16}}>
                    <summary><b>Pronunciation Dictionary & Overrides</b> ({Object.keys(p.pronunciation_dict || {}).length} custom mappings)</summary>
                    <p className="muted">Explicitly map English loanwords, acronyms, and place names to native Telugu phonetic forms. Normalizer applies these before synthesis.</p>
                    <div style={{maxWidth: 600}}>
                      <div className="dict-row">
                        <input placeholder="Word / Acronym (e.g. AI, API, Vizag)" value={newDictKey} onChange={e => setNewDictKey(e.target.value)} />
                        <span>→</span>
                        <input placeholder="Spoken Telugu (e.g. ఏఐ, ఏపీఐ, వైజాగ్)" value={newDictVal} onChange={e => setNewDictVal(e.target.value)} />
                        <button type="button" onClick={addDictEntry}>Add override</button>
                      </div>
                      {Object.entries(p.pronunciation_dict || {}).map(([k, v]) => (
                        <div key={k} className="dict-row" style={{background: '#fff', padding: '6px 12px', border: '1px solid var(--line)', borderRadius: 6}}>
                          <b style={{flex: 1}}>{k}</b>
                          <span>→</span>
                          <span style={{flex: 1}}>{v as string}</span>
                          <button type="button" className="ghost" onClick={() => removeDictEntry(k)}>✕</button>
                        </div>
                      ))}
                    </div>

                    <div style={{marginTop: 14, paddingTop: 12, borderTop: '1px dashed var(--line)'}}>
                      <small><b>Test Pronunciation Normalizer:</b></small>
                      <div className="dict-row" style={{marginTop: 6}}>
                        <input placeholder="Test text with numbers/dates/English terms..." value={normTestText} onChange={e => setNormTestText(e.target.value)} />
                        <button type="button" onClick={testNormalize}>Normalize</button>
                      </div>
                      {normTestResult && (
                        <div style={{background: '#f4f7f1', padding: 10, borderRadius: 6, fontSize: 13}}>
                          <div><b>Spoken Telugu:</b> {normTestResult.spoken_text}</div>
                          <div style={{color: 'var(--muted)', marginTop: 4}}><b>Roman Captions:</b> {normTestResult.roman_text}</div>
                        </div>
                      )}
                    </div>
                  </details>

                  {/* Conversational Rewrite Preview (Approval Required) */}
                  <details style={{marginTop: 12}}>
                    <summary><b>Conversational Rewrite Preview</b> (Colloquial rhythm review)</summary>
                    <p className="muted">Polishes bookish Telugu suffixes (ఉన్నది → ఉంది, చేసినది → చేశారు) into natural conversational spoken flow. Diff is shown and requires your approval.</p>
                    <div className="actions">
                      <button type="button" disabled={busy} onClick={checkConversationalRewrite}>
                        Check Scene {p.scenes.findIndex((s: Any) => s.id === (voiceLabSceneId || p.scenes[0]?.id)) + 1} For Colloquial Polish
                      </button>
                    </div>
                    {rewritePreview && (
                      <div className="diff-container">
                        <b>Conversational Polish Diff:</b>
                        <p>{rewritePreview.note}</p>
                        <div style={{margin: '10px 0', padding: 12, background: '#fafafa', border: '1px solid var(--line)', borderRadius: 6}}>
                          {rewritePreview.diff?.map((part: Any, idx: number) => {
                            if (part.type === 'removed') return <span key={idx} className="diff-word-del">{part.text}</span>;
                            if (part.type === 'added') return <span key={idx} className="diff-word-add">{part.text}</span>;
                            return <span key={idx}>{part.text} </span>;
                          })}
                        </div>
                        {rewritePreview.changed ? (
                          <div className="actions">
                            <button type="button" className="primary" onClick={applyRewrite}>✓ Approve & Apply to Scene Narration</button>
                            <button type="button" onClick={() => setRewritePreview(null)}>Dismiss</button>
                          </div>
                        ) : (
                          <small className="muted">No colloquial changes needed for this text.</small>
                        )}
                      </div>
                    )}
                  </details>

                  {/* Interactive A/B Candidate Preview & Comparison */}
                  <div className="voicelab" style={{marginTop: 20}}>
                    <h3>🎙️ Voice Candidate Preview & Identical-Script A/B Test</h3>
                    <p className="muted">
                      Synthesize identical script with candidate voice. Compare side-by-side with baseline before committing.
                    </p>

                    <div className="grid two" style={{alignItems: 'flex-end'}}>
                      <Field label="Test Scene">
                        <select value={voiceLabSceneId || p.scenes[0]?.id} onChange={e => {
                          setVoiceLabSceneId(e.target.value);
                          setCandidateTake(null);
                        }}>
                          {p.scenes.map((sc: Any, idx: number) => (
                            <option key={sc.id} value={sc.id}>Scene {idx + 1}: {sc.narration?.slice(0, 40) || 'Untitled'}...</option>
                          ))}
                        </select>
                      </Field>

                      <div className="actions" style={{marginBottom: 13}}>
                        <button type="button" className="primary" disabled={busy} onClick={generateVoicePreview}>
                          ▶ Generate Candidate Preview Take
                        </button>
                        <label className="check" style={{margin: 0}}>
                          <input type="checkbox" checked={explicitRegenerate} onChange={e => setExplicitRegenerate(e.target.checked)} />
                          Bypass cache (explicit regenerate)
                        </label>
                      </div>
                    </div>

                    {/* Text tiers preview */}
                    {(() => {
                      const curSc = p.scenes.find((s: Any) => s.id === (voiceLabSceneId || p.scenes[0]?.id)) || p.scenes[0];
                      if (!curSc) return null;
                      return (
                        <div style={{background: '#fff', border: '1px solid var(--line)', borderRadius: 8, padding: 12, margin: '10px 0', fontSize: 13}}>
                          <div><b>Source Narration (Telugu):</b> {curSc.narration || '(empty)'}</div>
                          <div style={{marginTop: 4}}><b>Spoken Telugu (Normalized):</b> {curSc.spoken_text || curSc.narration || '(empty)'}</div>
                          <div style={{color: 'var(--muted)', marginTop: 4}}><b>Display Captions (Roman):</b> {curSc.roman || '(empty)'}</div>
                        </div>
                      );
                    })()}

                    {/* Side-by-side A/B Cards */}
                    {candidateTake && (
                      <div>
                        <div className="ab-grid">
                          <div className="ab-card">
                            <h4>
                              <b>Baseline Voice</b>
                              <span className="badge">Baseline</span>
                            </h4>
                            <audio controls src={'/api/assets/' + (p.scenes.find((s: Any) => s.id === voiceLabSceneId)?.audio_asset || '') + '/media'} />
                            <small className="muted">Current project scene audio asset</small>
                          </div>

                          <div className="ab-card active-take">
                            <h4>
                              <b>Candidate Take ({candidateTake.display_name})</b>
                              <span className="badge approved">Candidate Take</span>
                            </h4>
                            <audio controls autoPlay src={candidateTake.audio_url} />
                            <small>
                              Duration: <b>{candidateTake.duration?.toFixed(2)}s</b> · Provider: {candidateTake.provider} · Preset: {candidateTake.preset}
                            </small>
                            <div className="actions" style={{marginTop: 6}}>
                              <button type="button" className="primary" onClick={() => acceptTake(candidateTake.take_id)}>
                                ✓ Accept This Take for Scene
                              </button>
                            </div>
                          </div>
                        </div>

                        <div className="ab-metrics">
                          <div><b>Duration:</b> {candidateTake.duration?.toFixed(2)}s</div>
                          <div><b>Script Match:</b> ✓ Identical text synthesized</div>
                          <div><b>Est. Cost:</b> ₹{(candidateTake.estimated_cost * 100).toFixed(4)}</div>
                          <div><b>Cache Status:</b> Contract key {candidateTake.contract_key?.slice(0, 8)}...</div>
                        </div>
                      </div>
                    )}

                    {/* Take History */}
                    {takeHistory.length > 0 && (
                      <details style={{marginTop: 14}}>
                        <summary><b>Take History for Project</b> ({takeHistory.length} recorded takes)</summary>
                        <table className="takes-table">
                          <thead>
                            <tr>
                              <th>Take ID</th>
                              <th>Scene</th>
                              <th>Voice / Provider</th>
                              <th>Preset</th>
                              <th>Duration</th>
                              <th>Status</th>
                              <th>Listen & Action</th>
                            </tr>
                          </thead>
                          <tbody>
                            {takeHistory.map((tk: Any) => (
                              <tr key={tk.id}>
                                <td><code>{tk.id?.slice(0, 10)}</code></td>
                                <td>{tk.scene_id ? `Scene ${p.scenes.findIndex((s: Any) => s.id === tk.scene_id) + 1}` : 'General'}</td>
                                <td>{tk.voice_id} ({tk.provider})</td>
                                <td>{tk.preset}</td>
                                <td>{tk.duration?.toFixed(1)}s</td>
                                <td>
                                  {tk.accepted ? (
                                    <span className="badge approved">Accepted</span>
                                  ) : (
                                    <span className="badge">Take</span>
                                  )}
                                </td>
                                <td>
                                  <div style={{display: 'flex', gap: 6, alignItems: 'center'}}>
                                    <audio style={{height: 28, width: 150}} controls src={tk.audio_url} />
                                    {!tk.accepted && (
                                      <button type="button" onClick={() => acceptTake(tk.id)}>Accept</button>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </details>
                    )}
                  </div>
                </section>

                {p.scenes.map((s: Any, i: number) => {
                  // Voice preview reuse across unrelated edits: verify scene narration matches
                  const v = latestVoice?.result?.voices?.find((x: Any) => {
                    if (x.scene_id !== s.id) return false;
                    const snapScene = latestVoice.snapshot?.project?.scenes?.find((sc: Any) => sc.id === s.id);
                    return snapScene && snapScene.narration === s.narration;
                  });

                  return (
                    <section className="panel" key={s.id}>
                      <div className="sectionhead">
                        <div>
                          <span className="eyebrow">SCENE {String(i + 1).padStart(2, '0')}</span>
                          <h2>{s.graphic_title || s.roman.slice(0, 55) || 'Choose a visual'}</h2>
                        </div>
                        <span className={'badge ' + (s.visual === 'graphic' ? 'approved' : s.video_asset ? '' : 'red')}>
                          {s.visual === 'graphic' ? 'graphic title card' : s.video_asset ? s.visual : 'media missing'}
                        </span>
                      </div>
                      <p>{s.roman}</p>
                      <div className="scenegrid">
                        <div>
                          {s.video_asset && s.visual !== 'graphic' ? (
                            <video className="clippreview" controls preload="metadata" src={'/api/assets/' + s.video_asset + '/media'} />
                          ) : (
                            <div className="visualplaceholder">
                              <span>✦</span>
                              <p>{s.visual === 'graphic' ? s.graphic_title || 'Your graphic title' : 'Select footage or upload a clip'}</p>
                            </div>
                          )}
                        </div>
                        <div>
                          <Field label="Visual source">
                            <select value={s.visual} onChange={e => edit(s.id, {visual: e.target.value, rights_reviewed: false})}>
                              <option value="upload">Uploaded footage</option>
                              <option value="stock">Pixabay stock footage</option>
                              <option value="graphic">Simple animated title card (explicit choice)</option>
                            </select>
                          </Field>
                          {s.visual === 'graphic' ? (
                            <Field label="Graphic heading (English / Roman Telugu)">
                              <input value={s.graphic_title} onChange={e => edit(s.id, {graphic_title: e.target.value})} />
                            </Field>
                          ) : (
                            <>
                              <Field label="English stock search terms">
                                <input value={s.keywords} maxLength={100} onChange={e => edit(s.id, {keywords: e.target.value})} />
                              </Field>
                              <div className="actions">
                                <button disabled={busy || !s.keywords} onClick={() => action(async () => {
                                  setStock((x: Any) => ({...x, [s.id]: awaitablePlaceholder}));
                                  const hits = await api('/stock?q=' + encodeURIComponent(s.keywords));
                                  setStock((x: Any) => ({...x, [s.id]: hits}));
                                })}>Find on Pixabay</button>
                                <button onClick={() => openLibrary(s.id)}>Choose from library</button>
                                <label className="filebutton">Upload video<input type="file" accept="video/*" disabled={busy} onChange={e => {
                                  const f = e.target.files?.[0];
                                  if (f) action(() => upload(f, s.id));
                                  e.target.value = '';
                                }}/></label>
                              </div>
                              <div className="grid two">
                                <Field label="Trim start (seconds)">
                                  <input type="number" min="0" step=".1" value={s.trim_start} onChange={e => edit(s.id, {trim_start: +e.target.value})} />
                                </Field>
                                <Field label="Framing">
                                  <select value={s.fit} onChange={e => edit(s.id, {fit: e.target.value})}>
                                    <option value="crop">Fill vertical frame</option>
                                    <option value="contain">Fit entire clip with padding</option>
                                  </select>
                                </Field>
                              </div>
                              {s.fit === 'crop' && (
                                <Field label="Horizontal crop position">
                                  <input type="range" min="0" max="1" step=".05" value={s.crop_x} onChange={e => edit(s.id, {crop_x: +e.target.value})} />
                                </Field>
                              )}
                              <small>Short footage loops to cover narration. Source clip audio is muted.</small>
                            </>
                          )}
                          <Field label="Hold after narration (seconds)">
                            <input type="number" min="0" max="30" step=".1" value={s.hold_seconds} onChange={e => edit(s.id, {hold_seconds: +e.target.value})} />
                          </Field>
                          <label className="check">
                            <input type="checkbox" checked={s.rights_reviewed} onChange={e => edit(s.id, {rights_reviewed: e.target.checked})} />
                            I reviewed visual relevance and permission to use it.
                          </label>
                        </div>
                      </div>

                      {Array.isArray(stock[s.id]) && (
                        <div className="stockgrid">
                          {stock[s.id].length === 0 && <p>No matching stock clips. Change the query or upload your own.</p>}
                          {stock[s.id].map((h: Any) => (
                            <div className="stockcard" key={h.id}>
                              <p>{h.tags}</p>
                              <small>by {h.creator} via Pixabay · {h.duration}s · {h.video.width}×{h.video.height}</small>
                              <Link url={h.source_url}>View source & clip ↗</Link>
                              <button disabled={busy} onClick={() => action(() => queue('stock', {video_id: h.id, scene_id: s.id}))}>Download this clip</button>
                            </div>
                          ))}
                        </div>
                      )}

                      <div className="voicebox">
                        <b>Narration & captions</b>
                        <div className="actions">
                          <label className="filebutton">Upload scene narration<input type="file" accept="audio/*" disabled={busy} onChange={e => {
                            const f = e.target.files?.[0];
                            if (f) action(() => upload(f, s.id, 'audio'));
                            e.target.value = '';
                          }}/></label>
                          {s.audio_asset && <button onClick={() => edit(s.id, {audio_asset: '', audio_text_hash: '', cues: []})}>Use generated voice instead</button>}
                        </div>
                        {s.audio_asset && <audio controls src={'/api/assets/' + s.audio_asset + '/media'} />}
                        {v && (
                          <>
                            <audio controls src={'/api/jobs/' + latestVoice.id + '/audio/' + s.id} />
                            <p className="muted">{v.duration.toFixed(1)}s · Caption timing: {v.alignment}</p>
                            <button onClick={() => setCueEdit((x: Any) => ({...x, [s.id]: JSON.stringify(v.cues, null, 2)}))}>
                              Load generated timings for editing
                            </button>
                          </>
                        )}
                        <details>
                          <summary>Edit caption timestamps</summary>
                          <p className="muted">JSON array of {'{start, end, text}'}. Seconds relative to this scene's original narration. An empty array uses automatic estimates. Preview every scene.</p>
                          <textarea rows={7} value={cueEdit[s.id] ?? JSON.stringify(s.cues, null, 2)} onChange={e => setCueEdit((x: Any) => ({...x, [s.id]: e.target.value}))} />
                          <button onClick={() => action(async () => {
                            const c = JSON.parse(cueEdit[s.id] ?? JSON.stringify(s.cues));
                            if (!Array.isArray(c)) throw new Error('Use a JSON array.');
                            edit(s.id, {cues: c});
                            tell('Caption timings applied. Save and re-approve before rendering.');
                          })}>Apply timings</button>
                        </details>
                      </div>
                    </section>
                  );
                })}

                <section className="panel">
                  <h2>Background music</h2>
                  <p className="muted">Use a track whose license covers your Instagram and YouTube uploads. Voice-triggered ducking lowers the music during narration.</p>
                  <div className="actions">
                    <label className="filebutton">Upload music<input type="file" accept="audio/*" disabled={busy} onChange={e => {
                      const f = e.target.files?.[0];
                      if (f) action(() => upload(f, undefined, 'audio'));
                      e.target.value = '';
                    }}/></label>
                    {p.music_asset && <button onClick={() => change('music_asset', '')}>Remove music</button>}
                  </div>
                  {p.music_asset && (
                    <>
                      <audio controls src={'/api/assets/' + p.music_asset + '/media'} />
                      <Field label={'Music level: ' + Math.round(p.music_gain * 100) + '%'}>
                        <input type="range" min="0" max=".5" step=".01" value={p.music_gain} onChange={e => change('music_gain', +e.target.value)} />
                      </Field>
                      <label className="check">
                        <input type="checkbox" checked={p.music_rights_reviewed} onChange={e => change('music_rights_reviewed', e.target.checked)} />
                        I reviewed permission to use this music on both platforms.
                      </label>
                    </>
                  )}
                </section>
              </>
            )}

            {tab === 'export' && (
              <>
                <section className="panel">
                  <span className="eyebrow">THE FINAL CUT</span>
                  <h2>Preview, adjust, export.</h2>
                  <div className="grid two">
                    <Field label={'Caption size: ' + p.caption_size}>
                      <input type="range" min="30" max="90" value={p.caption_size} onChange={e => change('caption_size', +e.target.value)} />
                    </Field>
                    <Field label={'Bottom caption margin: ' + p.caption_bottom + ' px'}>
                      <input type="range" min="180" max="600" value={p.caption_bottom} onChange={e => change('caption_bottom', +e.target.value)} />
                    </Field>
                  </div>
                  <div className="exportsummary">
                    <div><b>9:16</b><small>Vertical composition</small></div>
                    <div><b>1080p</b><small>H.264 + AAC · 30 fps</small></div>
                    <div><b>{p.target_seconds}s</b><small>{p.duration_mode === 'natural' ? 'Target guide · natural pace' : 'Target fit · bounded speed'}</small></div>
                  </div>

                  {/* Preflight Checklist */}
                  <div className="sectionhead" style={{marginTop: 20}}>
                    <h3>Preflight Readiness Checklist</h3>
                    <span className={'badge ' + (renderReady ? 'approved' : 'red')}>
                      {renderReady ? 'All checks passed' : (preflight?.errors?.length || 0) + ' blocking issue(s)'}
                    </span>
                  </div>
                  <div className="checklist">
                    {preflight?.errors?.map((err: Any, idx: number) => (
                      <div key={idx} className="checkitem err">
                        <span>✕</span> {err.message}
                      </div>
                    ))}
                    {preflight?.warnings?.map((warn: Any, idx: number) => (
                      <div key={idx} className="checkitem warn">
                        <span>⚠</span> {warn.message}
                      </div>
                    ))}
                    {renderReady && (
                      <div className="checkitem ok">
                        <span>✓</span> Storyboard, footage, narration, and rights reviews are verified. Ready for rendering.
                      </div>
                    )}
                  </div>

                  <p className="muted">Use a 540p draft to review framing, pronunciation, and caption timing before the final render. Script changes require new approval. Final duration is measured and reported.</p>
                  <div className="actions">
                    <button disabled={busy} onClick={() => action(approve)}>Save & approve changes</button>
                    <button disabled={busy} onClick={() => action(() => queue('render', {quality: 'draft'}))}>
                      Render 540p draft
                    </button>
                    <button className="primary" disabled={busy} onClick={() => action(() => queue('render', {quality: 'final'}))}>
                      Render final 1080p →
                    </button>
                  </div>
                </section>

                {exports.map(j => (
                  <section className="panel exportcard" key={j.id}>
                    <video controls preload="metadata" src={'/api/exports/' + j.id + '/reel.mp4'} />
                    <div>
                      <span className="eyebrow">{j.result.dimensions[0] === 1080 ? 'FINAL EXPORT' : 'DRAFT PREVIEW'}</span>
                      <h2>{j.result.duration.toFixed(1)} seconds</h2>
                      <p>Revision {j.revision}{j.revision !== row.revision ? ' · earlier revision' : ''}<br/>{new Date(j.created).toLocaleString()}</p>
                      <div className="downloads">
                        {j.result.files.map((name: string) => (
                          <a key={name} className="filebutton" href={'/api/exports/' + j.id + '/' + name} download={name}>↓ {name}</a>
                        ))}
                      </div>
                    </div>
                  </section>
                ))}
              </>
            )}

            <section className="panel jobs">
              <div className="sectionhead">
                <h2>Production jobs</h2>
                <span className="badge">{pending ? pending + ' in progress' : 'Queue clear'}</span>
              </div>
              {!jobs.length && <p className="muted">Research, voice preparation, downloads, and renders appear here.</p>}
              {jobs.map(j => (
                <div className={'job ' + j.state} key={j.id}>
                  <div>
                    <b>
                      {j.kind === 'stock' ? 'Footage download' : j.kind[0].toUpperCase() + j.kind.slice(1)}
                      <span> · v{j.revision}</span>
                    </b>
                    <p>{j.progress}</p>
                  </div>
                  <span className={'badge ' + (j.applied ? 'approved' : '')}>
                    {j.applied ? 'Applied' : j.state}
                  </span>

                  {j.state === 'failed' && (
                    <div className="jobactions">
                      <button disabled={busy} onClick={() => action(async () => {
                        await api('/jobs/' + j.id + '/retry?mode=original', {});
                        tell('Retry queued with original inputs.');
                      })}>Retry original inputs</button>
                      <button disabled={busy} onClick={() => action(async () => {
                        await api('/jobs/' + j.id + '/retry?mode=current', {});
                        tell('Re-run with current project revision.');
                      })}>Re-run with current project</button>
                    </div>
                  )}

                  {j.state === 'succeeded' && ['research', 'draft', 'stock', 'stock_all'].includes(j.kind) && (
                    <button className={j.applied ? 'ghost' : 'primary'} disabled={busy} onClick={() => action(() => applyJob(j))}>
                      {j.applied ? 'Re-apply result' : 'Apply result'}
                    </button>
                  )}

                  {j.kind === 'stock_all' && j.state === 'succeeded' && (
                    <details>
                      <summary>Footage search results</summary>
                      {j.result.selections.map((x: Any) => (
                        <p key={x.scene_id}>{x.scene_id}: {x.error || 'Candidate downloaded; review after applying.'}</p>
                      ))}
                    </details>
                  )}

                  {j.state === 'succeeded' && j.kind === 'research' && (
                    <details>
                      <summary>Read report</summary>
                      <pre className="research">{j.result.text}</pre>
                      {j.result.sources?.map((s: Any, i: number) => (
                        <div key={i}><Link url={s.url}>{s.title}</Link></div>
                      ))}
                      {j.result.grounding?.searchEntryPoint?.renderedContent && (
                        <iframe title="Search suggestions" sandbox="allow-popups allow-popups-to-escape-sandbox" srcDoc={j.result.grounding.searchEntryPoint.renderedContent} />
                      )}
                    </details>
                  )}
                </div>
              ))}
            </section>
          </>
        )}

        <footer>LOCAL FIRST · HUMAN REVIEWED · MADE FOR TELUGU STORIES</footer>
      </main>

      {/* Reusable Media Library Modal */}
      {showMediaLibrary && (
        <div className="modalback">
          <section className="modal wide">
            <div className="sectionhead">
              <h2>Reusable Media Library</h2>
              <button onClick={() => setShowMediaLibrary(false)}>Close</button>
            </div>
            <p className="muted">
              Select an uploaded or downloaded clip to reuse across your scenes without duplicating files on disk.
            </p>
            <div className="actions">
              <label className="filebutton primary">
                Upload new MP4 to library
                <input type="file" accept="video/*" disabled={busy} onChange={async e => {
                  const f = e.target.files?.[0];
                  if (f) {
                    await action(async () => {
                      const form = new FormData();
                      form.append('file', f);
                      await api('/assets', form);
                      const updated = await api('/assets');
                      setMediaAssets(updated);
                      tell('Clip added to library.');
                    });
                  }
                  e.target.value = '';
                }} />
              </label>
            </div>
            {!mediaAssets.length && <p className="muted">No media assets in library yet. Upload clips or search Pixabay stock above.</p>}
            <div className="mediagrid">
              {mediaAssets.filter(a => a.kind === 'video').map(a => (
                <div className="mediacard" key={a.id}>
                  <video className="mediathumb" preload="metadata" src={'/api/assets/' + a.id + '/media'} />
                  <div className="mediainfo">
                    <b>{a.name || a.id}</b>
                    <small>{a.duration ? a.duration.toFixed(1) + 's' : ''} · {a.provider}</small>
                    <div className="mediaactions">
                      {libraryTargetScene !== 'all' && (
                        <button className="primary" onClick={() => assignLibraryAsset(a.id)}>
                          Use for this scene
                        </button>
                      )}
                      <button onClick={() => assignLibraryAsset(a.id)}>
                        Use across all scenes
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}

      {showSettings && (
        <div className="modalback">
          <section className="modal">
            <div className="sectionhead">
              <h2>Settings & providers</h2>
              <button onClick={() => setShowSettings(false)}>Close</button>
            </div>
            <p className="muted">Keys stay in this local application's data folder. Blank key fields preserve saved keys. Online actions send your topic/script or search terms to the chosen provider.</p>
            <label className="check">
              <input type="checkbox" checked={cfg.online_enabled} onChange={e => setCfg({...cfg, online_enabled: e.target.checked})} />
              Enable online services
            </label>
            <Field label="Gemini API key" hint={cfg.gemini_key_configured ? 'A key is saved.' : 'Needed for research and AI drafts.'}>
              <input type="password" autoComplete="off" value={cfg.gemini_key || ''} onChange={e => setCfg({...cfg, gemini_key: e.target.value})} />
            </Field>
            <Field label="Gemini model ID" hint="Recommended: gemini-2.5-flash (supports Google Search grounding and fast drafting).">
              <input value={cfg.gemini_model} placeholder="gemini-2.5-flash" onChange={e => setCfg({...cfg, gemini_model: e.target.value})} />
            </Field>
            <Field label="Maximum Gemini API requests per UTC day" hint="A request-count limit, not a financial spending cap. Provider billing and search charges still apply.">
              <input type="number" min="1" max="100" value={cfg.daily_ai_calls} onChange={e => setCfg({...cfg, daily_ai_calls: +e.target.value})} />
            </Field>
            <Field label="Pixabay API key" hint={cfg.pixabay_key_configured ? 'A key is saved.' : 'Needed for stock search and downloads.'}>
              <input type="password" autoComplete="off" value={cfg.pixabay_key || ''} onChange={e => setCfg({...cfg, pixabay_key: e.target.value})} />
            </Field>
            <Field label="Narration provider">
              <select value={cfg.tts_provider} onChange={e => setCfg({...cfg, tts_provider: e.target.value})}>
                <option value="edge">Edge community integration — no key, online</option>
                <option value="sarvam">Sarvam AI Bulbul neural — Telugu specialist</option>
                <option value="azure">Azure Speech — official API, account required</option>
              </select>
            </Field>
            {cfg.tts_provider === 'sarvam' && (
              <Field label="Sarvam API subscription key" hint={cfg.sarvam_key_configured ? 'A key is saved.' : 'Needed for Sarvam Bulbul neural synthesis.'}>
                <input type="password" autoComplete="off" value={cfg.sarvam_key || ''} onChange={e => setCfg({...cfg, sarvam_key: e.target.value})} />
              </Field>
            )}
            {cfg.tts_provider === 'azure' && (
              <>
                <Field label="Azure Speech key">
                  <input type="password" autoComplete="off" value={cfg.azure_key || ''} onChange={e => setCfg({...cfg, azure_key: e.target.value})} />
                </Field>
                <Field label="Azure region">
                  <input value={cfg.azure_region} placeholder="centralindia" onChange={e => setCfg({...cfg, azure_region: e.target.value})} />
                </Field>
              </>
            )}
            <Field label="FFmpeg executable" hint="Leave blank to use PATH, or paste the full path to ffmpeg.exe.">
              <input value={cfg.ffmpeg} onChange={e => setCfg({...cfg, ffmpeg: e.target.value})} />
            </Field>
            <Field label="FFprobe executable">
              <input value={cfg.ffprobe} onChange={e => setCfg({...cfg, ffprobe: e.target.value})} />
            </Field>
            <div className="actions">
              <button className="primary" disabled={busy} onClick={() => action(async () => {
                const clean = Object.fromEntries(Object.entries(cfg).filter(([k]) => !k.endsWith('_configured')));
                setCfg(await api('/settings', clean));
                tell('Settings saved.');
                setShowSettings(false);
              })}>Save settings</button>
              <button disabled={busy} onClick={() => action(async () => setHealth(await api('/health')))}>Check saved media setup</button>
            </div>
            {health && <pre className="research">{JSON.stringify(health, null, 2)}</pre>}
          </section>
        </div>
      )}

      {history && (
        <div className="modalback">
          <section className="modal">
            <div className="sectionhead">
              <h2>Revision history</h2>
              <button onClick={() => setHistory(null)}>Close</button>
            </div>
            <p className="muted">Restoring loads an older revision as unsaved edits. Save to create a new revision; history is preserved.</p>
            {history.map(h => (
              <div className="job" key={h.revision}>
                <div>
                  <b>Revision {h.revision}</b>
                  <p>{new Date(h.created).toLocaleString()}</p>
                </div>
                <button onClick={() => {
                  setP(h.project);
                  setDirty(true);
                  setHistory(null);
                }}>Load draft</button>
              </div>
            ))}
          </section>
        </div>
      )}
    </div>
  );
}

const awaitablePlaceholder: any = [];
createRoot(document.getElementById('root')!).render(<App/>);
