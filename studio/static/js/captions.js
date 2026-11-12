/**
 * Telugu Reel Studio - Caption Editor (Stage 2)
 *
 * Edits are per-scene drafts of the raw input strings until "Save captions", so an
 * incomplete timestamp never reaches the project. Times are seconds from the start
 * of the scene's narration, measured by /caption-timing from the audio the renderer
 * will use. Saving writes scene.cues, which the shared timeline consumes unchanged.
 */

(function($) {
  'use strict';

  const END_TOLERANCE = 0.1;  // Matches media.captions and timeline.compute_timeline.
  const SHORT_CUE = 0.3;
  const MAX_CUES = 200;
  const MAX_TEXT = 160;
  const TELUGU = /[ఀ-౿]/;

  let projectId = null;
  let active = false;
  let drafts = {};      // sid -> {cues: [{start, end, text}] (strings), base: JSON of the starting cues}
  let timing = {};      // sid -> measurement from the server
  let timingSeq = 0;
  let voiceJobsSeen = '';
  const stopAt = {};    // sid -> end of the caption being auditioned

  const esc = v => $('<div>').text(v == null ? '' : String(v)).html().replace(/"/g, '&quot;');
  const fmt = n => (Math.round(n * 100) / 100).toFixed(2);
  const round3 = n => Math.round(n * 1000) / 1000;

  function parseTime(raw) {
    const m = String(raw == null ? '' : raw).trim().match(/^(?:(\d+):)?(\d+(?:[.,]\d+)?)$/);
    if (!m) return NaN;
    const seconds = parseFloat(m[2].replace(',', '.'));
    if (m[1] && seconds >= 60) return NaN;
    return (m[1] ? +m[1] * 60 : 0) + seconds;
  }

  function toDraftCues(cues) {
    return (cues || []).map(c => ({start: fmt(c.start), end: fmt(c.end), text: c.text}));
  }

  /** Returns per-caption and scene issues; errors block saving, warnings do not. */
  function validate(cues, duration) {
    const rows = cues.map(() => []);
    const scene = [];
    let prevEnd = NaN;
    cues.forEach((c, i) => {
      const start = parseTime(c.start), end = parseTime(c.end);
      const err = message => rows[i].push({level: 'error', message});
      const warn = message => rows[i].push({level: 'warning', message});
      if (isNaN(start)) err('Start is not a valid time. Use seconds, for example 1.25.');
      if (isNaN(end)) err('End is not a valid time. Use seconds, for example 2.50.');
      if (!isNaN(start) && !isNaN(end)) {
        if (end <= start) err('End must be later than start.');
        else if (end - start < SHORT_CUE) warn(`Very short (${fmt(end - start)} s). Viewers may not have time to read it.`);
      }
      if (duration != null) {
        if (start >= duration) err(`Starts after the narration ends at ${fmt(duration)} s.`);
        else if (end > duration + END_TOLERANCE) err(`Ends after the narration ends at ${fmt(duration)} s.`);
      }
      if (start < prevEnd - 1e-9) {
        err(`Overlaps caption ${i} by ${fmt(prevEnd - start)} s. Start at ${fmt(prevEnd)} or later.`);
      }
      const text = String(c.text || '');
      if (!text.trim()) err('Caption text is empty.');
      else if (text.length > MAX_TEXT) err(`Text has ${text.length} characters; the limit is ${MAX_TEXT}.`);
      prevEnd = end;
    });
    if (cues.length > MAX_CUES) scene.push({level: 'error', message: `A scene supports at most ${MAX_CUES} captions.`});
    if (duration == null && cues.length) {
      scene.push({level: 'warning', message: 'Timings are not checked against audio yet. Prepare narration to measure this scene.'});
    }
    const all = rows.flat().concat(scene);
    return {rows, scene, errors: all.filter(x => x.level === 'error').length,
            warnings: all.filter(x => x.level === 'warning').length};
  }

  /** Splits at `at` seconds when inside the caption, otherwise at its midpoint. */
  function splitCue(cues, i, at) {
    const c = cues[i], start = parseTime(c.start), end = parseTime(c.end);
    if (!(end > start)) throw new Error('Fix this caption\'s start and end before splitting it.');
    if (end - start < 0.2) throw new Error('This caption is too short to split.');
    if (cues.length >= MAX_CUES) throw new Error(`A scene supports at most ${MAX_CUES} captions.`);
    const words = String(c.text || '').trim().split(/\s+/).filter(Boolean);
    if (words.length < 2) throw new Error('A caption needs at least two words to split.');
    const t = (at > start + 0.05 && at < end - 0.05) ? at : (start + end) / 2;
    const k = Math.min(words.length - 1, Math.max(1, Math.round(words.length * (t - start) / (end - start))));
    return cues.slice(0, i).concat([
      {start: c.start, end: fmt(t), text: words.slice(0, k).join(' ')},
      {start: fmt(t), end: c.end, text: words.slice(k).join(' ')}
    ], cues.slice(i + 1));
  }

  function mergeCue(cues, i) {
    if (i + 1 >= cues.length) throw new Error('There is no following caption to merge with.');
    const a = cues[i], b = cues[i + 1];
    const starts = [parseTime(a.start), parseTime(b.start)], ends = [parseTime(a.end), parseTime(b.end)];
    if (starts.concat(ends).some(isNaN)) throw new Error('Fix both captions\' times before merging them.');
    const merged = {start: fmt(Math.min(...starts)), end: fmt(Math.max(...ends)),
                    text: [a.text, b.text].map(t => String(t || '').trim()).filter(Boolean).join(' ')};
    return cues.slice(0, i).concat([merged], cues.slice(i + 2));
  }

  function scenes() {
    return (StudioState.current && StudioState.current.project && StudioState.current.project.scenes) || [];
  }

  function measured(sid) {
    const t = timing[sid];
    return t && t.status === 'measured' ? t : null;
  }

  function automaticRoman(sid) {
    const t = measured(sid);
    return t ? toDraftCues(t.automatic.roman.cues) : null;
  }

  function isDirty(sid) {
    const d = drafts[sid];
    return !!d && JSON.stringify(d.cues) !== d.base;
  }

  function hasUnsaved() {
    return scenes().some(s => isDirty(s.id));
  }

  /** Clean drafts follow the project; dirty drafts are kept until saved or discarded. */
  function syncDrafts() {
    const ids = new Set(scenes().map(s => s.id));
    Object.keys(drafts).forEach(sid => { if (!ids.has(sid)) delete drafts[sid]; });
    scenes().forEach(s => {
      if (isDirty(s.id)) return;
      const manual = s.cues && s.cues.length;
      const auto = automaticRoman(s.id);
      const cues = manual ? toDraftCues(s.cues) : (auto || []);
      drafts[s.id] = {cues, base: JSON.stringify(cues), origin: manual ? 'manual' : auto ? 'automatic' : 'none'};
    });
  }

  function issueHtml(issues) {
    return issues.map(x => `<div class="cueissue ${x.level}">${x.level === 'error' ? '✕' : '⚠'} ${esc(x.message)}</div>`).join('');
  }

  function rowHtml(c, i, count) {
    const lang = TELUGU.test(c.text || '') ? 'te' : 'en';
    return `
      <div class="cuerow" data-index="${i}">
        <span class="cueindex">${i + 1}</span>
        <label class="cuefield"><span>Start</span><input type="text" inputmode="decimal" class="cue-input" data-field="start" value="${esc(c.start)}" aria-label="Caption ${i + 1} start (seconds)"></label>
        <label class="cuefield"><span>End</span><input type="text" inputmode="decimal" class="cue-input" data-field="end" value="${esc(c.end)}" aria-label="Caption ${i + 1} end (seconds)"></label>
        <label class="cuefield cuetext"><span>Text</span><input type="text" class="cue-input" data-field="text" lang="${lang}" value="${esc(c.text)}" aria-label="Caption ${i + 1} text"></label>
        <div class="cuetools">
          <button type="button" class="btn-cue-play needs-audio">&#9654; Play</button>
          <button type="button" class="btn-cue-set needs-audio" data-field="start">Start = playhead</button>
          <button type="button" class="btn-cue-set needs-audio" data-field="end">End = playhead</button>
          <button type="button" class="btn-cue-split">Split</button>
          <button type="button" class="btn-cue-merge"${i + 1 >= count ? ' disabled' : ''}>Merge with next</button>
          <button type="button" class="btn-cue-delete">Delete</button>
        </div>
        <div class="cue-issues"></div>
      </div>`;
  }

  function panelHtml(s, idx) {
    const t = timing[s.id], m = measured(s.id), d = drafts[s.id];
    const heading = (s.roman || s.narration || 'Untitled scene').slice(0, 70);
    const info = !t ? 'Checking narration audio…'
      : m ? `${m.source} · measured ${fmt(m.duration)} s`
      : t.message;
    const origin = d.origin === 'manual' ? 'Saved manual captions.'
      : d.origin === 'automatic' ? `Automatic timing (${m ? m.automatic.roman.alignment : 'estimated'}). Editing saves manual captions.`
      : 'No captions yet. Add Roman Telugu captions on the Story tab, or reset from the Telugu narration.';
    const stash = (StudioState.clearedCues || {})[s.id];
    return `
      <details class="panel caption-scene fold" data-id="${esc(s.id)}" data-fold="caption-${esc(s.id)}">
        <summary class="foldhead sectionhead">
          <div>
            <span class="eyebrow">SCENE ${String(idx + 1).padStart(2, '0')}</span>
            <h2>${esc(heading)}</h2>
          </div>
          <span class="badge caption-scene-status"></span>
        </summary>
        <p class="muted caption-source">${esc(info)}</p>
        <label class="field caption-emphasis-field"><span>Key words in the highlight colour</span>
          <input type="text" class="scene-emphasis-input" data-scene-id="${esc(s.id)}" maxlength="200"
                 value="${esc((s.emphasis || []).join(', '))}" placeholder="e.g. Octopus, hearts (numbers are always highlighted)">
        </label>
        ${m ? `<div class="captionplayer"><audio class="caption-audio" controls preload="metadata" data-key="${esc(s.id + '|' + m.audio_url)}" src="${esc(m.audio_url)}"></audio><span class="muted">Playhead <b class="caption-playhead">0.00</b> s</span></div>` : ''}
        <p class="muted">${esc(origin)}</p>
        ${stash ? `<div class="notice">Captions for this scene were cleared when its text or narration changed. <button type="button" class="btn-caption-restore">Restore previous captions</button></div>` : ''}
        <div class="cuelist">${d.cues.map((c, i) => rowHtml(c, i, d.cues.length)).join('')}</div>
        <div class="caption-scene-issues"></div>
        <div class="actions">
          <label class="caption-reset-label">Automatic timing from
            <select class="caption-reset-source">
              <option value="roman">Roman Telugu captions</option>
              <option value="telugu">Telugu narration</option>
            </select>
          </label>
          <button type="button" class="btn-caption-reset needs-audio">Reset to automatic timing</button>
          <button type="button" class="btn-caption-discard">Discard unsaved changes</button>
        </div>
      </details>`;
  }

  function stopAtEnd(sid, audio) {
    if (stopAt[sid] != null && audio.currentTime >= stopAt[sid]) {
      audio.pause();
      stopAt[sid] = null;
    }
  }

  /** timeupdate fires only every ~250 ms; poll while auditioning so playback stops on the caption's end. */
  function watchCueEnd(sid, audio) {
    clearInterval(audio.captionTimer);
    audio.captionTimer = setInterval(function() {
      stopAtEnd(sid, audio);
      if (stopAt[sid] == null || audio.paused) clearInterval(audio.captionTimer);
    }, 20);
  }

  function bindAudio(sid, audio) {
    $(audio).off('.captions').on('pause.captions', function() {
      stopAt[sid] = null;  // A manual pause ends the audition; native play then runs freely.
    }).on('timeupdate.captions seeked.captions', function() {
      const now = this.currentTime;
      stopAtEnd(sid, this);
      const $panel = $(this).closest('.caption-scene');
      $panel.find('.caption-playhead').text(fmt(now));
      $panel.find('.cuerow').each(function() {
        const c = drafts[sid] && drafts[sid].cues[$(this).data('index')];
        $(this).toggleClass('playing', !!c && parseTime(c.start) <= now && now < parseTime(c.end));
      });
    });
  }

  /** Re-renders one scene (or all), keeping audio elements so playback is not interrupted. */
  function render(onlySid) {
    const $box = $('#caption-scenes-container');
    if (!$box.length || !StudioState.current) return;
    syncDrafts();
    const players = {};
    $box.find('audio.caption-audio').each(function() { players[$(this).attr('data-key')] = this; });
    const list = scenes();
    if (onlySid) {
      const idx = list.findIndex(s => s.id === onlySid);
      if (idx < 0) return;
      const source = $box.find(`.caption-scene[data-id="${onlySid}"] .caption-reset-source`).val();
      $box.find(`.caption-scene[data-id="${onlySid}"]`).replaceWith(panelHtml(list[idx], idx));
      if (source) $box.find(`.caption-scene[data-id="${onlySid}"] .caption-reset-source`).val(source);
    } else {
      $box.html(list.map(panelHtml).join(''));
    }
    if (window.StudioFold) StudioFold.restore($box);  // keep scenes the user opened open
    $box.find('audio.caption-audio').each(function() {
      const old = players[$(this).attr('data-key')];
      if (old && old !== this) $(this).replaceWith(old);
    });
    $box.find('audio.caption-audio').each(function() {
      bindAudio(panelSid(this), this);
    });
    (onlySid ? [onlySid] : list.map(s => s.id)).forEach(refreshScene);
  }

  /** Updates issues, badges and button states without replacing inputs (keeps focus while typing). */
  function refreshScene(sid) {
    const $panel = $(`#caption-scenes-container .caption-scene[data-id="${sid}"]`);
    const d = drafts[sid];
    if (!$panel.length || !d) return;
    const m = measured(sid);
    const v = validate(d.cues, m ? m.duration : null);
    $panel.find('.cuerow').each(function(i) {
      $(this).toggleClass('invalid', v.rows[i].some(x => x.level === 'error'))
        .toggleClass('warned', v.rows[i].some(x => x.level === 'warning'))
        .find('.cue-issues').html(issueHtml(v.rows[i]));
      $(this).find('.btn-cue-merge').prop('disabled', StudioState.isBusy || i + 1 >= d.cues.length);
    });
    $panel.find('.caption-scene-issues').html(issueHtml(v.scene));
    const $badge = $panel.find('.caption-scene-status');
    if (v.errors) $badge.attr('class', 'badge red caption-scene-status').text(v.errors + ' problem(s)');
    else if (isDirty(sid)) $badge.attr('class', 'badge amber caption-scene-status').text('Unsaved changes');
    else if (m) $badge.attr('class', 'badge approved caption-scene-status').text('Measured audio');
    else $badge.attr('class', 'badge caption-scene-status').text('Not measured');
    $panel.find('.needs-audio').prop('disabled', StudioState.isBusy || !m);
    $panel.find('.btn-caption-discard').prop('disabled', StudioState.isBusy || !isDirty(sid));
    refreshSummary();
  }

  function refreshSummary() {
    const dirty = scenes().filter(s => isDirty(s.id)).length;
    $('#captions-unsaved-badge').attr('class', dirty ? 'badge amber' : 'badge')
      .text(dirty ? `Unsaved captions in ${dirty} scene(s)` : 'No unsaved captions');
    $('#btn-save-captions').prop('disabled', StudioState.isBusy || !dirty);
    const total = scenes().length, ready = scenes().filter(s => measured(s.id)).length;
    if (Object.keys(timing).length) {
      $('#captions-summary').attr('class', 'muted').text(`${ready} of ${total} scene(s) have measured narration audio.`);
    }
  }

  function loadTiming() {
    if (!StudioState.current) return Promise.resolve();
    const pid = StudioState.current.id, seq = ++timingSeq;
    $('#captions-summary').attr('class', 'muted').text('Measuring narration audio…');
    return StudioApi.request('/projects/' + pid + '/caption-timing', 'POST', {project: StudioState.current.project})
      .then(function(res) {
        if (seq !== timingSeq || !StudioState.current || StudioState.current.id !== pid) return;
        timing = {};
        ((res && res.scenes) || []).forEach(t => { timing[t.scene_id] = t; });
        render();
      })
      .catch(function(err) {
        if (seq !== timingSeq) return;
        $('#captions-summary').attr('class', 'cueissue error').text('Could not measure narration: ' + err.message);
      });
  }

  function editDraft(sid, mutate) {
    const d = drafts[sid];
    if (!d) return;
    try {
      d.cues = mutate(d.cues);
    } catch (e) {
      StudioState.setNotice(e.message, true);
      return;
    }
    render(sid);
  }

  function saveAll() {
    const dirty = scenes().filter(s => isDirty(s.id));
    if (!dirty.length) return Promise.resolve(null);
    for (const s of dirty) {
      const m = measured(s.id);
      const v = validate(drafts[s.id].cues, m ? m.duration : null);
      if (v.errors) {
        const n = scenes().indexOf(s) + 1;
        StudioState.setNotice(`Scene ${n}: fix ${v.errors} caption problem(s) before saving. Nothing was saved.`, true);
        $(`#caption-scenes-container .caption-scene[data-id="${s.id}"]`).prop('open', true);
        const $first = $(`#caption-scenes-container .caption-scene[data-id="${s.id}"] .cuerow.invalid input`).first();
        if ($first.length) $first.trigger('focus');
        return Promise.resolve(null);
      }
    }
    const previous = {}, wasDirty = StudioState.isDirty;
    dirty.forEach(s => {
      previous[s.id] = s.cues;
      const d = drafts[s.id], auto = automaticRoman(s.id);
      // Unedited automatic Roman timing stays automatic, keeping its alignment label honest.
      s.cues = auto && JSON.stringify(d.cues) === JSON.stringify(auto) ? [] : d.cues.map(c => ({
        start: round3(parseTime(c.start)), end: round3(parseTime(c.end)), text: String(c.text).trim()
      }));
    });
    StudioState.markDirty(true);
    return Projects.saveProject()
      .then(function(row) {
        dirty.forEach(s => { delete drafts[s.id]; });
        render();
        StudioState.setNotice(`Captions saved in revision v${row.revision}. Approve it before rendering.`);
        return row;
      })
      .catch(function() {
        // saveProject already reported the error; keep the drafts and the previous project captions.
        dirty.forEach(s => {
          const current = scenes().find(x => x.id === s.id);
          if (current) current.cues = previous[s.id];
        });
        StudioState.markDirty(wasDirty);
        return null;
      });
  }

  function confirmLeave(action) {
    return !hasUnsaved() || confirm(`You have unsaved caption edits. ${action}`);
  }

  function panelSid(el) {
    // attr, not data(): jQuery would turn an all-digit scene ID into a number.
    return $(el).closest('.caption-scene').attr('data-id');
  }

  function rowIndex(el) {
    return +$(el).closest('.cuerow').data('index');
  }

  function audioFor(sid) {
    return $(`#caption-scenes-container .caption-scene[data-id="${sid}"] audio.caption-audio`)[0];
  }

  const Captions = {
    parseTime, validate, splitCue, mergeCue, hasUnsaved, confirmLeave, saveAll, loadTiming, render,

    // Measured narration seconds, once the Captions tab has measured this scene.
    measuredDuration: function(sid) {
      const t = measured(sid);
      return t ? t.duration : null;
    },

    refreshControls: function() {
      scenes().forEach(s => refreshScene(s.id));
    },

    onTabChange: function(tab) {
      active = tab === 'captions';
      if (active) {
        render();
        loadTiming();
      }
    },

    init: function() {
      $(document).on('studio:project-synced', function() {
        if (!StudioState.current) return;
        if (StudioState.current.id !== projectId) {
          projectId = StudioState.current.id;
          drafts = {};
          timing = {};
          StudioState.clearedCues = {};
          if (active) loadTiming();
        }
      });
      $(document).on('studio:editor-populated', function() { render(); });
      $(document).on('studio:busy-ended', function() { Captions.refreshControls(); });

      // Prepared narration finishing changes what can be measured.
      StudioState.subscribe(function(key, jobs) {
        if (key !== 'jobs') return;
        const seen = (jobs || []).filter(j => j.kind === 'voice' && j.state === 'succeeded').map(j => j.id).join(',');
        if (seen === voiceJobsSeen) return;
        voiceJobsSeen = seen;
        if (active) loadTiming();
      });

      $(document).on('input', '#caption-scenes-container .cue-input', function() {
        const sid = panelSid(this), d = drafts[sid], field = $(this).data('field');
        if (!d) return;
        d.cues[rowIndex(this)][field] = $(this).val();
        if (field === 'text') $(this).attr('lang', TELUGU.test($(this).val()) ? 'te' : 'en');
        refreshScene(sid);
      });

      $(document).on('click', '#caption-scenes-container .btn-cue-play', function() {
        const sid = panelSid(this), audio = audioFor(sid), c = drafts[sid].cues[rowIndex(this)];
        const start = parseTime(c.start), end = parseTime(c.end);
        if (!audio) return;
        if (isNaN(start)) return StudioState.setNotice('Fix this caption\'s start time before playing it.', true);
        audio.currentTime = start;
        stopAt[sid] = end > start ? end : null;
        const playing = audio.play();
        if (stopAt[sid] != null) watchCueEnd(sid, audio);
        if (playing && playing.catch) playing.catch(e => StudioState.setNotice('Audio could not play: ' + e.message, true));
      });

      $(document).on('click', '#caption-scenes-container .btn-cue-set', function() {
        const sid = panelSid(this), audio = audioFor(sid), field = $(this).data('field');
        if (!audio) return;
        const i = rowIndex(this);
        drafts[sid].cues[i][field] = fmt(audio.currentTime);
        $(this).closest('.cuerow').find(`.cue-input[data-field="${field}"]`).val(drafts[sid].cues[i][field]);
        refreshScene(sid);
      });

      $(document).on('click', '#caption-scenes-container .btn-cue-split', function() {
        const sid = panelSid(this), i = rowIndex(this), audio = audioFor(sid);
        editDraft(sid, cues => splitCue(cues, i, audio ? audio.currentTime : NaN));
      });

      $(document).on('click', '#caption-scenes-container .btn-cue-merge', function() {
        const sid = panelSid(this), i = rowIndex(this);
        editDraft(sid, cues => mergeCue(cues, i));
      });

      $(document).on('click', '#caption-scenes-container .btn-cue-delete', function() {
        const sid = panelSid(this), i = rowIndex(this);
        editDraft(sid, cues => cues.filter((_, j) => j !== i));
      });

      $(document).on('click', '#caption-scenes-container .btn-caption-reset', function() {
        const sid = panelSid(this), m = measured(sid), d = drafts[sid];
        const source = $(this).closest('.caption-scene').find('.caption-reset-source').val();
        if (!m || !d) return;
        const cues = toDraftCues(m.automatic[source].cues);
        if (!cues.length) return StudioState.setNotice('That text is empty, so there is nothing to time.', true);
        const changed = JSON.stringify(cues) !== JSON.stringify(d.cues);
        if (changed && d.cues.length && !confirm('Replace this scene\'s captions with automatic timing from the measured audio?')) return;
        editDraft(sid, () => cues);
      });

      $(document).on('click', '#caption-scenes-container .btn-caption-discard', function() {
        const sid = panelSid(this);
        if (!isDirty(sid) || !confirm('Discard unsaved caption changes for this scene?')) return;
        editDraft(sid, () => JSON.parse(drafts[sid].base));
      });

      $(document).on('click', '#caption-scenes-container .btn-caption-restore', function() {
        const sid = panelSid(this), stash = (StudioState.clearedCues || {})[sid];
        if (!stash) return;
        delete StudioState.clearedCues[sid];
        editDraft(sid, () => toDraftCues(stash));
        StudioState.setNotice('Previous captions restored. Check them against the new narration, then save.');
      });

      $(document).on('click', '#btn-save-captions', function() { saveAll(); });
      $(document).on('click', '#btn-reload-caption-timing', function() { loadTiming(); });

      $(document).on('click', '.btn-open-captions', function() {
        const sid = $(this).attr('data-scene-id');
        if (window.StudioApp) StudioApp.switchTab('captions');
        const el = $(`#caption-scenes-container .caption-scene[data-id="${sid}"]`)[0];
        if (el) el.open = true;
        if (el && el.scrollIntoView) el.scrollIntoView({block: 'start'});
      });
    }
  };

  window.StudioCaptions = Captions;
})(jQuery);
