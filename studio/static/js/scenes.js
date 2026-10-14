/**
 * Telugu Reel Studio - Scenes & Script Editor Management (Stage M8)
 */

(function($) {
  'use strict';

  const Scenes = {
    init: function() {
      // Scene text and field change bindings
      $(document).on('input change', '.scene-narration-input, .input-scene-narration', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.updateSceneField(sid, {
          narration: $(this).val(),
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

      $(document).on('input change', '.scene-keywords-input, .input-scene-visual-prompt', function() {
        const sid = $(this).closest('.scriptrow').data('id');
        Scenes.updateSceneField(sid, {
          keywords: $(this).val()
        });
      });

      // Add scene buttons
      $(document).on('click', '#btn-add-scene, #btn-add-scene-secondary', function() {
        Scenes.addScene();
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

      // Manual caption cues apply
      $(document).on('click', '.btn-apply-cues', function() {
        const sid = $(this).data('scene-id');
        const $textarea = $(`#cues-editor-${sid}`);
        try {
          const raw = $textarea.val().trim() || '[]';
          const parsed = JSON.parse(raw);
          if (!Array.isArray(parsed)) throw new Error('Cues must be a JSON array.');
          Scenes.updateSceneField(sid, { cues: parsed });
          StudioState.setNotice('Caption timings applied. Save and re-approve before rendering.');
        } catch (e) {
          StudioState.setNotice('Invalid cues JSON: ' + e.message, true);
        }
      });

      // Listen for project editor sync
      $(document).on('studio:editor-populated', function() {
        Scenes.renderScriptEditor();
        Scenes.renderSceneVisuals();
      });
    },

    updateSceneField: function(sid, updates) {
      if (!StudioState.current || !StudioState.current.project) return;
      const scenes = StudioState.current.project.scenes;
      const s = scenes.find(x => x.id === sid);
      if (!s) return;

      Object.assign(s, updates);
      StudioState.markDirty(true);
      Projects.triggerAutosave();
      Projects.checkPreflight();
    },

    addScene: function() {
      if (!StudioState.current || !StudioState.current.project) return;
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

      StudioState.current.project.scenes = scenes.filter(x => x.id !== sid);
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
          ? '<span class="muted">Uploaded narration attached; re-attach after text changes.</span>'
          : '';

        const rowHtml = `
          <div class="scriptrow scene-card" data-id="${s.id}">
            <div class="scenenumber">${numStr}</div>
            <div class="scriptbody">
              <div class="grid two">
                <label class="field">
                  <span>Telugu narration</span>
                  <textarea class="scene-narration-input input-scene-narration" lang="te" rows="4" placeholder="తెలుగులో మీ కథ…">${$('<div>').text(s.narration || '').html()}</textarea>
                </label>
                <label class="field">
                  <span>Roman Telugu captions</span>
                  <textarea class="scene-roman-input input-scene-caption" rows="4" placeholder="Telugulo mee katha…">${$('<div>').text(s.roman || '').html()}</textarea>
                </label>
              </div>
              <input type="hidden" class="scene-keywords-input input-scene-visual-prompt" value="${$('<div>').text(s.keywords || '').html()}">
              <div class="rowtools">
                <button type="button" class="btn-move-scene-up"${disableMoveUp}>Move up</button>
                <button type="button" class="btn-remove-scene">Remove scene</button>
                ${attachedAudioMsg}
              </div>
            </div>
          </div>
        `;
        $container.append(rowHtml);
      });
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

        const previewHtml = (s.video_asset && s.visual !== 'graphic')
          ? `<video class="clippreview" controls preload="metadata" src="/api/assets/${s.video_asset}/media"></video>`
          : `<div class="visualplaceholder"><span>✦</span><p>${s.visual === 'graphic' ? (s.graphic_title || 'Your graphic title') : 'Select footage or upload a clip'}</p></div>`;

        const isGraphic = (s.visual === 'graphic');
        const isCrop = (s.fit === 'crop');

        const visualConfigHtml = isGraphic ? `
          <label class="field">
            <span>Graphic heading (English / Roman Telugu)</span>
            <input type="text" class="scene-field-bind" data-field="graphic_title" value="${$('<div>').text(s.graphic_title || '').html()}" />
          </label>
        ` : `
          <label class="field">
            <span>English stock search terms</span>
            <input type="text" class="scene-field-bind" data-field="keywords" maxlength="100" value="${$('<div>').text(s.keywords || '').html()}" />
          </label>
          <div class="actions">
            <button type="button" class="btn-find-stock" data-scene-id="${s.id}" ${!s.keywords ? 'disabled' : ''}>Find on Pixabay</button>
            <button type="button" class="btn-open-library-single" data-scene-id="${s.id}">Choose from library</button>
            <label class="filebutton">
              Upload video
              <input type="file" class="scene-upload-video-input" data-scene-id="${s.id}" accept="video/*" />
            </label>
          </div>
          <div id="stock-results-${s.id}"></div>
          <div class="grid two">
            <label class="field">
              <span>Trim start (seconds)</span>
              <input type="number" class="scene-field-bind" data-field="trim_start" min="0" step="0.1" value="${s.trim_start || 0}" />
            </label>
            <label class="field">
              <span>Framing</span>
              <select class="scene-field-bind" data-field="fit">
                <option value="crop" ${s.fit === 'crop' ? 'selected' : ''}>Fill vertical frame</option>
                <option value="contain" ${s.fit === 'contain' ? 'selected' : ''}>Fit entire clip with padding</option>
              </select>
            </label>
          </div>
          ${isCrop ? `
            <label class="field">
              <span>Horizontal crop position</span>
              <input type="range" class="scene-field-bind" data-field="crop_x" min="0" max="1" step="0.05" value="${s.crop_x !== undefined ? s.crop_x : 0.5}" />
            </label>
          ` : ''}
          <small class="muted">Short footage loops to cover narration. Source clip audio is muted.</small>
        `;

        const cuesJson = JSON.stringify(s.cues || [], null, 2);

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
                <label class="field">
                  <span>Visual source</span>
                  <select class="scene-visual-source-select">
                    <option value="upload" ${s.visual === 'upload' ? 'selected' : ''}>Uploaded footage</option>
                    <option value="stock" ${s.visual === 'stock' ? 'selected' : ''}>Pixabay stock footage</option>
                    <option value="graphic" ${s.visual === 'graphic' ? 'selected' : ''}>Simple animated title card (explicit choice)</option>
                  </select>
                </label>
                ${visualConfigHtml}
                <label class="field">
                  <span>Hold after narration (seconds)</span>
                  <input type="number" class="scene-field-bind" data-field="hold_seconds" min="0" max="30" step="0.1" value="${s.hold_seconds || 0}" />
                </label>
                <label class="check">
                  <input type="checkbox" class="scene-rights-checkbox" ${s.rights_reviewed ? 'checked' : ''} />
                  I reviewed visual relevance and permission to use it.
                </label>
                <details style="margin-top:14px;">
                  <summary><b>Caption timing cues</b> (${s.cues ? s.cues.length : 0} manual words)</summary>
                  <p class="muted">Word-level start/end timestamps override automatic text-proportional timing.</p>
                  <textarea id="cues-editor-${s.id}" rows="4" style="font-family:monospace;font-size:12px;">${cuesJson}</textarea>
                  <div class="actions">
                    <button type="button" class="btn-apply-cues" data-scene-id="${s.id}">Apply timings</button>
                  </div>
                </details>
              </div>
            </div>
          </section>
        `;
        $container.append(cardHtml);
      });
    }
  };

  // Scene visual field bindings
  $(document).on('change', '.scene-visual-source-select', function() {
    const sid = $(this).closest('.scene-visual-card').data('id');
    const val = $(this).val();
    Scenes.updateSceneField(sid, { visual: val, rights_reviewed: false });
    Scenes.renderSceneVisuals();
  });

  $(document).on('input change', '.scene-field-bind', function() {
    const sid = $(this).closest('.scene-visual-card').data('id');
    const field = $(this).data('field');
    let val = $(this).val();
    if ($(this).attr('type') === 'number' || $(this).attr('type') === 'range') {
      val = +val;
    }
    const update = {};
    update[field] = val;
    Scenes.updateSceneField(sid, update);
    if (field === 'fit' || field === 'graphic_title') {
      Scenes.renderSceneVisuals();
    }
  });

  $(document).on('change', '.scene-rights-checkbox', function() {
    const sid = $(this).closest('.scene-visual-card').data('id');
    const checked = $(this).is(':checked');
    Scenes.updateSceneField(sid, { rights_reviewed: checked });
    Projects.updateHeaderBadges();
  });

  window.Scenes = Scenes;
  window.StudioScenes = Scenes;
})(jQuery);
