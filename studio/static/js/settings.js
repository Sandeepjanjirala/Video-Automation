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

  // API keys: the .env file gives the defaults and a key typed here replaces one; "Use the default key from .env"
  // goes back. Fields start empty, so no key ever reaches the page.
  const KEYS = [['gemini', 'gemini_key', 'Gemini', 'Needed for research and AI drafts.'],
                ['pixabay', 'pixabay_key', 'Pixabay', 'Needed for stock search and downloads.'],
                ['pexels', 'pexels_key', 'Pexels', 'Pixabay is searched first. Add Pexels for more footage choices.'],
                ['sarvam', 'sarvam_key', 'Sarvam', 'Optional metered Telugu conversion and final voice upgrade.'],
                ['azure', 'azure_key', 'Azure Speech', 'Optional: Azure neural voices.']];

  function renderKeys(cfg) {
    KEYS.forEach(([id, key, name, help]) => {
      const source = cfg[key + '_source'] || (cfg[key + '_configured'] ? 'settings' : '');
      const envDefault = !!cfg[key + '_env_default'];
      $(`#setting-${id}-key`).val('').prop('disabled', false).attr('placeholder',
        source === 'env' ? 'Default key from .env in use (type a new key to change it)'
          : source === 'settings' ? '●●●●●●●● saved here (type a new key to change it)' : `Enter ${name} key`);
      $(`#hint-${id}-key`).text(source === 'env' ? `${help} Using the default key from your .env file; a key typed here replaces it.`
        : source === 'settings' && envDefault ? `${help} Using the key saved here instead of the .env default.`
        : source === 'settings' ? `${help} A key is saved.` : help);
      $(`#row-${id}-key-default`).prop('hidden', !(source === 'settings' && envDefault));
      $(`#setting-${id}-key-default`).prop('checked', false);
    });
  }

  function populateForm() {
    const cfg = StudioState.getCfg() || {};
    $('#setting-online-enabled').prop('checked', !!cfg.online_enabled);
    
    renderKeys(cfg);

    $('#setting-gemini-model').val(cfg.gemini_model || '');
    $('#setting-gemini-fallback-models').val(cfg.gemini_fallback_models || '');
    $('#setting-daily-ai-calls').val(cfg.daily_ai_calls || 20);
    $('#setting-access-password').val('').prop('disabled', !!cfg.access_password_from_server)
      .attr('placeholder', cfg.access_password_set ? 'A password is set (type a new one to change it)' : 'At least 8 characters');
    $('#setting-access-password-clear').prop('checked', false);
    $('#row-access-password-clear').prop('hidden', !cfg.access_password_set || !!cfg.access_password_from_server);
    if (cfg.access_password_from_server) $('#hint-access-password').text('Set on the server (STUDIO_PASSWORD); change it there.');

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
        gemini_fallback_models: $('#setting-gemini-fallback-models').val().trim(),
        daily_ai_calls: parseInt($('#setting-daily-ai-calls').val(), 10) || 20,
        azure_region: $('#setting-azure-region').val().trim(),
        ffmpeg: $('#setting-ffmpeg').val().trim(),
        ffprobe: $('#setting-ffprobe').val().trim()
      };

      const accessPassword = $('#setting-access-password').val();
      if ($('#setting-access-password-clear').is(':checked')) payload.access_password_clear = true;
      else if (accessPassword) {
        if (accessPassword.length < 8) throw new Error('Use a password of at least 8 characters for other devices.');
        payload.access_password = accessPassword;
      }

      // Only send keys if non-empty (blank preserves existing server-side key)
      const gemini = $('#setting-gemini-key').val().trim();
      if (gemini) payload.gemini_key = gemini;

      const pixabay = $('#setting-pixabay-key').val().trim();
      if (pixabay) payload.pixabay_key = pixabay;

      const pexels = $('#setting-pexels-key').val().trim();
      if (pexels) payload.pexels_key = pexels;

      const sarvam = $('#setting-sarvam-key').val().trim();
      if (sarvam) payload.sarvam_key = sarvam;

      const azure = $('#setting-azure-key').val().trim();
      if (azure) payload.azure_key = azure;

      // A blank field with "Use the default key from .env" ticked drops the key saved here.
      const defaults = KEYS.filter(([id]) => $(`#setting-${id}-key-default`).is(':checked') && !$(`#setting-${id}-key`).val().trim())
        .map(([, key]) => key);
      if (defaults.length) payload.use_default_keys = defaults;

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
