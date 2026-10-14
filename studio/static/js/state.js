/**
 * Telugu Reel Studio - Global Application State (Stage M8)
 */

(function($) {
  'use strict';

  function generateUid() {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID().replace(/-/g, '').slice(0, 12);
    }
    return Math.random().toString(36).slice(2, 14);
  }

  function createEmptyScene() {
    return {
      id: generateUid(),
      narration: '',
      spoken_text: '',
      roman: '',
      keywords: '',
      visual: 'upload',
      video_asset: '',
      audio_asset: '',
      audio_text_hash: '',
      trim_start: 0,
      fit: 'crop',
      crop_x: 0.5,
      hold_seconds: 0,
      graphic_title: '',
      rights_reviewed: false,
      narration_block_id: '',
      take_id: '',
      delivery_preset: 'conversational',
      cues: []
    };
  }

  function createEmptyProject() {
    return {
      title: 'My next story',
      topic: '',
      category: 'Storytelling',
      target_seconds: 60,
      duration_mode: 'natural',
      tts_provider: 'edge',
      voice: 'te-IN-ShrutiNeural',
      speech_rate: 0,
      delivery_preset: 'conversational',
      pronunciation_dict: {},
      voice_locked: false,
      voice_profile: {},
      narration_blocks: [],
      scenes: [createEmptyScene()],
      evidence_notes: '',
      research_report: '',
      research_sources: [],
      research_grounding: {},
      music_asset: '',
      music_gain: 0.12,
      music_rights_reviewed: false,
      caption_size: 56,
      caption_bottom: 300,
      script_approved_hash: '',
      draft_script: ''
    };
  }

  const subscribers = [];

  function notify(key, val) {
    subscribers.forEach(cb => {
      try { cb(key, val); } catch(e) { console.error('Subscriber error:', e); }
    });
  }

  const StudioState = {
    projects: [],
    current: null, // { id, revision, approved, updated, project }
    isDirty: false,
    isBusy: false,
    activeTab: 'write', // 'write' | 'voice' | 'export'
    config: null,
    capabilities: [],
    jobs: [],
    draftRecoverable: null,
    conflicts: [],
    voiceLabSceneId: '',
    candidateTake: null,
    takeHistory: [],
    preflight: null,
    stockResults: {}, // sceneId -> array of stock hits
    researchOpen: false,

    uid: generateUid,
    createEmptyScene: createEmptyScene,
    createEmptyProject: createEmptyProject,

    subscribe: function(cb) {
      if (typeof cb === 'function') subscribers.push(cb);
    },

    tell: function(msg, isError) {
      StudioState.setNotice(msg, isError);
      if (!msg) {
        $('#alert-general-notice').hide();
        return;
      }
      $('#alert-notice-text').text(msg);
      if (isError) {
        $('#alert-general-notice').addClass('error');
      } else {
        $('#alert-general-notice').removeClass('error');
      }
      $('#alert-general-notice').fadeIn(150);
    },

    setNotice: function(msg, isError) {
      if (!msg) {
        $('#studio-notice-container').empty();
        return;
      }
      const errClass = isError ? ' error' : '';
      const html = `
        <div class="notice${errClass}" role="status">
          <span>${$('<div>').text(msg).html()}</span>
          <button type="button" class="notice-dismiss">×</button>
        </div>
      `;
      $('#studio-notice-container').html(html);
    },

    setBusy: function(busy) {
      StudioState.isBusy = !!busy;
      $('button, input[type=file]').each(function() {
        if ($(this).hasClass('keep-enabled') || $(this).hasClass('allow-busy')) return;
        $(this).prop('disabled', StudioState.isBusy);
      });
      if (!StudioState.isBusy) {
        $(document).trigger('studio:busy-ended');
      }
      notify('busy', StudioState.isBusy);
    },

    _dirty: false,
    markDirty: function(dirty) {
      const val = (dirty !== false);
      StudioState._dirty = val;
      StudioState.isDirty = val;
      $(document).trigger('studio:dirty-changed');
    },

    getDirty: function() {
      return !!StudioState._dirty;
    },

    getRow: function() {
      return StudioState.current;
    },

    getProject: function() {
      return StudioState.current ? StudioState.current.project : null;
    },

    setProject: function(p) {
      if (StudioState.current) StudioState.current.project = p;
      notify('project', p);
    },

    getCfg: function() {
      return StudioState.config;
    },

    setCfg: function(cfg) {
      StudioState.config = cfg;
      notify('config', cfg);
    },

    getProjects: function() {
      return StudioState.projects;
    },

    setProjects: function(prjs) {
      StudioState.projects = prjs;
      notify('projects', prjs);
    },

    getJobs: function() {
      return StudioState.jobs;
    },

    setJobs: function(j) {
      StudioState.jobs = j;
      notify('jobs', j);
    }
  };

  $(document).on('click', '.notice-dismiss', function() {
    $('#studio-notice-container').empty();
  });

  window.StudioState = StudioState;
})(typeof jQuery !== 'undefined' ? jQuery : null);
