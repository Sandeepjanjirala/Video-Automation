// settings.js — Settings modal, masked provider keys, health check

const StudioSettings = (() => {
  function init() {
    // Open settings modal
    $(document).on('click', '#btn-open-settings', function() {
      openModal();
    });

    // Close settings modal
    $(document).on('click', '#btn-close-settings, .modal-backdrop-settings', function() {
      closeModal();
    });

    // Prevent backdrop close when clicking modal body
    $(document).on('click', '#settings-modal .modal', function(e) {
      e.stopPropagation();
    });

    // TTS Provider toggle conditional fields
    $(document).on('change', '#setting-tts-provider', function() {
      const val = $(this).val();
      $('.tts-provider-fields').hide();
      $(`.tts-provider-${val}`).show();
    });

    // Save settings
    $(document).on('click', '#btn-save-settings', async function() {
      await saveSettings();
    });

    // Health check
    $(document).on('click', '#btn-check-health', async function() {
      await checkHealth();
    });
  }

  function openModal() {
    populateForm();
    $('#settings-modal').fadeIn(150);
  }

  function closeModal() {
    $('#settings-modal').fadeOut(150);
  }

  function populateForm() {
    const cfg = StudioState.getCfg() || {};
    $('#setting-online-enabled').prop('checked', !!cfg.online_enabled);
    
    // Clear passwords so existing keys are not exposed in DOM
    $('#setting-gemini-key').val('').attr('placeholder', cfg.gemini_key_configured ? '●●●●●●●● (saved key)' : 'Enter Gemini key');
    $('#hint-gemini-key').text(cfg.gemini_key_configured ? 'A key is saved.' : 'Needed for research and AI drafts.');

    $('#setting-gemini-model').val(cfg.gemini_model || 'gemini-2.5-flash');
    $('#setting-daily-ai-calls').val(cfg.daily_ai_calls || 20);

    $('#setting-pixabay-key').val('').attr('placeholder', cfg.pixabay_key_configured ? '●●●●●●●● (saved key)' : 'Enter Pixabay key');
    $('#hint-pixabay-key').text(cfg.pixabay_key_configured ? 'A key is saved.' : 'Needed for stock search and downloads.');

    const tts = cfg.tts_provider || 'edge';
    $('#setting-tts-provider').val(tts);
    $('.tts-provider-fields').hide();
    $(`.tts-provider-${tts}`).show();

    $('#setting-sarvam-key').val('').attr('placeholder', cfg.sarvam_key_configured ? '●●●●●●●● (saved key)' : 'Enter Sarvam key');
    $('#hint-sarvam-key').text(cfg.sarvam_key_configured ? 'A key is saved.' : 'Needed for Sarvam Bulbul neural synthesis.');

    $('#setting-azure-key').val('').attr('placeholder', cfg.azure_key_configured ? '●●●●●●●● (saved key)' : 'Enter Azure key');
    $('#setting-azure-region').val(cfg.azure_region || 'centralindia');

    $('#setting-ffmpeg').val(cfg.ffmpeg || '');
    $('#setting-ffprobe').val(cfg.ffprobe || '');

    $('#settings-health-output').empty().hide();
  }

  async function saveSettings() {
    try {
      StudioState.setBusy(true);
      const cfg = StudioState.getCfg() || {};
      const payload = {
        online_enabled: $('#setting-online-enabled').is(':checked'),
        gemini_model: $('#setting-gemini-model').val().trim(),
        daily_ai_calls: parseInt($('#setting-daily-ai-calls').val(), 10) || 20,
        tts_provider: $('#setting-tts-provider').val(),
        azure_region: $('#setting-azure-region').val().trim(),
        ffmpeg: $('#setting-ffmpeg').val().trim(),
        ffprobe: $('#setting-ffprobe').val().trim()
      };

      // Only send keys if non-empty (blank preserves existing server-side key)
      const gemini = $('#setting-gemini-key').val().trim();
      if (gemini) payload.gemini_key = gemini;

      const pixabay = $('#setting-pixabay-key').val().trim();
      if (pixabay) payload.pixabay_key = pixabay;

      const sarvam = $('#setting-sarvam-key').val().trim();
      if (sarvam) payload.sarvam_key = sarvam;

      const azure = $('#setting-azure-key').val().trim();
      if (azure) payload.azure_key = azure;

      const updated = await StudioApi.req('/settings', payload, 'POST');
      StudioState.setCfg(updated);
      StudioState.tell('Settings saved.');
      closeModal();
    } catch (e) {
      StudioState.tell(e.message, true);
    } finally {
      StudioState.setBusy(false);
    }
  }

  async function checkHealth() {
    try {
      StudioState.setBusy(true);
      const health = await StudioApi.req('/health');
      $('#settings-health-output')
        .text(JSON.stringify(health, null, 2))
        .show();
    } catch (e) {
      StudioState.tell(e.message, true);
    } finally {
      StudioState.setBusy(false);
    }
  }

  return {
    init,
    openModal,
    closeModal,
    populateForm,
    saveSettings,
    checkHealth
  };
})();

window.StudioSettings = StudioSettings;
