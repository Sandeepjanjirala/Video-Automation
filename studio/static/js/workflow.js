/** Low-cost scene workflow. No video generation calls are made by this module. */
(function($) {
  'use strict';
  let promptTarget = null;
  let textTarget = null;
  let requestVersion = 0;
  function currentScene(target) {
    if (!target || StudioState.current?.id !== target.pid) return null;
    return StudioState.current.project.scenes.find(s => s.id === target.sid);
  }
  function targetFor(button) {
    return {pid: StudioState.current?.id, sid: $(button).data('scene-id')};
  }
  function buildPrompt(scene, seconds) {
    const landscape = StudioState.format().width > StudioState.format().height;
    return `Create a ${seconds}-second cinematic ${landscape ? 'landscape video, 16:9, for a Telugu long-form YouTube video' : 'vertical video, 9:16, for a Telugu educational or storytelling reel'}.
Scene subject / action: ${scene.keywords || scene.graphic_title || '[Describe the subject and one clear action in English]'}.
Narration context (do not render as text): ${scene.narration || scene.roman || '[Add narration context]'}
Use one coherent shot, natural motion, realistic lighting, clear composition and a gentle camera movement. Keep the subject inside the central safe area; leave the bottom third clear for captions added later.
No text, captions, logos, watermarks, voiceover or music. Avoid distorted faces, hands and flickering objects. For historical or news topics, make an illustrative scene, never fabricated evidence.
Match the subject, location, clothing and period to this scene. If continuity matters, describe the same character and setting here.`;
  }
  function savePrompt() {
    if (!currentScene(promptTarget)) throw new Error('This scene is no longer open. Reopen its Prompt button.');
    Scenes.updateSceneField(promptTarget.sid, {generation_prompt: $('#scene-generation-prompt').val(), prompt_seconds: Number($('#prompt-seconds').val())});
  }
  const Workflow = {
    buildPrompt,
    init: function() {
      $(document).on('click', '.btn-scene-prompt', function() {
        promptTarget = targetFor(this);
        const s = currentScene(promptTarget);
        if (!s) return;
        $('#prompt-seconds').val(String(s.prompt_seconds || 5));
        $('#scene-generation-prompt').val(s.generation_prompt || buildPrompt(s, s.prompt_seconds || 5));
        $('#prompt-feedback').text('Prompt creation is local and free.');
        $('#scene-prompt-modal').show();
      });
      $(document).on('click', '#btn-refresh-prompt', function() {
        const s = currentScene(promptTarget);
        if (s) $('#scene-generation-prompt').val(buildPrompt(s, Number($('#prompt-seconds').val())));
      });
      $(document).on('click', '#btn-save-prompt, #btn-copy-prompt', async function() {
        try {
          savePrompt();
          if (this.id === 'btn-copy-prompt') {
            try {
              await navigator.clipboard.writeText($('#scene-generation-prompt').val());
              $('#prompt-feedback').text('Prompt saved and copied. Upload the generated clip back to this scene.');
            } catch (_) {
              $('#scene-generation-prompt').trigger('focus').trigger('select');
              $('#prompt-feedback').text('Prompt saved. Clipboard is unavailable here; press Ctrl+C to copy the selected text.');
            }
          } else $('#prompt-feedback').text('Prompt saved with the scene.');
        } catch (e) { $('#prompt-feedback').text(e.message); }
      });
      $(document).on('click', '.btn-scene-transliterate', function() {
        textTarget = targetFor(this);
        const s = currentScene(textTarget);
        if (!s) return;
        textTarget.roman = s.roman || '';
        requestVersion++;
        $('#transliteration-source').val(textTarget.roman);
        $('#transliteration-result').val(s.narration || '');
        $('#transliteration-feedback').text('');
        $('#transliteration-modal').show();
      });
      $(document).on('click', '#btn-convert-telugu', async function() {
        const target = textTarget;
        const version = ++requestVersion;
        if (!target?.roman.trim()) return $('#transliteration-feedback').text('Enter Roman Telugu in the scene first.');
        StudioState.setBusy(true);
        $('#transliteration-feedback').text('Converting this scene…');
        try {
          const result = await StudioApi.request('/voice/transliterate', 'POST', {text: target.roman, provider: $('#transliteration-provider').val()});
          if (version !== requestVersion || !currentScene(target)) return;
          $('#transliteration-result').val(result.telugu);
          $('#transliteration-feedback').text(result.cached ? 'Reused saved conversion. Review and apply.' : 'Conversion ready. Review and apply.');
        } catch (e) {
          if (version === requestVersion) $('#transliteration-feedback').text(e.message);
        } finally { StudioState.setBusy(false); }
      });
      $(document).on('click', '#btn-apply-telugu', function() {
        const s = currentScene(textTarget);
        if (!s || s.roman !== textTarget.roman) return $('#transliteration-feedback').text('The source changed. Close and reopen conversion for the current scene.');
        const native = ($('#transliteration-result').val() || '').trim();
        if (!/[\u0c00-\u0c7f]/u.test(native)) return $('#transliteration-feedback').text('Enter Telugu script in the narration field.');
        Scenes.updateSceneField(s.id, {narration: native, narration_source_roman: s.roman, spoken_text: '', take_id: '', cues: []});
        Scenes.renderScriptEditor();
        $('#transliteration-modal').hide();
        StudioState.setNotice('Telugu narration applied. Your Roman Telugu source is preserved.');
      });
      $(document).on('click', '.btn-close-workflow', function() {
        $(this).closest('.modalback').hide(); requestVersion++;
      });
      $(document).on('keydown', function(e) {
        if (e.key === 'Escape') { $('#scene-prompt-modal, #transliteration-modal').hide(); requestVersion++; }
      });
    }
  };
  window.StudioWorkflow = Workflow;
})(jQuery);
