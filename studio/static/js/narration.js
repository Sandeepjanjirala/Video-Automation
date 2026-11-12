/**
 * Narration on the Voice tab: the voice engine (free Mohan or Sarvam), one recording for the whole
 * video, or one recording per scene.
 *
 * A recording is never changed. Scene boundaries, trims and volume are settings: the renderer cuts and
 * levels a copy (studio/narration.py), so they can be changed again at any time. One recording for the
 * whole video plays continuously; each scene is the part from its start to the next scene's start.
 */
(function($) {
  'use strict';

  const analyses = {};        // asset id -> waveform, pauses, loudness and warnings (GET /assets/<id>/narration)
  const loading = {};
  let choice = null;          // 'voice' | 'scenes' | 'track' as last chosen for the open project
  let choiceFor = null;
  let zoom = 0;               // waveform pixels per second; 0 = the whole recording fits the box
  let drag = null;            // {k, start} while a scene boundary is being dragged
  let stopAt = null;          // the scene player stops here

  function project() { return StudioState.current && StudioState.current.project; }
  function esc(v) { return $('<div>').text(v == null ? '' : String(v)).html(); }
  function secs(t) { return t == null || isNaN(t) ? '–' : Number(t).toFixed(2) + ' s'; }
  function round(t) { return Math.round(Number(t) * 1000) / 1000; }

  function analysis(aid) {
    if (!aid) return Promise.resolve(null);
    if (analyses[aid]) return Promise.resolve(analyses[aid]);
    if (!loading[aid]) {
      loading[aid] = StudioApi.request('/assets/' + aid + '/narration')
        .then(function(result) { analyses[aid] = result; return result; })
        .finally(function() { delete loading[aid]; });
    }
    return loading[aid];
  }

  function mode(p) {
    if (choiceFor !== (StudioState.current && StudioState.current.id)) {
      choiceFor = StudioState.current && StudioState.current.id;
      choice = null;
    }
    if (p.narration_mode === 'track') return 'track';
    if (choice === 'scenes' || (choice !== 'voice' && (p.scenes || []).some(s => s.audio_asset))) return 'scenes';
    return 'voice';
  }

  function changed(refresh) {
    StudioState.markDirty(true);
    Projects.triggerAutosave();
    Projects.checkPreflight();
    if (refresh !== false) Narration.render();
  }

  function track(p) {
    p.narration_track = p.narration_track || {asset: '', end: 0, gain_db: 0};
    return p.narration_track;
  }

  // Where each scene's part of the whole-video recording starts and ends (null while not placed).
  function ranges(p, duration) {
    const tr = track(p);
    const end = tr.end > 0 ? Math.min(tr.end, duration || tr.end) : duration;
    return (p.scenes || []).map(function(s, k) {
      const next = p.scenes[k + 1];
      return {start: s.track_start, end: next ? next.track_start : end};
    });
  }

  function textOf(s) { return String(s.roman || s.narration || '').trim(); }

  // ---- Waveform -------------------------------------------------------------------------------------

  function canvasInfo() {
    const p = project();
    const a = p && analyses[track(p).asset];
    const canvas = document.getElementById('narration-wave');
    if (!a || !canvas) return null;
    const box = document.getElementById('narration-wave-box');
    const width = Math.max(320, zoom ? a.duration * zoom : (box.clientWidth || 720));
    return {p, a, canvas, width, height: 132, pps: width / a.duration};
  }

  function drawWave() {
    const info = canvasInfo();
    if (!info) return;
    const {p, a, canvas, width, height, pps} = info;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext && canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const mid = height / 2 + 8;
    // Pauses are shaded: scene boundaries belong in them.
    ctx.fillStyle = 'rgba(41, 98, 255, 0.07)';
    (a.pauses || []).forEach(pz => ctx.fillRect(pz[0] * pps, 16, (pz[1] - pz[0]) * pps, height - 16));
    // The waveform: the loudest moment under each pixel.
    ctx.fillStyle = '#5b6b7f';
    const per = a.peaks_per_second;
    for (let x = 0; x < width; x++) {
      const i0 = Math.floor(x / pps * per), i1 = Math.max(i0 + 1, Math.floor((x + 1) / pps * per));
      let top = 0;
      for (let i = i0; i < i1 && i < a.peaks.length; i++) top = Math.max(top, a.peaks[i]);
      const h = Math.max(1, top / 100 * (height / 2 - 12));
      ctx.fillRect(x, mid - h, 1, h * 2);
    }
    // After the narration's end the recording is not used.
    const tr = track(p);
    if (tr.end > 0 && tr.end < a.duration) {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.18)';
      ctx.fillRect(tr.end * pps, 16, (a.duration - tr.end) * pps, height - 16);
    }
    // Scene boundaries, numbered.
    ctx.font = '11px sans-serif';
    (p.scenes || []).forEach(function(s, k) {
      if (s.track_start == null) return;
      const x = s.track_start * pps;
      ctx.fillStyle = drag && drag.k === k ? '#d93025' : '#0b57d0';
      ctx.fillRect(x - 1, 14, 2, height - 14);
      ctx.fillRect(x - 1, 0, 18, 14);
      ctx.fillStyle = '#fff';
      ctx.fillText(String(k + 1), x + 2, 11);
    });
    const audio = document.getElementById('narration-track-audio');
    if (audio && !isNaN(audio.currentTime)) {
      ctx.fillStyle = '#e8710a';
      ctx.fillRect(audio.currentTime * pps - 1, 14, 2, height - 14);
    }
  }

  function timeAt(event, info) {
    const rect = info.canvas.getBoundingClientRect();
    return Math.max(0, Math.min(info.a.duration, (event.clientX - rect.left) / info.pps));
  }

  function setStart(k, t) {
    const p = project();
    const scenes = p.scenes;
    const prev = k > 0 ? scenes[k - 1].track_start : null;
    const next = k + 1 < scenes.length ? scenes[k + 1].track_start : null;
    let value = round(t);
    if (prev != null) value = Math.max(value, round(prev + 0.3));
    if (next != null) value = Math.min(value, round(next - 0.3));
    scenes[k].track_start = Math.max(0, value);
  }

  // ---- Rendering --------------------------------------------------------------------------------------

  function renderTrack(p) {
    const tr = track(p);
    const $box = $('#narration-track-box');
    $box.find('.track-has-recording').prop('hidden', !tr.asset);
    $box.find('.track-no-recording').prop('hidden', !!tr.asset);
    if (!tr.asset) return;
    const audio = document.getElementById('narration-track-audio');
    const src = '/api/assets/' + tr.asset + '/media';
    if (audio && audio.getAttribute('src') !== src) audio.setAttribute('src', src);
    $('#range-track-gain').val(tr.gain_db || 0);
    $('#label-track-gain').text((tr.gain_db > 0 ? '+' : '') + (tr.gain_db || 0) + ' dB');
    $('#input-track-end').val(tr.end > 0 ? tr.end : '');
    const a = analyses[tr.asset];
    if (!a) {
      $('#narration-track-info').text('Reading the recording…');
      analysis(tr.asset).then(() => Narration.render()).catch(function(err) {
        $('#narration-track-info').text(err.message);
      });
      return;
    }
    $('#narration-track-info').html(`<b>${esc(a.name || 'Recording')}</b> · ${secs(a.duration)} · `
      + `${a.sample_rate ? (a.sample_rate / 1000).toFixed(1) + ' kHz' : ''} ${a.channels === 1 ? 'mono' : a.channels ? 'stereo' : ''}`
      + (a.loudness != null ? ` · loudness ${a.loudness.toFixed(1)} LUFS (levelled to about -16 in the video)` : ''));
    $('#narration-track-warnings').html((a.warnings || []).map(w => `<li>${esc(w)}</li>`).join(''));
    const rows = ranges(p, a.duration).map(function(r, k) {
      const s = p.scenes[k];
      const length = r.start != null && r.end != null ? r.end - r.start : null;
      const bad = r.start == null || (length != null && length < 0.3);
      return `<tr data-k="${k}" class="${bad ? 'track-row-bad' : ''}">
        <td>${k + 1}</td>
        <td class="track-text">${esc(textOf(s).slice(0, 70) || '(no text yet)')}</td>
        <td><input type="number" class="track-start-input" min="0" step="0.01" aria-label="Scene ${k + 1} starts at (seconds)"
              value="${r.start == null ? '' : r.start}" placeholder="not placed"></td>
        <td><button type="button" class="ghost btn-track-set-start" title="Start this scene where the player is now">at playhead</button></td>
        <td class="track-length">${length == null ? '–' : secs(length)}</td>
        <td><button type="button" class="ghost btn-track-play-scene" aria-label="Play scene ${k + 1}">&#9654;</button></td>
      </tr>`;
    }).join('');
    $('#narration-track-scenes tbody').html(rows);
    const placed = p.scenes.filter(s => s.track_start != null).length;
    $('#narration-track-status').text(placed === p.scenes.length
      ? `All ${placed} scene(s) placed. Drag the blue numbered lines on the waveform, or type times, to adjust.`
      : `${p.scenes.length - placed} of ${p.scenes.length} scene(s) not placed yet: use "Place scenes by their text" or set each start.`);
    drawWave();
  }

  function renderScenes(p) {
    const rows = (p.scenes || []).map(function(s, k) {
      const a = s.audio_asset ? analyses[s.audio_asset] : null;
      if (s.audio_asset && !a) analysis(s.audio_asset).then(() => Narration.render()).catch(() => {});
      const stale = s.audio_asset && s.__textHash && s.audio_text_hash !== s.__textHash;
      const recording = s.audio_asset
        ? `<audio class="scene-recording-audio" controls preload="none" src="/api/assets/${s.audio_asset}/media"></audio>
           <span class="muted">${a ? secs(a.duration) : ''}</span>`
        : '<span class="muted">No recording: the voice engine reads this scene.</span>';
      const edits = s.audio_asset ? `
        <div class="rowtools">
          <label>Use from <input type="number" class="scene-audio-trim" data-field="audio_trim_start" min="0" step="0.01" value="${s.audio_trim_start || 0}"> s</label>
          <label>to <input type="number" class="scene-audio-trim" data-field="audio_trim_end" min="0" step="0.01" value="${s.audio_trim_end || ''}" placeholder="end"> s</label>
          <button type="button" class="ghost btn-scene-audio-play-part" data-k="${k}">&#9654; Play the part used</button>
          <label>Volume <select class="scene-audio-gain">${[-12, -9, -6, -3, 0, 3, 6, 9, 12].map(v =>
            `<option value="${v}"${Number(s.audio_gain_db || 0) === v ? ' selected' : ''}>${v > 0 ? '+' : ''}${v} dB</option>`).join('')}</select></label>
          <button type="button" class="ghost btn-remove-scene-audio" data-scene-id="${s.id}">Remove recording</button>
          ${stale ? `<button type="button" class="btn-confirm-scene-audio" data-scene-id="${s.id}">Text matches the recording</button>` : ''}
        </div>
        ${stale ? '<p class="muted warn">The text was edited after this recording was attached. Check the captions match what is said, then confirm.</p>' : ''}` : '';
      return `<div class="scene-recording-row" data-k="${k}" data-scene-id="${s.id}">
        <div class="scene-recording-head"><b>Scene ${k + 1}</b> <span class="muted">${esc(textOf(s).slice(0, 80))}</span></div>
        <div class="rowtools">
          <label class="filebutton ghost">${s.audio_asset ? 'Replace recording' : 'Upload recording'}<input type="file" class="scene-upload-audio-input" data-scene-id="${s.id}" accept="audio/*,.m4a,.mp3,.wav,.ogg,.opus,.flac,.aac,.webm"></label>
          ${recording}
        </div>
        ${edits}
      </div>`;
    }).join('');
    $('#narration-scenes-list').html(rows);
  }

  // A scene's stored text hash (audio_text_hash) is compared with its current narration's hash.
  function refreshHashes(p) {
    (p.scenes || []).forEach(function(s) {
      if (!s.audio_asset) return;
      Media.textHash(s.narration || '').then(function(hash) {
        if (s.__textHash !== hash) {
          Object.defineProperty(s, '__textHash', {value: hash, writable: true, configurable: true, enumerable: false});
          if (mode(project()) === 'scenes') renderScenes(project());
        }
      });
    });
  }

  const Narration = {
    render: function() {
      const p = project();
      if (!p) return;
      const current = mode(p);
      $(`input[name="narration-source"][value="${current}"]`).prop('checked', true);
      $('#narration-track-box').prop('hidden', current !== 'track');
      $('#narration-scenes-box').prop('hidden', current !== 'scenes');
      $('#voice-engine-panel').prop('hidden', current === 'track');
      $('#narration-mode-note').text(current === 'track'
        ? 'Your recording narrates the whole video; the voice engine is not used.'
        : current === 'scenes' ? 'Scenes with a recording use it; scenes without one are read by the voice engine below.'
        : 'The voice engine reads every scene.');
      if (current === 'track') renderTrack(p);
      if (current === 'scenes') { renderScenes(p); refreshHashes(p); }
      if (window.StudioVoice && StudioVoice.renderCapabilitiesStrip) StudioVoice.renderCapabilitiesStrip();  // the voice note
    },

    analysis: analysis,
    ranges: ranges,

    setMode: function(value) {
      const p = project();
      if (!p) return;
      choice = value;
      choiceFor = StudioState.current.id;
      const wasTrack = p.narration_mode === 'track';
      p.narration_mode = value === 'track' ? 'track' : 'voice';
      if (wasTrack !== (p.narration_mode === 'track')) changed(false);
      Narration.render();
      if (value === 'track' && track(p).asset && p.scenes.some(s => s.track_start == null)) Narration.placeByText(false);
    },

    uploadTrack: function(file) {
      const p = project();
      if (!p || !file) return Promise.resolve();
      const replacing = !!track(p).asset;
      StudioState.setBusy(true);
      StudioState.setNotice('Uploading and checking your recording…');
      return Media.uploadFile(file, 'audio', 'narration')
        .then(function(asset) { return analysis(asset.id).then(() => asset); })
        .then(function(asset) {
          const tr = track(p);
          const previous = p.scenes.map(s => s.track_start);
          tr.asset = asset.id;
          tr.end = 0;
          tr.gain_db = 0;
          p.narration_mode = 'track';
          choice = 'track';
          changed(false);
          StudioState.setNotice(replacing
            ? 'New recording uploaded. Scene boundaries were placed again for it; your old ones can be restored below.'
            : 'Recording uploaded. Scenes were placed on it by their text; listen and adjust the boundaries.');
          Narration.lastStarts = replacing ? previous : null;
          $('#btn-track-restore').prop('hidden', !replacing);
          return Narration.placeByText(false);
        })
        .catch(function(err) { StudioState.setNotice(err.message, true); })
        .finally(function() { StudioState.setBusy(false); Narration.render(); });
    },

    placeByText: function(ask) {
      const p = project();
      const tr = track(p);
      if (!tr.asset) return Promise.resolve();
      if (ask && p.scenes.some(s => s.track_start != null) && !confirm('Replace the current scene boundaries with new estimates?')) {
        return Promise.resolve();
      }
      return StudioApi.request('/assets/' + tr.asset + '/narration', 'POST',
        {action: 'place', texts: p.scenes.map(textOf), end: tr.end || null})
        .then(function(res) {
          p.scenes.forEach((s, k) => { s.track_start = res.starts[k]; });
          changed();
          if (ask) StudioState.setNotice('Scenes placed by the length of their text, at the nearest pauses. Play each scene to check it.');
        })
        .catch(function(err) { StudioState.setNotice(err.message, true); });
    },

    splitAtPauses: function() {
      const p = project();
      const tr = track(p);
      if (!tr.asset) return Promise.resolve();
      const written = p.scenes.some(s => textOf(s));
      if (written && !confirm('Your scenes already have text; "Place scenes by their text" fits them to the recording. '
          + 'Replace them with new empty scenes at the pauses instead? Their text and footage are removed.')) {
        return Promise.resolve();
      }
      const fmt = StudioState.format();
      return StudioApi.request('/assets/' + tr.asset + '/narration', 'POST',
        {action: 'split', target: fmt.width > fmt.height ? 6 : 4, end: tr.end || null})
        .then(function(res) {
          if (res.starts.length > fmt.maxScenes) throw new Error(`That makes ${res.starts.length} scenes; ${fmt.label} allows ${fmt.maxScenes}. Choose YouTube video for long recordings.`);
          const kept = written ? [] : p.scenes;   // empty scenes keep their footage, in order
          p.scenes = res.starts.map(function(start, k) {
            return Object.assign(kept[k] || StudioState.createEmptyScene(), {track_start: start});
          });
          changed();
          Scenes.renderScenes();
          StudioState.setNotice(`${res.starts.length} scene(s) made at the pauses of your recording. Type what is said in each scene's boxes on the Story tab.`);
        })
        .catch(function(err) { StudioState.setNotice(err.message, true); });
    },

    playRange: function(audio, start, end) {
      if (!audio || start == null) return;
      stopAt = end;
      audio.currentTime = start;
      const playing = audio.play();
      if (playing && playing.catch) playing.catch(() => {});
    },

    init: function() {
      $(document).on('change', 'input[name="narration-source"]', function() { Narration.setMode(this.value); });
      $(document).on('change', '#input-narration-track, #input-narration-track-replace', function(e) {
        const file = e.target.files && e.target.files[0];
        Narration.uploadTrack(file).finally(() => { e.target.value = ''; });
      });
      $(document).on('click', '#btn-track-remove', function() {
        const p = project();
        if (!p || !confirm('Remove the recording from this project? (It stays in your media library.)')) return;
        track(p).asset = '';
        changed();
      });
      $(document).on('click', '#btn-track-restore', function() {
        const p = project();
        if (!p || !Narration.lastStarts) return;
        p.scenes.forEach((s, k) => { s.track_start = Narration.lastStarts[k] === undefined ? null : Narration.lastStarts[k]; });
        Narration.lastStarts = null;
        $(this).prop('hidden', true);
        changed();
      });
      $(document).on('click', '#btn-track-place', function() { Narration.placeByText(true); });
      $(document).on('click', '#btn-track-split', function() { Narration.splitAtPauses(); });
      $(document).on('change', '.track-start-input', function() {
        const k = Number($(this).closest('tr').data('k'));
        const raw = String($(this).val()).trim();
        if (raw === '') project().scenes[k].track_start = null;
        else setStart(k, Number(raw));
        changed();
      });
      $(document).on('click', '.btn-track-set-start', function() {
        const audio = document.getElementById('narration-track-audio');
        setStart(Number($(this).closest('tr').data('k')), audio ? audio.currentTime : 0);
        changed();
      });
      $(document).on('click', '.btn-track-play-scene', function() {
        const p = project();
        const a = analyses[track(p).asset];
        const r = ranges(p, a ? a.duration : null)[Number($(this).closest('tr').data('k'))];
        Narration.playRange(document.getElementById('narration-track-audio'), r.start, r.end);
      });
      $(document).on('change', '#input-track-end', function() {
        const value = Number($(this).val());
        track(project()).end = value > 0 ? round(value) : 0;
        changed();
      });
      $(document).on('click', '#btn-track-end-playhead', function() {
        const audio = document.getElementById('narration-track-audio');
        if (audio) { track(project()).end = round(audio.currentTime); changed(); }
      });
      $(document).on('click', '#btn-track-end-full', function() { track(project()).end = 0; changed(); });
      $(document).on('input change', '#range-track-gain', function(e) {
        track(project()).gain_db = Number($(this).val());
        $('#label-track-gain').text((this.value > 0 ? '+' : '') + this.value + ' dB');
        if (e.type === 'change') changed(false);
      });
      $(document).on('click', '#btn-wave-zoom-in, #btn-wave-zoom-out, #btn-wave-zoom-fit', function() {
        const info = canvasInfo();
        if (!info) return;
        if (this.id === 'btn-wave-zoom-fit') zoom = 0;
        else zoom = Math.max(5, Math.min(400, (zoom || info.pps) * (this.id === 'btn-wave-zoom-in' ? 2 : 0.5)));
        drawWave();
      });
      // The waveform: click to listen from there; drag a numbered line to move that scene's start.
      $(document).on('pointerdown', '#narration-wave', function(e) {
        const info = canvasInfo();
        if (!info) return;
        const t = timeAt(e, info);
        const near = info.p.scenes.map((s, k) => ({k, d: s.track_start == null ? Infinity : Math.abs(s.track_start - t) * info.pps}))
          .sort((x, y) => x.d - y.d)[0];
        if (near && near.d <= 8) {
          drag = {k: near.k};
          if (this.setPointerCapture) this.setPointerCapture(e.pointerId);
        } else {
          const audio = document.getElementById('narration-track-audio');
          if (audio) { stopAt = null; audio.currentTime = t; }
        }
        drawWave();
      });
      $(document).on('pointermove', '#narration-wave', function(e) {
        if (!drag) return;
        const info = canvasInfo();
        if (!info) return;
        setStart(drag.k, timeAt(e, info));
        drawWave();
      });
      $(document).on('pointerup pointercancel', '#narration-wave', function() {
        if (!drag) return;
        drag = null;
        changed();
      });
      $(document).on('timeupdate seeked', '#narration-track-audio, .scene-recording-audio', function() {
        if (stopAt != null && this.currentTime >= stopAt) { this.pause(); stopAt = null; }
        if (this.id === 'narration-track-audio') drawWave();
      });
      $(window).on('resize', function() { if (!zoom) drawWave(); });

      // One recording per scene.
      $(document).on('change', '.scene-audio-trim', function() {
        const sid = $(this).closest('.scene-recording-row').data('scene-id');
        const value = Math.max(0, Number($(this).val()) || 0);
        Scenes.updateSceneField(sid, {[$(this).data('field')]: round(value)});
        Narration.render();
      });
      $(document).on('change', '.scene-audio-gain', function() {
        Scenes.updateSceneField($(this).closest('.scene-recording-row').data('scene-id'), {audio_gain_db: Number($(this).val())});
      });
      $(document).on('click', '.btn-scene-audio-play-part', function() {
        const $row = $(this).closest('.scene-recording-row');
        const s = project().scenes[Number($row.data('k'))];
        Narration.playRange($row.find('audio')[0], Number(s.audio_trim_start || 0), Number(s.audio_trim_end || 0) || null);
      });
      $(document).on('click', '.btn-confirm-scene-audio', function() {
        const sid = $(this).data('scene-id');
        const s = project().scenes.find(x => x.id === sid);
        Media.textHash(s.narration || '').then(function(hash) {
          Scenes.updateSceneField(sid, {audio_text_hash: hash});
          Narration.render();
          StudioState.setNotice('Recording confirmed for the edited text. Save to keep it.');
        });
      });
      $(document).on('studio:editor-populated', function() { Narration.render(); });
    }
  };

  window.StudioNarration = Narration;
})(jQuery);
