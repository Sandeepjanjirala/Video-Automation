/**
 * Telugu Reel Studio - Phase 4 Telugu Voice Lab & Delivery Presets (Stage M8)
 */

(function($) {
  'use strict';

  let rewritePreviewData = null;

  const Voice = {
    init: function() {
      // Provider change -> update voice options
      $(document).on('change', '#select-tts-provider', function() {
        const prov = $(this).val();
        Voice.populateVoiceOptions(prov);

        if (!StudioState.current || !StudioState.current.project) return;
        const p = StudioState.current.project;
        p.tts_provider = prov;
        p.voice = $('#select-tts-voice').val();

        StudioState.markDirty(true);
        Voice.renderCapabilitiesStrip();
        Projects.triggerAutosave();
      });

      // Voice change
      $(document).on('change', '#select-tts-voice', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        StudioState.current.project.voice = $(this).val();
        StudioState.markDirty(true);
        Voice.renderCapabilitiesStrip();
        Projects.triggerAutosave();
      });

      // Voice profile lock
      $(document).on('change', '#check-voice-locked', function() {
        if (!StudioState.current) return;
        const pid = StudioState.current.id;
        const locked = $(this).is(':checked');

        StudioState.setBusy(true);
        StudioApi.request('/projects/' + pid + '/voice_lock', 'POST', { locked: locked })
          .then(function(updatedRow) {
            Projects.sync(updatedRow);
            StudioState.setNotice(locked ? 'Voice profile locked to project.' : 'Voice profile unlocked.');
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            StudioState.setBusy(false);
          });
      });

      // Delivery preset & Speech rate
      $(document).on('change', '#select-delivery-preset', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        StudioState.current.project.delivery_preset = $(this).val();
        StudioState.markDirty(true);
        Projects.triggerAutosave();
      });

      $(document).on('input change', '#range-speech-rate', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        const val = +$(this).val();
        StudioState.current.project.speech_rate = val;
        $('#speech-rate-label').text('Speech rate: ' + val + '%');
        StudioState.markDirty(true);
        Projects.triggerAutosave();
      });

      // Add dictionary entry
      $(document).on('click', '#btn-add-dict-entry', function() {
        const key = $('#input-dict-key').val().trim();
        const val = $('#input-dict-val').val().trim();
        if (!key || !val || !StudioState.current) return;

        const pid = StudioState.current.id;
        const p = StudioState.current.project;
        const updatedDict = Object.assign({}, p.pronunciation_dict || {});
        updatedDict[key] = val;

        StudioState.setBusy(true);
        StudioApi.request('/projects/' + pid + '/pronunciation_dict', 'POST', { dictionary: updatedDict })
          .then(function(updatedRow) {
            Projects.sync(updatedRow);
            $('#input-dict-key').val('');
            $('#input-dict-val').val('');
            StudioState.setNotice(`Pronunciation rule added for "${key}".`);
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            StudioState.setBusy(false);
          });
      });

      // Remove dictionary entry
      $(document).on('click', '.btn-remove-dict-entry', function() {
        const key = $(this).data('key');
        if (!key || !StudioState.current) return;

        const pid = StudioState.current.id;
        const p = StudioState.current.project;
        const updatedDict = Object.assign({}, p.pronunciation_dict || {});
        delete updatedDict[key];

        StudioState.setBusy(true);
        StudioApi.request('/projects/' + pid + '/pronunciation_dict', 'POST', { dictionary: updatedDict })
          .then(function(updatedRow) {
            Projects.sync(updatedRow);
            StudioState.setNotice(`Pronunciation rule "${key}" removed.`);
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            StudioState.setBusy(false);
          });
      });

      // Test pronunciation normalizer
      $(document).on('click', '#btn-test-normalize', function() {
        const text = $('#input-test-normalize-text').val().trim();
        if (!text || !StudioState.current) return;
        const p = StudioState.current.project;

        StudioState.setBusy(true);
        StudioApi.request('/voice/normalize', 'POST', {
          text: text,
          dictionary: p.pronunciation_dict || {}
        })
        .then(function(res) {
          $('#norm-test-result-box').html(`
            <div><b>Spoken Telugu:</b> ${$('<div>').text(res.spoken_text).html()}</div>
            <div style="color:var(--muted);margin-top:4px;"><b>Roman Captions:</b> ${$('<div>').text(res.roman_text).html()}</div>
          `).show();
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
      });

      // Conversational rewrite check
      $(document).on('click', '#btn-check-rewrite', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        const p = StudioState.current.project;
        const sid = StudioState.voiceLabSceneId || (p.scenes && p.scenes[0] && p.scenes[0].id);
        const curScene = p.scenes.find(s => s.id === sid);
        if (!curScene || !curScene.narration || !curScene.narration.trim()) {
          StudioState.setNotice('Scene has no Telugu narration to rewrite.', true);
          return;
        }

        StudioState.setBusy(true);
        StudioApi.request('/voice/rewrite_preview', 'POST', { text: curScene.narration })
          .then(function(res) {
            rewritePreviewData = res;
            Voice.renderRewritePreview(res);
            if (!res.changed) {
              StudioState.setNotice('Narration already has natural spoken cadence. No colloquial changes needed.');
            }
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            StudioState.setBusy(false);
          });
      });

      // Apply conversational rewrite
      $(document).on('click', '#btn-apply-rewrite', function() {
        if (!rewritePreviewData || !rewritePreviewData.proposed || !StudioState.current) return;
        const p = StudioState.current.project;
        const sid = StudioState.voiceLabSceneId || (p.scenes && p.scenes[0] && p.scenes[0].id);
        Scenes.updateSceneField(sid, {
          narration: rewritePreviewData.proposed,
          spoken_text: rewritePreviewData.proposed
        });
        rewritePreviewData = null;
        $('#rewrite-preview-box').empty().hide();
        Scenes.renderScriptEditor();
        Scenes.renderSceneVisuals();
        Voice.renderVoiceLabPanel();
        StudioState.setNotice('Colloquial spoken phrasing approved and applied to scene narration.');
      });

      $(document).on('click', '#btn-dismiss-rewrite', function() {
        rewritePreviewData = null;
        $('#rewrite-preview-box').empty().hide();
      });

      // Voice Lab scene selection change
      $(document).on('change', '#select-voicelab-scene', function() {
        StudioState.voiceLabSceneId = $(this).val();
        StudioState.candidateTake = null;
        Voice.renderVoiceLabPanel();
      });

      // Generate candidate preview take
      $(document).on('click', '#btn-generate-preview-take', function() {
        Voice.generatePreviewTake();
      });

      // Accept take
      $(document).on('click', '.btn-accept-voice-take', function() {
        const takeId = $(this).data('take-id');
        Voice.acceptTake(takeId);
      });

      $(document).on('studio:editor-populated studio:takes-updated', function() {
        Voice.populateVoiceControls();
        Voice.renderVoiceLabPanel();
      });
    },

    populateVoiceOptions: function(prov) {
      const $select = $('#select-tts-voice').empty();
      if (prov === 'sarvam') {
        $select.append(`
          <option value="pavithra">Pavithra · Bulbul v3 (Telugu Female)</option>
          <option value="meera">Meera · Bulbul v3 (Telugu Female)</option>
          <option value="arvind">Arvind · Bulbul v3 (Telugu Male)</option>
          <option value="amartya">Amartya · Bulbul v3 (Telugu Male)</option>
        `);
      } else if (prov === 'upload') {
        $select.append('<option value="human_upload">Uploaded Audio (Human Voice)</option>');
      } else {
        $select.append(`
          <option value="te-IN-ShrutiNeural">Shruti · Telugu female</option>
          <option value="te-IN-MohanNeural">Mohan · Telugu male</option>
        `);
      }
    },

    renderVoiceControls: function() {
      Voice.populateVoiceControls();
    },

    loadCapabilities: function() {
      return StudioApi.request('/voice/capabilities')
        .then(function(caps) {
          StudioState.capabilities = caps || [];
          Voice.renderCapabilitiesStrip();
        })
        .catch(function() {});
    },

    populateVoiceControls: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const p = StudioState.current.project;

      const prov = p.tts_provider || 'edge';
      $('#select-voice-provider, #select-tts-provider').val(prov);
      Voice.populateVoiceOptions(prov);
      $('#select-voice-speaker, #select-tts-voice').val(p.voice || 'te-IN-ShrutiNeural');

      $('#check-voice-lock, #check-voice-locked').prop('checked', !!p.voice_locked);
      $('#select-delivery-preset').val(p.delivery_preset || 'conversational');
      $('#range-speech-rate').val(p.speech_rate || 1.0).prop('disabled', prov === 'upload');
      $('#label-speech-rate, #speech-rate-label').text((p.speech_rate || 1.0) + 'x');

      Voice.renderCapabilitiesStrip();
      Voice.renderDictionaryList();
    },

    renderCapabilitiesStrip: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const p = StudioState.current.project;
      const caps = StudioState.capabilities || [];

      const curCap = caps.find(c => c.voice_id === p.voice && c.provider === p.tts_provider)
        || caps.find(c => c.voice_id === p.voice)
        || caps[0];

      const $strip = $('#voice-cap-badges, #voice-capabilities-badges').empty();
      if (!curCap) return;

      if (curCap.supported_controls) {
        curCap.supported_controls.forEach(function(ctrl) {
          $strip.append(`<span class="badge-cap-ok">✓ ${ctrl.toUpperCase()}</span>`);
        });
      }
      if (curCap.unsupported_controls) {
        curCap.unsupported_controls.forEach(function(ctrl) {
          $strip.append(`<span class="badge-cap-no" title="Unsupported controls are visibly disabled">✕ ${ctrl}</span>`);
        });
      }
      $strip.append(`<span class="badge-cap-info">Format: ${(curCap.audio_formats && curCap.audio_formats[0]) || 'mp3'}</span>`);
      $strip.append(`<span class="badge-cap-info">Timing: ${curCap.timing_metadata || 'sentence'}</span>`);
      $strip.append(`<span class="badge-cap-info">Limit: ${curCap.max_characters_per_request || 5000} chars</span>`);
      const costPer1k = curCap.estimated_cost_per_1k_chars || 0;
      $strip.append(`<span class="badge-cap-info">Est. Cost: ₹${(costPer1k * 1000).toFixed(2)}/1M chars</span>`);
    },

    renderDictionaryList: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const p = StudioState.current.project;
      const dict = p.pronunciation_dict || {};
      const keys = Object.keys(dict);

      $('#dict-count-label').text(keys.length + ' custom mappings');
      const $list = $('#dict-entries-list').empty();

      keys.forEach(function(k) {
        const val = dict[k];
        const $row = $(`
          <div class="dict-row" style="background:#fff;padding:6px 12px;border:1px solid var(--line);border-radius:6px;">
            <b style="flex:1;">${$('<div>').text(k).html()}</b>
            <span>→</span>
            <span style="flex:1;">${$('<div>').text(val).html()}</span>
            <button type="button" class="ghost btn-remove-dict-entry" data-key="${$('<div>').text(k).html()}">✕</button>
          </div>
        `);
        $list.append($row);
      });
    },

    renderRewritePreview: function(res) {
      const $box = $('#rewrite-preview-box').empty().show();
      let diffHtml = '';
      if (res.diff) {
        res.diff.forEach(function(part) {
          if (part.type === 'removed') {
            diffHtml += `<span class="diff-word-del">${$('<div>').text(part.text).html()}</span>`;
          } else if (part.type === 'added') {
            diffHtml += `<span class="diff-word-add">${$('<div>').text(part.text).html()}</span>`;
          } else {
            diffHtml += `<span>${$('<div>').text(part.text).html()} </span>`;
          }
        });
      }

      const actionsHtml = res.changed ? `
        <div class="actions">
          <button type="button" class="primary" id="btn-apply-rewrite">✓ Approve & Apply to Scene Narration</button>
          <button type="button" id="btn-dismiss-rewrite">Dismiss</button>
        </div>
      ` : '<small class="muted">No colloquial changes needed for this text.</small>';

      $box.html(`
        <div class="diff-container">
          <b>Conversational Polish Diff:</b>
          <p>${$('<div>').text(res.note || '').html()}</p>
          <div style="margin:10px 0;padding:12px;background:#fafafa;border:1px solid var(--line);border-radius:6px;">
            ${diffHtml}
          </div>
          ${actionsHtml}
        </div>
      `);
    },

    renderVoiceLabPanel: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const p = StudioState.current.project;
      const scenes = p.scenes || [];

      // Populate test scene dropdown
      const $select = $('#select-voicelab-scene').empty();
      scenes.forEach(function(sc, idx) {
        const textSnippet = (sc.narration && sc.narration.slice(0, 40)) || 'Untitled';
        $select.append(`<option value="${sc.id}">Scene ${idx + 1}: ${$('<div>').text(textSnippet).html()}...</option>`);
      });
      if (StudioState.voiceLabSceneId) {
        $select.val(StudioState.voiceLabSceneId);
      } else if (scenes.length) {
        StudioState.voiceLabSceneId = scenes[0].id;
        $select.val(scenes[0].id);
      }

      const sid = StudioState.voiceLabSceneId;
      const curScene = scenes.find(s => s.id === sid) || scenes[0];

      // Text tiers preview
      const $tiersBox = $('#voicelab-text-tiers-preview');
      if (curScene) {
        $tiersBox.html(`
          <div style="background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0;font-size:13px;">
            <div><b>Source Narration (Telugu):</b> ${$('<div>').text(curScene.narration || '(empty)').html()}</div>
            <div style="margin-top:4px;"><b>Spoken Telugu (Normalized):</b> ${$('<div>').text(curScene.spoken_text || curScene.narration || '(empty)').html()}</div>
            <div style="color:var(--muted);margin-top:4px;"><b>Display Captions (Roman):</b> ${$('<div>').text(curScene.roman || '(empty)').html()}</div>
          </div>
        `).show();
      } else {
        $tiersBox.empty().hide();
      }

      // A/B Comparison Cards
      const $abBox = $('#voicelab-ab-cards-container');
      const cand = StudioState.candidateTake;
      if (cand && curScene) {
        const baselineAudio = curScene.audio_asset
          ? `<audio controls src="/api/assets/${curScene.audio_asset}/media"></audio>`
          : '<small class="muted">No baseline audio recorded yet</small>';

        $abBox.html(`
          <div>
            <div class="ab-grid">
              <div class="ab-card">
                <h4><b>Baseline Voice</b><span class="badge">Baseline</span></h4>
                ${baselineAudio}
                <small class="muted">Current project scene audio asset</small>
              </div>
              <div class="ab-card active-take">
                <h4><b>Candidate Take (${$('<div>').text(cand.display_name || '').html()})</b><span class="badge approved">Candidate Take</span></h4>
                <audio controls autoplay src="${cand.audio_url}"></audio>
                <small>Duration: <b>${cand.duration ? cand.duration.toFixed(2) : 0}s</b> · Provider: ${cand.provider} · Preset: ${cand.preset}</small>
                <div class="actions" style="margin-top:6px;">
                  <button type="button" class="primary btn-accept-voice-take" data-take-id="${cand.take_id || cand.id}">
                    ✓ Accept This Take for Scene
                  </button>
                </div>
              </div>
            </div>
            <div class="ab-metrics">
              <div><b>Duration:</b> ${cand.duration ? cand.duration.toFixed(2) : 0}s</div>
              <div><b>Script Match:</b> ✓ Identical text synthesized</div>
              <div><b>Est. Cost:</b> ₹${((cand.estimated_cost || 0) * 100).toFixed(4)}</div>
              <div><b>Cache Status:</b> Contract key ${cand.contract_key ? cand.contract_key.slice(0, 8) : ''}...</div>
            </div>
          </div>
        `).show();
      } else {
        $abBox.empty().hide();
      }

      // Take History table
      const takes = StudioState.takeHistory || [];
      const $takesTable = $('#voicelab-takes-tbody').empty();
      $('#voicelab-takes-count').text(takes.length + ' recorded takes');

      takes.forEach(function(tk) {
        const scIdx = scenes.findIndex(s => s.id === tk.scene_id);
        const sceneLabel = scIdx >= 0 ? `Scene ${scIdx + 1}` : 'General';
        const acceptedBadge = tk.accepted
          ? '<span class="badge approved">Accepted</span>'
          : '<span class="badge">Take</span>';
        const acceptBtn = !tk.accepted
          ? `<button type="button" class="btn-accept-voice-take" data-take-id="${tk.id}">Accept</button>`
          : '';

        const rowHtml = `
          <tr>
            <td><code>${(tk.id || '').slice(0, 10)}</code></td>
            <td>${sceneLabel}</td>
            <td>${tk.voice_id} (${tk.provider})</td>
            <td>${tk.preset || 'conversational'}</td>
            <td>${tk.duration ? tk.duration.toFixed(1) : 0}s</td>
            <td>${acceptedBadge}</td>
            <td>
              <div style="display:flex;gap:6px;align-items:center;">
                <audio style="height:28px;width:150px;" controls src="${tk.audio_url}"></audio>
                ${acceptBtn}
              </div>
            </td>
          </tr>
        `;
        $takesTable.append(rowHtml);
      });
    },

    generatePreviewTake: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const pid = StudioState.current.id;
      const p = StudioState.current.project;
      const sid = StudioState.voiceLabSceneId || (p.scenes && p.scenes[0] && p.scenes[0].id);
      const curScene = p.scenes.find(s => s.id === sid);
      if (!curScene) return;

      const textToSynthesize = curScene.spoken_text || curScene.narration;
      if (!textToSynthesize || !textToSynthesize.trim()) {
        StudioState.setNotice('Enter Telugu narration before generating a voice preview.', true);
        return;
      }

      const explicitRegen = $('#check-explicit-regenerate').is(':checked');
      StudioState.setBusy(true);

      StudioApi.request('/voice/preview', 'POST', {
        project_id: pid,
        scene_id: curScene.id,
        text: textToSynthesize,
        provider: p.tts_provider || 'edge',
        voice: p.voice || 'te-IN-ShrutiNeural',
        preset: p.delivery_preset || 'conversational',
        rate: p.speech_rate || 0,
        pitch: 0,
        dictionary: p.pronunciation_dict || {},
        regenerate: explicitRegen
      })
      .then(function(take) {
        StudioState.candidateTake = take;
        StudioState.setNotice(`Candidate take generated (${take.duration.toFixed(1)}s, ${take.display_name}).`);
        return StudioApi.request('/projects/' + pid + '/takes');
      })
      .then(function(takes) {
        StudioState.takeHistory = takes || [];
        Voice.renderVoiceLabPanel();
      })
      .catch(function(err) {
        StudioState.setNotice(err.message, true);
      })
      .finally(function() {
        StudioState.setBusy(false);
      });
    },

    acceptTake: function(takeId) {
      if (!StudioState.current || !takeId) return;
      const pid = StudioState.current.id;
      StudioState.setBusy(true);

      StudioApi.request('/projects/' + pid + '/takes/' + takeId + '/accept', 'POST')
        .then(function(res) {
          Projects.sync(res.project);
          StudioState.setNotice('Voice take accepted and applied to project (v' + res.project.revision + ').');
          return StudioApi.request('/projects/' + pid + '/takes');
        })
        .then(function(takes) {
          StudioState.takeHistory = takes || [];
          Voice.renderVoiceLabPanel();
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    }
  };

  window.Voice = Voice;
  window.StudioVoice = Voice;
})(jQuery);
