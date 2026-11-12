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
      narration_source_roman: '',
      generation_prompt: '',
      prompt_seconds: 5,
      stock_provider: 'auto',
      short_clip_mode: 'loop',
      visual: 'upload',
      video_asset: '',
      audio_asset: '',
      audio_text_hash: '',
      trim_start: 0,
      trim_end: 0,
      fit: 'crop',
      frame_aspect: 'full',
      zoom: 1,
      crop_x: 0.5,
      crop_y: 0.5,
      hold_seconds: 0,
      graphic_title: '',
      rights_reviewed: false,
      narration_block_id: '',
      take_id: '',
      delivery_preset: 'conversational',
      cues: [],
      extra_shots: [],
      shot_weights: [],
      emphasis: []
    };
  }

  // Mirrors studio/formats.py: an Instagram Reel (vertical, the default) or a long-form YouTube video.
  const FORMATS = {
    reel: {label: 'Instagram Reel', short: 'Reel', width: 1080, height: 1920, draft: [540, 960], maxScenes: 30,
           maxSeconds: 180, captionChars: 32, sides: [90, 140], aspect: '9:16',
           durations: [[30, '30 seconds (~60 words)'], [45, '45 seconds (~90 words)'], [60, '60 seconds (~120 words)'],
                       [90, '90 seconds (~180 words)'], [180, '3 minutes (~360 words)']]},
    youtube: {label: 'YouTube video', short: 'YouTube', width: 1920, height: 1080, draft: [960, 540], maxScenes: 400,
              maxSeconds: 3600, captionChars: 48, sides: [200, 200], aspect: '16:9',
              durations: [[300, '5 minutes (~600 words)'], [600, '10 minutes (~1,200 words)'], [900, '15 minutes (~1,800 words)'],
                          [1200, '20 minutes (~2,400 words)'], [1800, '30 minutes (~3,600 words)']]},
  };

  function formatOf(p) {
    return FORMATS[(p && p.format) || 'reel'] || FORMATS.reel;
  }

  // Bottom caption margin in output pixels (studio/formats.py caption_margin).
  function captionMargin(p) {
    const bottom = Number(p && p.caption_bottom) || 300;
    return formatOf(p).width > formatOf(p).height ? Math.round(bottom * 0.28) : bottom;
  }

  function createEmptyProject() {
    return {
      title: 'My next story',
      topic: '',
      category: 'Storytelling',
      format: 'reel',
      target_seconds: 60,
      duration_mode: 'natural',
      tts_provider: 'edge',
      voice: 'te-IN-MohanNeural',
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
      caption_style: 'classic',
      captions_enabled: true,
      caption_emphasis: 'keywords',
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
    clearedCues: {}, // sceneId -> captions cleared by a text change, restorable once
    researchOpen: false,

    uid: generateUid,
    createEmptyScene: createEmptyScene,
    createEmptyProject: createEmptyProject,
    FORMATS: FORMATS,
    formatOf: formatOf,
    captionMargin: captionMargin,
    /** The open project's format (a reel when nothing is open). */
    format: function() {
      return formatOf(StudioState.current && StudioState.current.project);
    },

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
      $('#alert-notice-text').text(msg || '');
      $('#alert-general-notice').toggleClass('error', !!isError).toggle(!!msg);
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

    _busyCount: 0,
    setBusy: function(busy) {
      StudioState._busyCount = Math.max(0, StudioState._busyCount + (busy ? 1 : -1));
      StudioState.isBusy = StudioState._busyCount > 0;
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
