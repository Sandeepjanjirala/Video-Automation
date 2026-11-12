/**
 * Telugu Reel Studio - One-click draft video
 *
 * Runs the existing jobs in order: AI scene preparation (Telugu narration, captions and
 * stock keywords), stock footage for every scene, rights + save + approval, Mohan
 * narration, and a draft render (540x960 for a reel, 960x540 for a YouTube video). Every step is an ordinary job that also appears
 * under Production jobs; the run stops at the first failure with its message.
 */
(function($) {
  'use strict';

  const STEPS = [
    ['prepare', 'Prepare every scene with AI: Telugu narration, captions and stock search words'],
    ['footage', 'Plan the shots and pick matching stock footage with AI'],
    ['approve', 'Tick rights, save and approve'],
    ['voice', 'Generate the Mohan narration'],
    ['render', 'Render the draft video'],
  ];
  // Minutes per job, plus minutes per scene: a 20-minute YouTube video has a few hundred scenes.
  const TIMEOUT_MINUTES = {draft: [4, 0.5], stock_all: [20, 1], voice: [15, 0.3], render: [20, 0.6]};
  let running = false;
  let pollDelay = 1000;  // ms between progress checks while a step runs

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const esc = value => $('<div>').text(value == null ? '' : value).html();

  function project() {
    return StudioState.current && StudioState.current.project;
  }

  function hasText(s) {
    return !!(String(s.roman || '').trim() || String(s.narration || '').trim());
  }

  function show(key, state, detail) {
    const $li = $(`#autopilot-steps li[data-step="${key}"]`);
    $li.attr('data-state', state);
    $li.find('.autopilot-detail').text(detail || '');
  }

  function status(text, isError) {
    $('#autopilot-status').attr('class', isError ? 'cueissue error' : 'muted').text(text);
  }

  async function save() {
    if (StudioState.isDirty) await Projects.saveProject();
  }

  async function queueJob(kind, extra) {
    await save();
    const row = StudioState.current;
    return StudioApi.request('/projects/' + row.id + '/jobs', 'POST', Object.assign({revision: row.revision, kind: kind}, extra || {}));
  }

  async function waitJob(key, job) {
    const pid = StudioState.current.id, label = STEPS.find(s => s[0] === key)[1];
    const [base, perScene] = TIMEOUT_MINUTES[job.kind];
    const deadline = Date.now() + (base + perScene * project().scenes.length) * 60000;
    while (Date.now() < deadline) {
      const jobs = await StudioApi.request('/projects/' + pid + '/jobs');
      StudioState.setJobs(jobs);
      const now = (jobs || []).find(j => j.id === job.id);
      if (now && now.state === 'succeeded') return now;
      if (now && ['failed', 'cancelled'].includes(now.state)) throw new Error(now.progress || `${label}: ${now.state}`);
      if (now) show(key, 'running', now.progress);
      await sleep(pollDelay);
    }
    throw new Error('This step is taking unusually long. Check Production jobs, then run the button again.');
  }

  async function applyJob(job) {
    // Anything typed while the step ran is saved first, so the result merges with it instead of replacing it.
    if (StudioState.isDirty) await Projects.saveProject();
    const res = await StudioApi.request('/projects/' + StudioState.current.id + '/apply-job/' + job.id, 'POST', {});
    Projects.sync(res.project);
    if (res.conflicts && res.conflicts.length) throw new Error(res.conflicts[0].reason);
  }

  const run = {
    // A pasted script is split by its own lines, exactly like "Split into scenes"; the AI then
    // only completes what is missing (Telugu, captions, stock words), keeping the writer's text.
    prepare: async function() {
      if (!project().scenes.some(hasText)) {
        const script = String($('#input-import-script').val() || '').trim();
        if (!script) throw new Error('Paste your script into the Quick script importer first.');
        project().scenes = Scenes.splitScript(script, $('#script-input-format').val() !== 'telugu');
        StudioState.markDirty(true);
        Projects.populateEditor();
      }
      const job = await queueJob('draft', {fill: true});
      await applyJob(await waitJob('prepare', job));
      return `${project().scenes.length} scene(s) ready`;
    },

    footage: async function() {
      const missing = () => project().scenes.filter(s => s.visual !== 'graphic' && !s.video_asset);
      if (missing().length) {
        const job = await queueJob('stock_all', {prefetch_voice: true});  // the voice is recorded during footage
        await applyJob(await waitJob('footage', job));
      }
      // Scenes without a usable stock clip become title cards rather than blocking the draft.
      const fallback = missing();
      fallback.forEach(s => {
        s.visual = 'graphic';
        s.graphic_title = (s.graphic_title || s.roman || 'Title').slice(0, 150);
      });
      if (fallback.length) StudioState.markDirty(true);
      return fallback.length ? `${fallback.length} scene(s) without a matching clip use a title card` : 'every scene has footage';
    },

    approve: async function() {
      project().scenes.forEach(s => { s.rights_reviewed = true; });
      StudioState.markDirty(true);
      await save();
      const row = StudioState.current;
      Projects.sync(await StudioApi.request('/projects/' + row.id + '/approve', 'POST', {revision: row.revision}));
      const report = await StudioApi.request('/projects/' + row.id + '/preflight');
      if (!report.ready) throw new Error(report.errors[0].message);
      return 'revision v' + StudioState.current.revision + ' approved';
    },

    voice: async function() {
      await waitJob('voice', await queueJob('voice'));
      return 'narration ready';
    },

    render: async function() {
      const job = await waitJob('render', await queueJob('render', {quality: 'draft'}));
      const seconds = job.result && job.result.duration;
      return 'draft ready (' + (!seconds ? 'video' : seconds >= 120 ? (seconds / 60).toFixed(1) + ' min' : seconds.toFixed(1) + ' s') + ')';
    },
  };

  // Approval records a review note; ask for it up front instead of failing after the AI steps.
  function ensureReviewNote() {
    const p = project();
    if (String(p.evidence_notes || '').trim()) return true;
    const note = prompt('Approval needs a short review note (for example where the facts come from, or that it is fiction).',
                        'Original script by me.');
    if (note === null || !note.trim()) return false;
    p.evidence_notes = note.trim();
    $('#textarea-evidence-notes').val(p.evidence_notes);
    StudioState.markDirty(true);
    return true;
  }

  async function start() {
    if (running || !StudioState.current) return;
    if (window.StudioCaptions && !StudioCaptions.confirmLeave('The draft would use the last saved captions. Continue without them?')) return;
    if (!ensureReviewNote()) return;
    const fmt = StudioState.format(), [dw, dh] = fmt.draft;
    if (!confirm(`Create a ${dw}×${dh} ${fmt.label} draft automatically?\n\nThis prepares every scene with Gemini, downloads matching stock `
        + 'clips, ticks the rights check on every scene, saves and approves, generates the Mohan narration and renders a draft. '
        + 'Text you already wrote is kept.' + (fmt.maxScenes > 30 ? '\n\nA long video takes a while (roughly an hour for 20 minutes); keep this page open.' : ''))) return;
    running = true;
    $('#btn-autopilot').prop('disabled', true);
    $('#autopilot-panel').prop('hidden', false);
    $('#autopilot-steps').html(STEPS.map(([key, label]) =>
      `<li data-step="${key}" data-state="waiting"><span>${esc(label)}</span> <small class="autopilot-detail"></small></li>`).join(''));
    status('Working… keep this page open. Each step also appears under Production jobs.');
    let current = null;
    try {
      for (const [key] of STEPS) {
        current = key;
        show(key, 'running', '');
        show(key, 'done', await run[key]());
      }
      status(`Your draft is ready on Preview & export. Watch it, then click "Render ${fmt.width}×${fmt.height} ${fmt.label}" for the final video.`);
      StudioState.setNotice('Draft video ready. Watch it on Preview & export.');
      if (window.StudioApp && StudioApp.switchTab) StudioApp.switchTab('export');
    } catch (err) {
      show(current, 'failed', err.message);
      status('Stopped: ' + err.message + ' Fix this, then click the button again; finished steps are reused.', true);
      StudioState.setNotice(err.message, true);
    } finally {
      running = false;
      $('#btn-autopilot').prop('disabled', false);
    }
  }

  /** Complete every scene with one AI job (Telugu narration, captions, stock words, headings, key
   *  words) and apply it to the open project; used by "Split into scenes". */
  async function prepareScenes() {
    if (running) throw new Error('The one-click draft is already running.');
    running = true;
    try {
      const job = await queueJob('draft', {fill: true});
      await applyJob(await waitJob('prepare', job));
    } finally {
      running = false;
    }
  }

  window.StudioAutopilot = {
    STEPS, start, run, prepareScenes,
    isRunning: () => running,
    setPollDelay: ms => { pollDelay = ms; },
    init: function() {
      $(document).on('click', '#btn-autopilot', start);
    }
  };
})(jQuery);
