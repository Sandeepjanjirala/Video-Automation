/**
 * Telugu Reel Studio - Caption style presets (Stage 4)
 *
 * Live CSS previews of the ASS presets in studio/media.py (CAPTION_STYLES), used by
 * the style picker on the Captions tab and by every scene's framing preview.
 * "Render exact caption" draws the caption with FFmpeg/libass through the same
 * write_ass the draft and final exports use.
 */
(function($) {
  'use strict';

  const dims = () => [StudioState.format().width, StudioState.format().height];
  const STYLES = {
    classic: {label: 'Classic outline', note: 'White text with a dark outline (the original style).'},
    black_box: {label: 'Black box', note: 'White text on a solid black box behind each line.'},
    yellow_accent: {label: 'Yellow accent', note: 'Bold yellow text with a heavy black outline.'},
    translucent: {label: 'Translucent background', note: 'White text on one see-through dark panel.'},
  };
  const TELUGU = /[ఀ-౿]/;
  const FONTS = {te: "'Nirmala UI', 'Noto Sans Telugu', sans-serif", en: 'Arial, sans-serif'};
  const esc = value => $('<div>').text(value == null ? '' : value).html();
  const ems = {};
  let bgSeq = 0, exactSeq = 0, bgKey = null, active = false;

  function num(value, fallback) {
    const n = Number(value);
    return value === '' || value == null || !Number.isFinite(n) ? fallback : n;
  }

  function styleOf(p) {
    return p && STYLES.hasOwnProperty(p.caption_style) ? p.caption_style : 'classic';
  }

  // libass scales a font so its ascent + descent equals the ASS font size; CSS sizes the
  // em box. Measure the ratio for the font the browser actually uses (0.895 for Arial).
  function emFactor(lang) {
    if (ems[lang]) return ems[lang];
    let factor = 0.895;
    try {
      const ctx = document.createElement('canvas').getContext('2d');
      ctx.font = `700 100px ${FONTS[lang]}`;
      const m = ctx.measureText(lang === 'te' ? 'క' : 'H');
      const height = m.fontBoundingBoxAscent + m.fontBoundingBoxDescent;
      if (height > 0) factor = 100 / height;
    } catch (e) { /* no canvas (tests): keep the Arial ratio */ }
    return (ems[lang] = factor);
  }

  // Mirrors media.word_key / emphasis_words: a scene's key words (or its stock subject words) and numbers.
  const wordKey = w => String(w || '').toLowerCase().replace(/[^0-9a-z\u0c00-\u0c7f]/g, '');
  function emphasisWords(scene, p) {
    if (!p || p.caption_emphasis !== 'keywords') return null;
    let chosen = new Set((scene && scene.emphasis || []).map(wordKey).filter(Boolean));
    if (!chosen.size) chosen = new Set(String(scene && scene.keywords || '').split(/\s+/).map(wordKey).filter(k => k.length >= 4));
    return chosen;
  }

  function cq(px) {
    return (px / dims()[0] * 100) + 'cqw';
  }

  /** Draw `text` into a .framecaption element the way the export will place and style it
   * (key words of `scene` in the highlight colour when the project highlights them). */
  function overlay($cap, text, p, scene) {
    text = String(text || '').trim();
    const style = styleOf(p), lang = TELUGU.test(text) ? 'te' : 'en';
    // Same placement as media.write_ass for the project's format (studio/formats.py).
    const fmt = StudioState.formatOf(p), [W, H] = [fmt.width, fmt.height];
    const size = num(p && p.caption_size, 56), bottom = StudioState.captionMargin(p);
    const em = emphasisWords(scene, p);
    const mark = word => em && wordKey(word) && (em.has(wordKey(word)) || /\d/.test(word)) ? `<span class="capem">${esc(word)}</span>` : esc(word);
    const lines = StudioFraming.wrap(text, fmt.captionChars).map(line => `<span class="capline">${line.split(' ').map(mark).join(' ')}</span>`);
    const html = style === 'translucent' ? `<span class="capblock">${lines.join('<br>')}</span>` : lines.join('<br>');
    $cap.attr('class', 'framecaption capstyle-' + style).attr('lang', lang).prop('hidden', !text)
      .css({bottom: (bottom / H * 100) + '%', left: (fmt.sides[0] / W * 100) + '%', right: (fmt.sides[1] / W * 100) + '%',
            fontSize: cq(size * emFactor(lang)), lineHeight: cq(size), '--cap-pad': cq(Math.round(size / 4))})
      .html(html);
  }

  function scenes() {
    return (StudioState.current && StudioState.current.project && StudioState.current.project.scenes) || [];
  }

  function chosenScene() {
    const sid = $('#caption-style-scene').val();
    return scenes().find(s => s.id === sid) || scenes()[0] || null;
  }

  function sourceTime(s) {
    return s && s.visual !== 'graphic' && s.visual !== 'image' ? num(s.trim_start, 0) : null;
  }

  function sampleText() {
    const typed = $('#caption-style-text').val();
    return typed != null && typed.trim() ? typed.trim() : 'నమస్కారం, మన తెలుగు కథ';
  }

  function canPreview(s) {
    return !!s && (s.visual === 'graphic' || !!s.video_asset);
  }

  function setStatus(text, isError) {
    $('#caption-style-status').attr('class', isError ? 'cueissue error' : 'muted').text(text || '');
  }

  function showLive() {
    $('#caption-style-exact').prop('hidden', true);
    $('#btn-caption-style-live').prop('hidden', true);
    $('#caption-style-stage .framelabel').text('Live preview');
  }

  function request(s, caption) {
    const p = StudioState.current.project;
    return StudioApi.blob('/projects/' + StudioState.current.id + '/caption-preview', {
      scene: s, source_time: sourceTime(s), caption: caption, quality: 'final', format: p.format || 'reel',
      caption_emphasis: p.caption_emphasis || 'none',
      caption_size: p.caption_size, caption_bottom: p.caption_bottom, caption_style: styleOf(p)});
  }

  function setImage($img, blob) {
    const old = $img.attr('src');
    if (old && old.indexOf('blob:') === 0 && window.URL && URL.revokeObjectURL) URL.revokeObjectURL(old);
    $img.attr('src', URL.createObjectURL(blob));
  }

  /** The stage's background is the export frame without captions, so only captions differ. */
  function loadBackground(force) {
    const s = chosenScene();
    if (!StudioState.current || !canPreview(s)) {
      $('#caption-style-bg').removeAttr('src');
      setStatus(s ? 'Choose a video or image for this scene to preview captions over it.' : '');
      return Promise.resolve();
    }
    const key = JSON.stringify([StudioState.current.id, s]);
    if (!force && key === bgKey) return Promise.resolve();
    bgKey = key;
    const seq = ++bgSeq;
    setStatus('Rendering the scene background with FFmpeg…');
    return request(s, '').then(function(res) {
      if (seq !== bgSeq) return;
      setImage($('#caption-style-bg'), res.blob);
      setStatus('');
    }).catch(function(err) {
      if (seq !== bgSeq) return;
      bgKey = null;
      setStatus('Could not render the scene background: ' + err.message, true);
    });
  }

  function renderExact() {
    const s = chosenScene();
    if (!StudioState.current || !canPreview(s)) return Promise.resolve();
    const seq = ++exactSeq;
    setStatus('Rendering this caption with FFmpeg…');
    $('#btn-caption-style-exact').prop('disabled', true);
    return request(s, sampleText()).then(function(res) {
      if (seq !== exactSeq) return;
      setImage($('#caption-style-exact'), res.blob);
      $('#caption-style-exact').prop('hidden', false);
      $('#btn-caption-style-live').prop('hidden', false);
      $('#caption-style-stage .framelabel').text('FFmpeg frame');
      setStatus(`Exact ${STYLES[styleOf(StudioState.current.project)].label.toLowerCase()} caption, drawn by FFmpeg exactly as in draft and final exports.`);
    }).catch(function(err) {
      if (seq === exactSeq) setStatus('Could not render the caption: ' + err.message, true);
    }).finally(function() {
      if (seq === exactSeq) $('#btn-caption-style-exact').prop('disabled', false);
    });
  }

  function refreshPreviews() {
    if (!StudioState.current) return;
    const p = StudioState.current.project;
    $('#caption-style-panel input[name="caption-style"]').each(function() {
      $(this).prop('checked', this.value === styleOf(p));
      $(this).closest('.stylecard').toggleClass('selected', this.value === styleOf(p));
    });
    // Each swatch shows its own style; only size and margin come from the project.
    $('.stylesample .framecaption').each(function() {
      overlay($(this), $(this).attr('data-sample'), Object.assign({}, p, {caption_style: $(this).closest('.stylecard').attr('data-style')}),
              {emphasis: ['katha']});
    });
    overlay($('#caption-style-stage .framecaption'), sampleText(), p, chosenScene());
    $('#caption-emphasis-toggle').prop('checked', p.caption_emphasis === 'keywords');
    $('.caption-size-input').val(num(p.caption_size, 56));
    $('.caption-bottom-input').val(num(p.caption_bottom, 300));
    if (window.StudioFraming) scenes().forEach(s => StudioFraming.apply(s.id));
  }

  function render() {
    const $panel = $('#caption-style-panel');
    if (!$panel.length || !StudioState.current) return;
    const p = StudioState.current.project;
    $('#caption-style-options').html(Object.keys(STYLES).map(id => `
      <label class="stylecard" data-style="${id}">
        <input type="radio" name="caption-style" value="${id}"${id === styleOf(p) ? ' checked' : ''}>
        <span class="stylesample" aria-hidden="true">
          <span class="framecaption" data-sample="మన కథ Mana katha"></span>
        </span>
        <b>${STYLES[id].label}</b>
        <small class="muted">${STYLES[id].note}</small>
      </label>`).join(''));
    const current = $('#caption-style-scene').val();
    $('#caption-style-scene').html(scenes().map((s, i) => {
      const label = `Scene ${i + 1}${s.visual === 'graphic' ? ' (title card)' : s.video_asset ? '' : ' (no media yet)'}`;
      return `<option value="${esc(s.id)}">${esc(label)}</option>`;
    }).join(''));
    if (scenes().some(s => s.id === current)) $('#caption-style-scene').val(current);
    if (!$('#caption-style-text').val()) {
      const s = chosenScene();
      const sample = s && window.StudioFraming ? StudioFraming.captionSample(s) : '';
      $('#caption-style-text').val(sample || 'నమస్కారం, మన తెలుగు కథ');
    }
    refreshPreviews();
    if (active) loadBackground(false);
  }

  function choose(style) {
    if (!StudioState.current || !STYLES[style]) return;
    StudioState.current.project.caption_style = style;
    StudioState.markDirty(true);
    Projects.triggerAutosave();
    showLive();
    refreshPreviews();
  }

  const CaptionStyle = {
    STYLES, overlay, styleOf, emFactor, emphasisWords, render, renderExact, loadBackground,

    onTabChange: function(tab) {
      active = tab === 'captions';
      if (active) {
        render();
        loadBackground(false);
      }
    },

    init: function() {
      $(document).on('studio:editor-populated', function() {
        bgKey = null;
        $('#caption-style-text').val('');
        render();
      });
      $(document).on('change', '#caption-style-panel input[name="caption-style"]', function() {
        choose(this.value);
      });
      // Size and margin controls exist here and on the Export tab; projects.js stores the value.
      $(document).on('input change', '.caption-size-input, .caption-bottom-input, #range-caption-size, #range-caption-bottom', function() {
        const field = $(this).attr('data-field');
        $(`[data-field="${field}"]`).not(this).val($(this).val());
        showLive();
        refreshPreviews();
      });
      $(document).on('change', '#caption-emphasis-toggle', function() {
        if (!StudioState.current) return;
        StudioState.current.project.caption_emphasis = this.checked ? 'keywords' : 'none';
        StudioState.markDirty(true);
        Projects.triggerAutosave();
        showLive();
        refreshPreviews();
      });
      $(document).on('input', '#caption-style-text', function() {
        showLive();
        refreshPreviews();
      });
      $(document).on('change', '#caption-style-scene', function() {
        const s = chosenScene();
        $('#caption-style-text').val((s && StudioFraming.captionSample(s)) || 'నమస్కారం, మన తెలుగు కథ');
        showLive();
        refreshPreviews();
        loadBackground(false);
      });
      $(document).on('click', '#btn-caption-style-exact', function() { renderExact(); });
      $(document).on('click', '#btn-caption-style-live', function() { showLive(); setStatus(''); });
      $(document).on('click', '#btn-caption-style-refresh', function() { showLive(); loadBackground(true); });
    }
  };

  window.StudioCaptionStyle = CaptionStyle;
})(jQuery);
