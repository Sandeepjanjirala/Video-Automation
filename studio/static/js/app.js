// app.js — Main application bootstrap, tab navigation, and global initialization

const StudioApp = (() => {
  let activeTab = 'write';

  function init() {
    // Tab switching
    $(document).on('click', '.tab-btn', function() {
      const tab = $(this).data('tab');
      switchTab(tab);
    });

    // Subscriptions to state changes
    StudioState.subscribe((key, val) => {
      if (key === 'projects') {
        StudioProjects.renderProjectList(val);
      } else if (key === 'project') {
        StudioProjects.renderProject();
        StudioScenes.renderScenes();
        StudioVoice.renderVoiceControls();
        StudioProjects.updatePreflight();
      } else if (key === 'jobs') {
        StudioJobs.renderJobs(val);
      } else if (key === 'busy') {
        updateBusyState(val);
      }
    });

    // Alert dismiss
    $(document).on('click', '.alert-dismiss', function() {
      $(this).closest('.alert').fadeOut(200);
    });

    // Initial Bootstrap
    bootstrap();
  }

  function switchTab(tab) {
    activeTab = tab;
    $('.tab-btn').removeClass('active');
    $(`.tab-btn[data-tab="${tab}"]`).addClass('active');

    $('.tab-panel').hide();
    $(`#tab-${tab}`).show();

    // If switching to voice, refresh capabilities if needed
    if (tab === 'voice') {
      StudioVoice.loadCapabilities();
    }
    // If switching to export, refresh preflight
    if (tab === 'export') {
      StudioProjects.updatePreflight();
    }
  }

  function updateBusyState(busy) {
    if (busy) {
      $('button:not(.allow-busy), input:not(.allow-busy)').prop('disabled', true);
      $('#global-busy-spinner').show();
    } else {
      $('button:not(.allow-busy), input:not(.allow-busy)').prop('disabled', false);
      $('#global-busy-spinner').hide();
    }
  }

  async function bootstrap() {
    try {
      $('#app-loading-state').show();
      $('#app-main-layout').hide();

      // Bootstrap API call
      const data = await StudioApi.req('/bootstrap');
      
      // Store token and config
      const tok = data.token || data.session_token || '';
      if (tok) {
        StudioApi.setToken(tok);
      }
      const cfg = data.settings || data.cfg || {};
      if (cfg) {
        StudioState.setCfg(cfg);
      }

      // Populate project list
      const projects = data.projects || [];
      StudioState.setProjects(projects);

      $('#app-loading-state').hide();
      $('#app-main-layout').show();

      // Select first project or show welcome
      if (projects.length > 0) {
        await StudioProjects.load(projects[0].id);
      } else {
        StudioProjects.renderWelcome();
      }

      // Initial tab
      switchTab('write');
    } catch (e) {
      $('#app-loading-state').html(`
        <div style="padding:2rem;text-align:center;">
          <h2 style="color:var(--danger,#e53e3e);">Studio Launch Error</h2>
          <p class="muted">${escapeHtml(e.message)}</p>
          <p>Please check that the server is running, then <a href="javascript:location.reload()">refresh this page</a>.</p>
        </div>
      `);
    }
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  return {
    init,
    switchTab,
    bootstrap
  };
})();

window.StudioApp = StudioApp;

// Document Ready
$(document).ready(() => {
  StudioApi.init();
  StudioJobs.init();
  StudioMedia.init();
  StudioVoice.init();
  StudioScenes.init();
  StudioProjects.init();
  StudioSettings.init();
  StudioApp.init();
});
