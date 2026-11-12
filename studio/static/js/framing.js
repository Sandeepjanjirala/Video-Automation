/**
 * Telugu Reel Studio - Framing preview (Stage 3)
 *
 * Shows how each scene's video or image sits in the frame: 1080x1920 for a reel, 1920x1080 for a
 * YouTube video (StudioState.FORMATS). `geometry`
 * mirrors studio/framing.py operation for operation, so the live preview crops
 * and pads at the renderer's exact pixel offsets. "Render exact frame" asks
 * FFmpeg for the real frame using the export's filters and caption style.
 */
(function($) {
  'use strict';

  const FRAME = 1 / 30;
  const ASPECTS = {'full': null, '4:5': [4, 5], '1:1': [1, 1], '16:9': [16, 9]};
  // Output frame of the open project's format.
  const dims = () => [StudioState.format().width, StudioState.format().height];
  const aspectLabels = () => [['full', StudioState.format().width > StudioState.format().height ? 'Full frame 16:9' : 'Full vertical 9:16'],
                              ['4:5', 'Portrait 4:5'], ['1:1', 'Square 1:1'], ['16:9', 'Widescreen 16:9']];
  const RESET = {fit: 'crop', frame_aspect: 'full', zoom: 1, crop_x: 0.5, crop_y: 0.5};
  const esc = value => $('<div>').text(value == null ? '' : value).html().replace(/"/g, '&quot;');

  const infos = {};   // asset id -> Promise of /assets/<id>/framing
  const loaded = {};  // asset id -> resolved info
  const exactSeq = {};
  const timers = {};
  const drag = {active: null};

  function even(v) {
    return Math.floor(v / 2 + 0.5) * 2;
  }

  function frameWindow(width, height, aspect) {
    const ratio = ASPECTS[aspect];
    if (!ratio) return [0, 0, width, height];
    if (width * ratio[1] > height * ratio[0]) {
      const w = Math.min(width, even(height * ratio[0] / ratio[1]));
      return [even((width - w) / 2), 0, w, height];
    }
    const h = Math.min(height, even(width * ratio[1] / ratio[0]));
    return [0, even((height - h) / 2), width, h];
  }

  function geometry(srcW, srcH, width, height, fit, aspect, zoom, x, y) {
    const [wx, wy, ww, wh] = frameWindow(width, height, aspect);
    let base;
    if (fit === 'crop') base = Math.max(ww / srcW, wh / srcH);
    else base = Math.min(ww / srcW, wh / srcH);
    const scale = base * zoom;
    const sw = Math.max(2, even(srcW * scale));
    const sh = Math.max(2, even(srcH * scale));

    function axis(scaled, room, pos, origin) {
      if (scaled > room) return [room, even((scaled - room) * pos), origin];
      return [scaled, 0, origin + even((room - scaled) / 2)];
    }

    const [cw, cx, px] = axis(sw, ww, x, wx);
    const [ch, cy, py] = axis(sh, wh, y, wy);
    return {width: width, height: height, window: [wx, wy, ww, wh],
            scale_w: sw, scale_h: sh, crop_w: cw, crop_h: ch,
            crop_x: cx, crop_y: cy, pad_x: px, pad_y: py};
  }

  function num(value, fallback) {
    const n = Number(value);
    return value === '' || value == null || !Number.isFinite(n) ? fallback : n;
  }

  function framingOf(s) {
    return {fit: s.fit === 'contain' ? 'contain' : 'crop', frame_aspect: ASPECTS.hasOwnProperty(s.frame_aspect) ? s.frame_aspect : 'full',
            zoom: num(s.zoom, 1), crop_x: num(s.crop_x, 0.5), crop_y: num(s.crop_y, 0.5)};
  }

  function sceneGeometry(s, source, width, height) {
    const f = framingOf(s);
    const [W, H] = dims();
    return geometry(source.width, source.height, width || W, height || H, f.fit, f.frame_aspect, f.zoom, f.crop_x, f.crop_y);
  }

  // Mirrors studio/framing.py segment() plus the model's trim order check.
  function segment(s, duration) {
    const start = num(s.trim_start, 0);
    const trimEnd = num(s.trim_end, 0);
    let end = trimEnd || duration;
    end = Math.min(end, duration);
    if (trimEnd && trimEnd <= start) return {start, end, error: 'Trim end must be after trim start'};
    if (start >= duration - 0.05) return {start, end, error: 'Trim start must be before the end of the video'};
    if (end - start < 0.1) return {start, end, error: 'The trimmed clip must be at least 0.1 seconds long'};
    return {start, end};
  }

  // media.ass_text wraps captions at 32 characters with Python's textwrap.
  function wrap(text, width) {
    const lines = [];
    let line = '';
    String(text || '').replace(/[\r\n]+/g, ' ').split(/\s+/).filter(Boolean).forEach(function(word) {
      while (word.length > width) {
        if (line) { lines.push(line); line = ''; }
        lines.push(word.slice(0, width));
        word = word.slice(width);
      }
      if (!word) return;
      if (!line) line = word;
      else if ((line + ' ' + word).length <= width) line += ' ' + word;
      else { lines.push(line); line = word; }
    });
    if (line) lines.push(line);
    return lines;
  }

  function captionSample(s) {
    const cue = (s.cues || []).find(c => c && String(c.text || '').trim());
    if (cue) return String(cue.text).trim();
    // Automatic captions show four words at a time.
    return String(s.roman || '').split(/\s+/).filter(Boolean).slice(0, 4).join(' ');
  }

  function scene(sid) {
    const p = StudioState.current && StudioState.current.project;
    return p ? (p.scenes || []).find(x => x.id === sid) : null;
  }

  function card(sid) {
    return $('.scene-visual-card').filter(function() { return $(this).attr('data-id') === sid; });
  }

  function info(aid) {
    if (!infos[aid]) {
      infos[aid] = StudioApi.request('/assets/' + aid + '/framing').then(function(res) {
        loaded[aid] = res;
        return res;
      }, function(err) {
        delete infos[aid];
        throw err;
      });
    }
    return infos[aid];
  }

  function pct(part, whole) {
    return (part / whole * 100) + '%';
  }

  function seconds(v) {
    return (Math.round(v * 100) / 100).toFixed(2) + ' s';
  }

  function stageHtml(s) {
    const isImage = s.visual === 'image';
    const src = `/api/assets/${esc(s.video_asset)}/media`;
    const media = isImage
      ? `<img class="framemedia" alt="Scene image" draggable="false" src="${src}">`
      : `<video class="framemedia" muted playsinline preload="${StudioState.format().maxScenes > 30 ? 'metadata' : 'auto'}" src="${src}"></video>`;
    return `
      <div class="framing" data-id="${esc(s.id)}" data-asset="${esc(s.video_asset)}">
        <div class="framestage" title="Drag to reposition when the picture is larger than its frame">
          <div class="framecrop">${media}</div>
          <div class="framecaption" aria-hidden="true"></div>
          <img class="frameexact" alt="Frame rendered by FFmpeg" hidden>
          <span class="framelabel">Live preview</span>
        </div>
        ${isImage ? '' : `
        <div class="frametransport">
          <button type="button" class="btn-framing-play">Play</button>
          <input type="range" class="framing-scrub" min="0" max="0" step="0.01" value="0" aria-label="Clip position">
          <span class="framing-time">0.00 s</span>
        </div>`}
        <div class="frameactions">
          <button type="button" class="btn-framing-exact">Render exact frame</button>
          <button type="button" class="btn-framing-live" hidden>Back to live preview</button>
        </div>
        <small class="framing-status muted" aria-live="polite"></small>
      </div>`;
  }

  function controlsHtml(s) {
    const f = framingOf(s), isImage = s.visual === 'image';
    const opt = (value, label, current) => `<option value="${value}"${value === current ? ' selected' : ''}>${label}</option>`;
    const range = (field, label, min, max, value) => `
      <label class="field"><span>${label} <output class="framing-out" data-for="${field}"></output></span>
        <input type="range" class="framing-bind" data-field="${field}" min="${min}" max="${max}" step="0.01" value="${value}">
      </label>`;
    const mode = s.short_clip_mode === 'freeze' ? 'freeze' : 'loop';
    return `
      <div class="framingcontrols">
        <div class="grid two">
          <label class="field"><span>Frame shape</span>
            <select class="framing-bind" data-field="frame_aspect">${aspectLabels().map(([v, l]) => opt(v, l, f.frame_aspect)).join('')}</select>
          </label>
          <label class="field"><span>Framing</span>
            <select class="framing-bind" data-field="fit">
              ${opt('crop', 'Fill the shape (crop edges)', f.fit)}${opt('contain', 'Show the whole picture (add padding)', f.fit)}
            </select>
          </label>
        </div>
        ${range('zoom', 'Zoom', 1, 3, f.zoom)}
        <div class="grid two">
          ${range('crop_x', 'Horizontal position', 0, 1, f.crop_x)}
          ${range('crop_y', 'Vertical position', 0, 1, f.crop_y)}
        </div>
        <div class="actions">
          <button type="button" class="btn-framing-reset">Reset framing</button>
          <label class="check"><input type="checkbox" class="framing-captions" checked> Show caption position</label>
        </div>
        ${isImage ? '<small class="muted">Images hold for the whole scene.</small>' : `
        <div class="grid two">
          <label class="field"><span>Trim start (seconds)</span>
            <input type="number" class="framing-bind" data-field="trim_start" min="0" step="0.01" value="${num(s.trim_start, 0)}">
          </label>
          <label class="field"><span>Trim end (seconds, 0 = clip end)</span>
            <input type="number" class="framing-bind" data-field="trim_end" min="0" step="0.01" value="${num(s.trim_end, 0)}">
          </label>
        </div>
        <div class="actions">
          <button type="button" class="btn-framing-mark" data-field="trim_start">Start = playhead</button>
          <button type="button" class="btn-framing-mark" data-field="trim_end">End = playhead</button>
          <button type="button" class="btn-framing-clear-end">Use clip end</button>
        </div>
        <label class="field"><span>If the trimmed clip is shorter than the scene</span>
          <select class="framing-bind" data-field="short_clip_mode">
            ${opt('loop', 'Loop the trimmed clip', mode)}${opt('freeze', 'Hold the last frame', mode)}
          </select>
        </label>
        <small class="framing-trim muted" aria-live="polite"></small>`}
        <small class="framing-geometry muted"></small>
      </div>`;
  }

  function setStatus($card, text, isError) {
    $card.find('.framing-status').toggleClass('cueissue error', !!isError).toggleClass('muted', !isError).text(text || '');
  }

  function showLive($card) {
    if ($card.find('.framing').hasClass('exact')) setStatus($card, '');
    $card.find('.frameexact').prop('hidden', true);
    $card.find('.btn-framing-live').prop('hidden', true);
    $card.find('.framelabel').text('Live preview');
    $card.find('.framing').removeClass('exact');
  }

  function renderCaption($card, s) {
    // Drawn in the project's caption style (captionstyle.js mirrors media.CAPTION_STYLES).
    const text = $card.find('.framing-captions').is(':checked') ? captionSample(s) : '';
    StudioCaptionStyle.overlay($card.find('.framecaption'), text, StudioState.current.project, s);
  }

  function describeTrim($card, s, source) {
    const $trim = $card.find('.framing-trim');
    if (!$trim.length || !source) return null;
    const seg = segment(s, source.duration);
    if (seg.error) {
      $trim.attr('class', 'framing-trim cueissue error').text(seg.error);
      return seg;
    }
    let text = `Using ${seg.start.toFixed(2)}–${seg.end.toFixed(2)} s of the ${source.duration.toFixed(2)} s clip (${(seg.end - seg.start).toFixed(2)} s).`;
    const scenes = window.StudioCaptions && StudioCaptions.measuredDuration ? StudioCaptions.measuredDuration(s.id) : null;
    if (scenes) {
      const length = scenes + num(s.hold_seconds, 0), span = seg.end - seg.start;
      if (span < length) {
        text += s.short_clip_mode === 'freeze'
          ? ` The scene lasts about ${length.toFixed(2)} s, so the last frame holds for ${(length - span).toFixed(2)} s.`
          : ` The scene lasts about ${length.toFixed(2)} s, so the clip plays ${(length / span).toFixed(1)} times.`;
      }
    }
    if (num(s.trim_end, 0) > source.duration + 0.05) text += ' Trim end is past the clip\'s end; the clip\'s end is used.';
    $trim.attr('class', 'framing-trim muted').text(text);
    return seg;
  }

  function apply(sid) {
    const s = scene(sid), $card = card(sid);
    if (!s || !$card.length || !$card.find('.framing').length) return;
    const source = loaded[s.video_asset];
    renderCaption($card, s);
    const f = framingOf(s);
    $card.find('.framing-out[data-for="zoom"]').text(f.zoom.toFixed(2) + '×');
    $card.find('.framing-out[data-for="crop_x"]').text(Math.round(f.crop_x * 100) + '%');
    $card.find('.framing-out[data-for="crop_y"]').text(Math.round(f.crop_y * 100) + '%');
    if (!source) return;
    const g = sceneGeometry(s, source), [W, H] = dims();
    $card.find('.framecrop').css({left: pct(g.pad_x, W), top: pct(g.pad_y, H), width: pct(g.crop_w, W), height: pct(g.crop_h, H)});
    $card.find('.framemedia').css({left: pct(-g.crop_x, g.crop_w), top: pct(-g.crop_y, g.crop_h),
                                   width: pct(g.scale_w, g.crop_w), height: pct(g.scale_h, g.crop_h)});
    const panX = g.scale_w > g.crop_w, panY = g.scale_h > g.crop_h;
    $card.find('.framing-bind[data-field="crop_x"]').prop('disabled', !panX);
    $card.find('.framing-bind[data-field="crop_y"]').prop('disabled', !panY);
    $card.find('.framestage').toggleClass('pannable', panX || panY);
    $card.find('.framing-geometry').text(
      `Source ${source.width}×${source.height}${source.rotation ? ' (turned upright from camera metadata)' : ''}` +
      ` → scaled to ${g.scale_w}×${g.scale_h}; showing ${g.crop_w}×${g.crop_h} from (${g.crop_x}, ${g.crop_y})` +
      ` at (${g.pad_x}, ${g.pad_y}) in the ${W}×${H} frame.` +
      (panX || panY ? ' Drag the preview to reposition.' : ''));
    describeTrim($card, s, source);
    $card.find('.framing-scrub').attr('max', source.duration.toFixed(2));
  }

  function seek(video, t) {
    try { video.currentTime = Math.max(0, t); } catch (e) { /* not loaded yet */ }
  }

  function stop(sid) {
    clearInterval(timers[sid]);
    delete timers[sid];
    const $card = card(sid);
    const video = $card.find('video.framemedia')[0];
    if (video && !video.paused) video.pause();
    $card.find('.btn-framing-play').text('Play');
  }

  function play(sid) {
    const s = scene(sid), $card = card(sid), video = $card.find('video.framemedia')[0];
    const source = s && loaded[s.video_asset];
    if (!video || !source) return;
    if (timers[sid]) return stop(sid);
    const seg = segment(s, source.duration);
    if (seg.error) return setStatus($card, seg.error, true);
    showLive($card);
    if (video.currentTime < seg.start || video.currentTime >= seg.end - FRAME) seek(video, seg.start);
    const started = video.play();
    if (started && started.catch) started.catch(err => setStatus($card, 'Playback failed: ' + err.message, true));
    $card.find('.btn-framing-play').text('Pause');
    // timeupdate fires only every ~250 ms; poll so the loop point matches the trim end.
    timers[sid] = setInterval(function() {
      const now = scene(sid);
      if (!now || !document.body.contains(video)) return stop(sid);
      const live = segment(now, source.duration);
      if (live.error) return stop(sid);
      if (video.currentTime >= live.end - 0.02 || video.ended) {
        if (now.short_clip_mode === 'freeze') {
          stop(sid);
          seek(video, live.end - FRAME);
        } else {
          seek(video, live.start);
          if (video.paused) video.play();
        }
      }
    }, 20);
  }

  function renderExact(sid) {
    const s = scene(sid), $card = card(sid);
    if (!s || !StudioState.current) return Promise.resolve();
    stop(sid);
    const p = StudioState.current.project, pid = StudioState.current.id;
    const video = $card.find('video.framemedia')[0];
    const body = {scene: s, quality: 'final', format: p.format || 'reel', caption_emphasis: p.caption_emphasis || 'none',
                  caption_size: p.caption_size, caption_bottom: p.caption_bottom,
                  caption_style: StudioCaptionStyle.styleOf(p),
                  caption: $card.find('.framing-captions').is(':checked') ? captionSample(s) : ''};
    if (video) body.source_time = Math.round(video.currentTime * 1000) / 1000;
    const seq = exactSeq[sid] = (exactSeq[sid] || 0) + 1;
    setStatus($card, 'Rendering this frame with FFmpeg…');
    $card.find('.btn-framing-exact').prop('disabled', true);
    return StudioApi.blob('/projects/' + pid + '/framing-preview', body)
      .then(function(res) {
        if (seq !== exactSeq[sid] || !StudioState.current || StudioState.current.id !== pid) return;
        const $now = card(sid), $img = $now.find('.frameexact');
        const old = $img.attr('src');
        if (old && old.indexOf('blob:') === 0 && window.URL && URL.revokeObjectURL) URL.revokeObjectURL(old);
        $img.attr('src', URL.createObjectURL(res.blob)).prop('hidden', false);
        $now.find('.framing').addClass('exact');
        $now.find('.framelabel').text('FFmpeg frame');
        $now.find('.btn-framing-live').prop('hidden', false);
        let note = `Exact frame${video ? ' at ' + seconds(body.source_time) : ''}, rendered by FFmpeg with the export's filters.`;
        const server = JSON.parse(res.headers.get('X-Framing-Geometry') || 'null');
        const source = loaded[s.video_asset];
        if (server && source && JSON.stringify(server) !== JSON.stringify(sceneGeometry(s, source))) {
          note += ' The live preview differs from the renderer; trust this frame.';
        }
        setStatus($now, note);
      })
      .catch(function(err) {
        if (seq === exactSeq[sid]) setStatus(card(sid), 'Could not render the frame: ' + err.message, true);
      })
      .finally(function() {
        if (seq === exactSeq[sid]) card(sid).find('.btn-framing-exact').prop('disabled', false);
      });
  }

  function mount() {
    $('.framing').each(function() {
      const $f = $(this), sid = $f.attr('data-id'), aid = $f.attr('data-asset');
      const $card = $f.closest('.scene-visual-card');
      const video = $f.find('video.framemedia')[0];
      if (video) {
        $(video).on('loadedmetadata', function() {
          const s = scene(sid), source = s && loaded[s.video_asset];
          if (s && source && video.currentTime === 0) seek(video, segment(s, source.duration).start);
        }).on('timeupdate seeked', function() {
          $card.find('.framing-scrub').val(video.currentTime);
          $card.find('.framing-time').text(seconds(video.currentTime));
        }).on('error', function() {
          setStatus($card, 'This browser could not play the clip. The exact frame and export are unaffected.', true);
        });
      }
      if (loaded[aid]) return apply(sid);
      setStatus($card, 'Reading picture size…');
      info(aid).then(function() {
        setStatus(card(sid), '');
        apply(sid);
        const s = scene(sid), v = card(sid).find('video.framemedia')[0];
        if (s && v && v.readyState >= 1 && v.currentTime === 0) seek(v, segment(s, loaded[aid].duration).start);
      }).catch(function(err) {
        setStatus(card(sid), 'Could not read this media: ' + err.message, true);
      });
    });
  }

  function update(sid, updates, commit) {
    const s = scene(sid);
    if (!s) return;
    showLive(card(sid));
    if (commit) {
      Scenes.updateSceneField(sid, updates);
    } else {
      Object.assign(s, updates);
      StudioState.markDirty(true);
    }
    apply(sid);
  }

  function fieldValue($el) {
    const field = $el.attr('data-field');
    if ($el.is('select')) return $el.val();
    const value = num($el.val(), field === 'zoom' ? 1 : field.indexOf('crop_') === 0 ? 0.5 : 0);
    if (field === 'zoom') return Math.min(3, Math.max(1, value));
    if (field.indexOf('crop_') === 0) return Math.min(1, Math.max(0, value));
    return Math.max(0, Math.round(value * 1000) / 1000);
  }

  function bind() {
    $(document).on('input change', '.framing-bind', function(e) {
      const $el = $(this), sid = $el.closest('.scene-visual-card').attr('data-id');
      const field = $el.attr('data-field'), value = fieldValue($el);
      const updates = {[field]: value};
      update(sid, updates, e.type === 'change');
      const video = card(sid).find('video.framemedia')[0], s = scene(sid);
      const source = s && loaded[s.video_asset];
      if (video && source && (field === 'trim_start' || field === 'trim_end') && !timers[sid]) {
        const seg = segment(s, source.duration);
        if (!seg.error) seek(video, field === 'trim_end' ? seg.end - FRAME : seg.start);
      }
    });

    $(document).on('click', '.btn-framing-reset', function() {
      update($(this).closest('.scene-visual-card').attr('data-id'), Object.assign({}, RESET), true);
      Scenes.renderSceneVisuals();
    });

    $(document).on('change', '.framing-captions', function() {
      const sid = $(this).closest('.scene-visual-card').attr('data-id');
      showLive(card(sid));
      apply(sid);
    });

    $(document).on('click', '.btn-framing-play', function() {
      play($(this).closest('.scene-visual-card').attr('data-id'));
    });

    $(document).on('input', '.framing-scrub', function() {
      const sid = $(this).closest('.scene-visual-card').attr('data-id');
      const video = card(sid).find('video.framemedia')[0];
      showLive(card(sid));
      if (video) seek(video, num($(this).val(), 0));
    });

    $(document).on('click', '.btn-framing-mark', function() {
      const sid = $(this).closest('.scene-visual-card').attr('data-id');
      const video = card(sid).find('video.framemedia')[0];
      if (!video) return;
      stop(sid);
      const field = $(this).attr('data-field'), value = Math.round(video.currentTime * 1000) / 1000;
      card(sid).find(`.framing-bind[data-field="${field}"]`).val(value);
      update(sid, {[field]: value}, true);
    });

    $(document).on('click', '.btn-framing-clear-end', function() {
      const sid = $(this).closest('.scene-visual-card').attr('data-id');
      card(sid).find('.framing-bind[data-field="trim_end"]').val(0);
      update(sid, {trim_end: 0}, true);
    });

    $(document).on('click', '.btn-framing-exact', function() {
      renderExact($(this).closest('.scene-visual-card').attr('data-id'));
    });

    $(document).on('click', '.btn-framing-live', function() {
      showLive($(this).closest('.scene-visual-card'));
    });

    // Dragging pans the picture inside its frame, like moving a photo under a mask.
    $(document).on('pointerdown', '.framestage.pannable', function(e) {
      const sid = $(this).closest('.scene-visual-card').attr('data-id'), s = scene(sid);
      const source = s && loaded[s.video_asset];
      if (!source || $(this).closest('.framing').hasClass('exact')) return;
      const f = framingOf(s);
      drag.active = {sid, x: e.clientX, y: e.clientY, crop_x: f.crop_x, crop_y: f.crop_y,
                     g: sceneGeometry(s, source), scale: dims()[0] / (this.getBoundingClientRect().width || dims()[0])};
      if (this.setPointerCapture && e.pointerId !== undefined) this.setPointerCapture(e.pointerId);
      e.preventDefault();
    });

    $(document).on('pointermove', '.framestage', function(e) {
      const d = drag.active;
      if (!d) return;
      const g = d.g, updates = {};
      const clamp = v => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;
      if (g.scale_w > g.crop_w) updates.crop_x = clamp(d.crop_x - (e.clientX - d.x) * d.scale / (g.scale_w - g.crop_w));
      if (g.scale_h > g.crop_h) updates.crop_y = clamp(d.crop_y - (e.clientY - d.y) * d.scale / (g.scale_h - g.crop_h));
      Object.keys(updates).forEach(k => card(d.sid).find(`.framing-bind[data-field="${k}"]`).val(updates[k]));
      d.last = updates;
      update(d.sid, updates, false);
    });

    $(document).on('pointerup pointercancel', '.framestage', function() {
      const d = drag.active;
      drag.active = null;
      if (d && d.last) update(d.sid, d.last, true);
    });
  }

  const Framing = {
    dims, even, frameWindow, geometry, sceneGeometry, segment, wrap, captionSample,
    stageHtml, controlsHtml, mount, apply, play, stop, renderExact,

    init: function() {
      bind();
      $(document).on('studio:project-synced', function() {
        Object.keys(timers).forEach(stop);
      });
    }
  };

  window.StudioFraming = Framing;
})(jQuery);
