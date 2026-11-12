/**
 * Telugu Reel Studio - Scenes & Script Editor Management (Stage M8)
 */

(function($) {
  'use strict';

  const esc = value => $('<div>').text(value || '').html().replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const Scenes = {
    init: function() {
      StudioWorkflow.init();
      // Scene text and field change bindings
      $(document).on('input change', '.scene-narration-input, .input-scene-narration', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.updateSceneField(sid, {
          narration: $(this).val(),
          narration_source_roman: '',
          spoken_text: '',
          take_id: '',
          cues: []
        });
      });

      $(document).on('input change', '.scene-roman-input, .input-scene-caption', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.updateSceneField(sid, {
          roman: $(this).val(),
          cues: []
        });
      });

      $(document).on('input change', '.scene-clip-volume', function(e) {
        $(this).closest('.clipsound').find('.clip-volume-label').text(Math.round(this.value * 100) + '%');
        if (e.type === 'change') Scenes.updateSceneField($(this).data('scene-id'), {clip_volume: Number(this.value)});
      });

      $(document).on('click', '.btn-clear-extra-shots', function() {
        Scenes.updateSceneField($(this).data('scene-id'), {extra_shots: [], shot_weights: []});
        Scenes.renderSceneVisuals();
      });

      $(document).on('input change', '.scene-keywords-input, .input-scene-visual-prompt', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.updateSceneField(sid, {
          keywords: $(this).val()
        });
      });

      // Add scene buttons
      $(document).on('click', '#btn-add-scene, #btn-add-scene-secondary, #btn-add-scene-bottom', function() {
        Scenes.addScene();
      });

      $(document).on('click', '#btn-import-split', function() {
        if (!StudioState.current) return;
        let scenes;
        try {
          scenes = Scenes.splitScript($('#input-import-script').val(), $('#script-input-format').val() !== 'telugu');
        } catch (e) {
          return StudioState.setNotice(e.message, true);
        }
        const p = StudioState.current.project;
        if (p.scenes.some(s => s.narration.trim()) && !confirm('Replace the current storyboard with this script?')) return;
        p.scenes = scenes;
        StudioState.markDirty(true);
        Projects.populateEditor();
        Projects.triggerAutosave();
        Scenes.autoTelugu(scenes.length);
      });

      // Key words shown in the highlight colour (Captions tab), comma- or space-separated.
      $(document).on('change', '.scene-emphasis-input', function() {
        const words = String($(this).val() || '').split(/[,\s]+/).map(w => w.trim()).filter(Boolean).slice(0, 6).map(w => w.slice(0, 40));
        Scenes.updateSceneField($(this).data('scene-id'), {emphasis: words});
      });

      // Move scene up
      $(document).on('click', '.btn-move-scene-up', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.moveSceneUp(sid);
      });

      // Remove scene
      $(document).on('click', '.btn-remove-scene', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.removeScene(sid);
      });

      // Listen for project editor sync
      $(document).on('studio:editor-populated', function() {
        Scenes.renderScriptEditor();
        Scenes.renderSceneVisuals();
      });
    },

    /** After "Split into scenes": write every scene's Telugu narration (and stock words, headings and
     *  caption key words) with one AI job, the same preparation the one-click draft uses. */
    autoTelugu: function(count) {
      const cfg = StudioState.getCfg() || {};
      const scenes = StudioState.current.project.scenes || [];
      // Lines in English letters need Telugu narration; lines in Telugu letters need Roman captions.
      const needTelugu = scenes.some(s => String(s.roman || '').trim() && !String(s.narration || '').trim());
      const needRoman = scenes.some(s => String(s.narration || '').trim() && !String(s.roman || '').trim());
      const writing = [needTelugu && 'Telugu narration', needRoman && 'Roman captions'].filter(Boolean).join(' and ') || 'stock search words';
      if (!cfg.online_enabled || !cfg.gemini_key_configured || !window.StudioAutopilot) {
        return StudioState.setNotice(`Script split into ${count} scene(s).` + (needTelugu || needRoman
          ? ` Turn on online services and add a Gemini key in Settings to write the ${writing} automatically, or use Convert to Telugu on each scene.`
          : ''));
      }
      if (StudioAutopilot.isRunning()) return;
      StudioState.setNotice(`Script split into ${count} scene(s). Writing the ${writing} with AI… (about ${count > 30 ? 'a few minutes' : 'a minute'}; keep this page open)`);
      $('#btn-import-split').prop('disabled', true).addClass('keep-disabled');
      return StudioAutopilot.prepareScenes()
        .then(function() {
          const ready = StudioState.current.project.scenes.filter(s => String(s.narration || '').trim() && String(s.roman || '').trim()).length;
          StudioState.setNotice(`Telugu narration and Roman captions ready for ${ready} of ${count} scene(s). Review them before recording; edit any scene or use Convert to Telugu there.`);
        })
        .catch(function(err) {
          StudioState.setNotice(`Script split into ${count} scene(s), but the ${writing} could not be written: ${err.message} `
            + 'Click Prepare my script with AI to try again, or use Convert to Telugu on each scene.', true);
        })
        .finally(function() {
          $('#btn-import-split').prop('disabled', false).removeClass('keep-disabled');
        });
    },

    /** Telugu letters typed or pasted into the Roman captions box belong to the Telugu narration
     *  (the Roman box takes English letters only); move them there when that box is empty. */
    moveTeluguToNarration: function(p) {
      (p && p.scenes || []).forEach(function(s) {
        if (/[\u0c00-\u0c7f]/.test(s.roman || '') && !String(s.narration || '').trim()) {
          s.narration = s.roman;
          s.roman = '';
          s.narration_source_roman = '';
        }
      });
    },

    // One scene per line or sentence, keeping the writer's own words (minus chat formatting).
    splitScript: function(text, isRoman) {
      const parts = String(text || '').trim().split(/\n+|(?<=[.!?।])\s+/u).map(Scenes.cleanCaption).filter(Boolean);
      if (!parts.length) throw new Error('Paste a script first.');
      const fmt = StudioState.format();
      if (parts.length > fmt.maxScenes || parts.some(s => s.length > 3000)) {
        throw new Error(`${fmt.label} supports at most ${fmt.maxScenes} scenes (this script has ${parts.length}) and 3,000 characters per scene.`
          + (fmt.maxScenes < 400 ? ' For a longer script choose "YouTube video" as the video format.' : ''));
      }
      // A line written in Telugu letters (even with English words, "ఒక lake దగ్గర") is Telugu narration;
      // the Roman captions box only takes English letters and is written from it automatically.
      return parts.map(text => {
        const telugu = !isRoman || /[ఀ-౿]/.test(text);
        return {...StudioState.createEmptyScene(), narration: telugu ? text : '', roman: telugu ? '' : text};
      });
    },

    // Mirrors providers.clean_caption: markdown emphasis, heading marks and 'Title:'-style labels.
    cleanCaption: function(text) {
      return String(text || '').replace(/\*+|`+|_{2,}/g, '').replace(/^\s*#+\s*/, '')
        .replace(/^\s*(?:title|scene\s*\d+|hook|cta|caption|narration)\s*[:\-\u2013]\s*/i, '')
        .replace(/\s+/g, ' ').trim();
    },

    updateSceneField: function(sid, updates) {
      if (!StudioState.current || !StudioState.current.project) return;
      const scenes = StudioState.current.project.scenes;
      const s = scenes.find(x => x.id === sid);
      if (!s) return;

      if (Array.isArray(updates.cues) && !updates.cues.length && s.cues && s.cues.length) {
        // Text or narration changes invalidate timings; keep them restorable in the caption editor.
        StudioState.clearedCues = StudioState.clearedCues || {};
        StudioState.clearedCues[sid] = s.cues;
        StudioState.setNotice('Caption timings for this scene were cleared because its text or narration changed. You can restore them on the Captions tab.');
      }
      Object.assign(s, updates);
      StudioState.markDirty(true);
      Projects.triggerAutosave();
      Projects.checkPreflight();
    },

    addScene: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const fmt = StudioState.format();
      if (StudioState.current.project.scenes.length >= fmt.maxScenes) return StudioState.setNotice(`${fmt.label} supports up to ${fmt.maxScenes} scenes.`, true);
      const newScene = StudioState.createEmptyScene();
      StudioState.current.project.scenes.push(newScene);
      StudioState.markDirty(true);
      Scenes.renderScriptEditor();
      Scenes.renderSceneVisuals();
      Projects.triggerAutosave();
      Projects.checkPreflight();
      StudioState.setNotice('Blank scene added. No AI required.');
    },

    moveSceneUp: function(sid) {
      if (!StudioState.current || !StudioState.current.project) return;
      if (StudioState.current.project.narration_mode === 'track') {
        return StudioState.setNotice('With one recording for the whole video, scenes follow the order of the recording. '
          + 'Move where a scene starts on the Voice tab instead.', true);
      }
      const scenes = StudioState.current.project.scenes;
      const idx = scenes.findIndex(x => x.id === sid);
      if (idx <= 0) return;

      const temp = scenes[idx - 1];
      scenes[idx - 1] = scenes[idx];
      scenes[idx] = temp;

      StudioState.markDirty(true);
      Scenes.renderScriptEditor();
      Scenes.renderSceneVisuals();
      Projects.triggerAutosave();
      Projects.checkPreflight();
    },

    removeScene: function(sid) {
      if (!StudioState.current || !StudioState.current.project) return;
      const scenes = StudioState.current.project.scenes;
      if (scenes.length <= 1) {
        StudioState.setNotice('Projects must retain at least one scene.', true);
        return;
      }

      const index = scenes.findIndex(x => x.id === sid);
      StudioState.current.project.scenes = scenes.filter(x => x.id !== sid);
      if (StudioState.current.project.narration_mode === 'track' && index > 0) {
        StudioState.setNotice(`Scene removed. Its part of your recording now plays during scene ${index}; `
          + 'adjust where scenes start on the Voice tab if needed.');
      }
      StudioState.markDirty(true);
      Scenes.renderScriptEditor();
      Scenes.renderSceneVisuals();
      Projects.triggerAutosave();
      Projects.checkPreflight();
    },

    renderScenes: function() {
      Scenes.renderScriptEditor();
      Scenes.renderSceneVisuals();
    },

    renderScriptEditor: function() {
      const $container = $('#scenes-list, #script-editor-rows').empty();
      if (!StudioState.current || !StudioState.current.project) return;
      const scenes = StudioState.current.project.scenes || [];

      $('#script-editor-badge').text(scenes.length + ' scenes');

      scenes.forEach(function(s, idx) {
        const numStr = String(idx + 1).padStart(2, '0');
        const disableMoveUp = (idx === 0) ? ' disabled' : '';
        const attachedAudioMsg = s.audio_asset
          ? '<span class="muted">Narration attached; text or voice changes may require a new take.</span>'
          : '';

        const rowHtml = `
          <div class="scriptrow scene-card" data-id="${s.id}">
            <div class="scenenumber">${numStr}</div>
            <div class="scriptbody">
              <div class="grid two">
                <label class="field">
                  <span>Telugu narration — review before recording</span>
                  <textarea class="scene-narration-input input-scene-narration" lang="te" rows="4" placeholder="తెలుగులో మీ కథ…">${$('<div>').text(s.narration || '').html()}</textarea>
                </label>
                <label class="field">
                  <span>Roman Telugu input / captions</span>
                  <textarea class="scene-roman-input input-scene-caption" rows="4" placeholder="Telugulo mee katha…">${$('<div>').text(s.roman || '').html()}</textarea>
                </label>
              </div>
              <div class="rowtools">
                <button type="button" class="btn-scene-transliterate" data-scene-id="${s.id}">Convert to Telugu</button>
                ${attachedAudioMsg}
              </div>
              <details class="fold mini" data-fold="scene-more-${s.id}">
                <summary>More: search words, prompt, move, remove, own recording</summary>
                <label class="field"><span>English footage search keywords</span><input type="text" class="scene-keywords-input input-scene-visual-prompt" maxlength="100" value="${$('<div>').text(s.keywords || '').html().replace(/"/g, '&quot;')}"></label>
                <div class="rowtools">
                  <button type="button" class="btn-scene-prompt" data-scene-id="${s.id}">Prompt</button>
                  <button type="button" class="btn-move-scene-up"${disableMoveUp}>Move up</button>
                  <button type="button" class="btn-remove-scene">Remove scene</button>
                  <label class="filebutton ghost">Upload narration<input type="file" class="scene-upload-audio-input" data-scene-id="${s.id}" accept="audio/*"></label>
                  ${s.audio_asset ? `<audio controls preload="none" src="/api/assets/${s.audio_asset}/media"></audio><button type="button" class="btn-remove-scene-audio" data-scene-id="${s.id}">Remove recording</button>` : ''}
                </div>
              </details>
            </div>
          </div>
        `;
        $container.append(rowHtml);
      });
      if (window.StudioFold) StudioFold.restore($container);
    },

    renderSceneVisuals: function() {
      const $container = $('#scenes-visuals-container, #scene-visual-cards-container').empty();
      if (!StudioState.current || !StudioState.current.project) return;
      const scenes = StudioState.current.project.scenes || [];

      scenes.forEach(function(s, idx) {
        const numStr = String(idx + 1).padStart(2, '0');
        const heading = s.graphic_title || (s.roman ? s.roman.slice(0, 55) : 'Choose a visual');
        const visualBadgeClass = s.visual === 'graphic' ? 'approved' : s.video_asset ? '' : 'red';
        const visualBadgeText = s.visual === 'graphic' ? 'graphic title card' : s.video_asset ? s.visual : 'media missing';

        // Framing preview mirrors the renderer's crop, zoom and trim (framing.js).
        const previewHtml = (s.video_asset && s.visual !== 'graphic')
          ? StudioFraming.stageHtml(s)
          : `<div class="visualplaceholder"><span>✦</span><p>${esc(s.visual === 'graphic' ? (s.graphic_title || 'Your graphic title') : 'Select footage or upload a clip')}</p></div>`;

        const isGraphic = (s.visual === 'graphic');

        const visualConfigHtml = isGraphic ? `
          <label class="field">
            <span>Graphic heading (English / Roman Telugu)</span>
            <input type="text" class="scene-field-bind" data-field="graphic_title" value="${esc(s.graphic_title)}" />
          </label>
        ` : `
          <label class="field">
            <span>English stock search terms</span>
            <input type="text" class="scene-field-bind" data-field="keywords" maxlength="100" value="${esc(s.keywords)}" />
          </label>
          <div class="actions">
            <button type="button" class="btn-find-stock" data-scene-id="${s.id}" ${!s.keywords ? 'disabled' : ''}>Find free stock</button>
            <button type="button" class="btn-open-library-single" data-scene-id="${s.id}">Choose from library</button>
            <label class="filebutton">
              Upload video / image
              <input type="file" class="scene-upload-video-input" data-scene-id="${s.id}" accept="video/*,image/jpeg,image/png,image/webp" />
            </label>
          </div>
          <label class="field"><span>Stock source</span><select class="scene-field-bind" data-field="stock_provider">
            <option value="auto" ${!s.stock_provider || s.stock_provider === 'auto' ? 'selected' : ''}>Pixabay first, then Pexels if no results</option>
            <option value="pixabay" ${s.stock_provider === 'pixabay' ? 'selected' : ''}>Pixabay</option>
            <option value="pexels" ${s.stock_provider === 'pexels' ? 'selected' : ''}>Pexels</option>
          </select></label>
          <div id="stock-results-${s.id}"></div>
          ${s.video_asset ? StudioFraming.controlsHtml(s) : '<small class="muted">Choose footage or an image to adjust its framing and trim.</small>'}
          ${(s.extra_shots || []).length && ['stock', 'upload'].includes(s.visual) ? `
          <p class="extrashots"><b>+${s.extra_shots.length} more shot(s)</b> ${(s.shot_weights || []).length ? `in the order of the
            words: each clip shows while the part of the line it was chosen for is spoken` : `of the same subject: a long scene cuts to the
            next clip about every ${StudioState.format().width > StudioState.format().height ? 6 : 4} seconds`} (from its start, cropped to fill the frame).
            <button type="button" class="ghost btn-clear-extra-shots" data-scene-id="${s.id}">Use only the main clip</button></p>` : ''}
          ${['upload', 'stock'].includes(s.visual) && s.video_asset ? `
          <label class="field clipsound"><span>Clip sound: <b class="clip-volume-label">${Math.round((s.clip_volume || 0) * 100)}%</b></span>
            <input type="range" class="scene-clip-volume" data-scene-id="${s.id}" min="0" max="1" step="0.05" value="${s.clip_volume || 0}"></label>
          <small class="muted">0% keeps the clip silent (the default). Raise it to hear the clip's own sound under the narration;
            it loops or stops with the picture. Scene length follows the narration.</small>`
          : '<small class="muted">Scene length follows the narration.</small>'}
        `;

        const cardHtml = `
          <section class="panel scene-visual-card" data-id="${s.id}">
            <div class="sectionhead">
              <div>
                <span class="eyebrow">SCENE ${numStr}</span>
                <h2>${$('<div>').text(heading).html()}</h2>
              </div>
              <span class="badge ${visualBadgeClass}">${visualBadgeText}</span>
            </div>
            <p>${$('<div>').text(s.roman || '').html()}</p>
            <div class="scenegrid">
              <div>${previewHtml}</div>
              <div>
                <p class="visualnow">${s.visual === 'graphic' ? 'Showing a title card'
                  : s.video_asset ? `Showing ${s.visual === 'image' ? 'an image' : s.visual === 'stock' ? 'stock footage' : 'your footage'}`
                    + ((s.extra_shots || []).length ? ` + ${s.extra_shots.length} more shot(s)` : '')
                  : 'No footage yet: open the options below, or use Find stock for empty scenes'}</p>
                <details class="fold mini footagefold" data-fold="scene-footage-${s.id}">
                <summary>Change footage &amp; framing</summary>
                <label class="field">
                  <span>Visual source</span>
                  <select class="scene-visual-source-select">
                    <option value="upload" ${s.visual === 'upload' ? 'selected' : ''}>Uploaded footage</option>
                    <option value="stock" ${s.visual === 'stock' ? 'selected' : ''}>Stock footage</option>
                    <option value="image" ${s.visual === 'image' ? 'selected' : ''}>Uploaded image</option>
                    <option value="graphic" ${s.visual === 'graphic' ? 'selected' : ''}>Simple animated title card (explicit choice)</option>
                  </select>
                </label>
                <button type="button" class="btn-scene-prompt" data-scene-id="${s.id}">Prompt for this scene</button>
                ${visualConfigHtml}
                <label class="field">
                  <span>Hold after narration (seconds)</span>
                  <input type="number" class="scene-field-bind" data-field="hold_seconds" min="0" max="30" step="0.1" value="${s.hold_seconds || 0}" />
                </label>
                </details>
                <label class="check">
                  <input type="checkbox" class="scene-rights-checkbox" ${s.rights_reviewed ? 'checked' : ''} />
                  I reviewed visual relevance and permission to use it.
                </label>
                <div class="actions">
                  <span class="muted">${s.cues && s.cues.length ? s.cues.length + ' saved manual caption(s)' : 'Automatic caption timing'}</span>
                  <button type="button" class="btn-open-captions" data-scene-id="${s.id}">Edit captions &amp; timing</button>
                </div>
              </div>
            </div>
          </section>
        `;
        $container.append(cardHtml);
      });
      if (window.StudioFold) StudioFold.restore($container);
      StudioFraming.mount();
    }
  };

  // Scene visual field bindings
  $(document).on('change', '.scene-visual-source-select', function() {
    const sid = $(this).closest('.scene-visual-card').attr('data-id');
    const val = $(this).val();
    Scenes.updateSceneField(sid, { visual: val, rights_reviewed: false });
    Scenes.renderSceneVisuals();
  });

  $(document).on('input change', '.scene-field-bind', function() {
    const sid = $(this).closest('.scene-visual-card').attr('data-id');
    const field = $(this).data('field');
    let val = $(this).val();
    if ($(this).attr('type') === 'number' || $(this).attr('type') === 'range') {
      val = +val;
    }
    const update = {};
    update[field] = val;
    Scenes.updateSceneField(sid, update);
    if (field === 'graphic_title') {
      Scenes.renderSceneVisuals();
    }
  });

  $(document).on('change', '.scene-rights-checkbox', function() {
    const sid = $(this).closest('.scene-visual-card').attr('data-id');
    const checked = $(this).is(':checked');
    Scenes.updateSceneField(sid, { rights_reviewed: checked });
    Projects.updateHeaderBadges();
  });

  window.Scenes = Scenes;
  window.StudioScenes = Scenes;
})(jQuery);
