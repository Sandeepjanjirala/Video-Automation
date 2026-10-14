/**
 * Telugu Reel Studio - Media Library & Asset Uploads (Stage M8)
 */

(function($) {
  'use strict';

  let libraryTargetScene = 'all';

  const Media = {
    init: function() {
      // Reusable media library modal open
      $(document).on('click', '#btn-open-library-global, #btn-open-media-library', function() {
        Media.openLibrary('all');
      });

      $(document).on('click', '.btn-open-library-single', function() {
        const sid = $(this).data('scene-id');
        Media.openLibrary(sid);
      });

      $(document).on('click', '#btn-close-media-modal', function() {
        $('#media-library-modal, #modal-media-library').hide();
      });

      // Library asset assignment
      $(document).on('click', '.btn-assign-asset-scene', function() {
        const aid = $(this).data('asset-id');
        Media.assignAsset(aid, libraryTargetScene);
      });

      $(document).on('click', '.btn-assign-asset-all', function() {
        const aid = $(this).data('asset-id');
        Media.assignAsset(aid, 'all');
      });

      // Library modal file upload
      $(document).on('change', '#input-library-upload-mp4', function(e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        Media.uploadFile(file, 'video')
          .then(function() {
            return Media.loadLibraryAssets();
          })
          .then(function() {
            StudioState.setNotice('Clip added to library.');
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            e.target.value = '';
          });
      });

      // Scene-specific video upload
      $(document).on('change', '.scene-upload-video-input', function(e) {
        const sid = $(this).data('scene-id');
        const file = e.target.files && e.target.files[0];
        if (!file) return;

        StudioState.setBusy(true);
        Media.uploadFile(file, 'video')
          .then(function(asset) {
            Scenes.updateSceneField(sid, {
              video_asset: asset.id,
              visual: 'upload',
              rights_reviewed: true,
              trim_start: 0
            });
            Scenes.renderSceneVisuals();
            StudioState.setNotice('Uploaded video assigned to scene. Save project to keep selection.');
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            StudioState.setBusy(false);
            e.target.value = '';
          });
      });

      // Background music upload
      $(document).on('change', '#input-upload-music, #file-upload-music', function(e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;

        StudioState.setBusy(true);
        Media.uploadFile(file, 'audio')
          .then(function(asset) {
            if (!StudioState.current || !StudioState.current.project) return;
            const p = StudioState.current.project;
            p.music_asset = asset.id;
            p.music_rights_reviewed = false;
            StudioState.markDirty(true);
            Media.renderMusicPanel();
            Projects.triggerAutosave();
            Projects.checkPreflight();
            StudioState.setNotice('Music uploaded. Save the project to keep this selection.');
          })
          .catch(function(err) {
            StudioState.setNotice(err.message, true);
          })
          .finally(function() {
            StudioState.setBusy(false);
            e.target.value = '';
          });
      });

      // Remove music
      $(document).on('click', '#btn-remove-music', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        StudioState.current.project.music_asset = '';
        StudioState.markDirty(true);
        Media.renderMusicPanel();
        Projects.triggerAutosave();
        Projects.checkPreflight();
      });

      // Music gain & rights
      $(document).on('input change', '#range-music-gain', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        const val = +$(this).val();
        StudioState.current.project.music_gain = val;
        $('#music-gain-label').text('Music level: ' + Math.round(val * 100) + '%');
        StudioState.markDirty(true);
        Projects.triggerAutosave();
      });

      $(document).on('change', '#check-music-rights', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        StudioState.current.project.music_rights_reviewed = $(this).is(':checked');
        StudioState.markDirty(true);
        Projects.triggerAutosave();
        Projects.checkPreflight();
      });

      // Pixabay stock search
      $(document).on('click', '.btn-find-stock', function() {
        const sid = $(this).data('scene-id');
        const s = StudioState.current.project.scenes.find(x => x.id === sid);
        if (!s || !s.keywords) return;

        const $box = $(`#stock-results-${sid}`).html('<p class="muted">Searching Pixabay stock...</p>');
        StudioState.setBusy(true);

        StudioApi.request('/stock?q=' + encodeURIComponent(s.keywords))
          .then(function(hits) {
            if (!hits || !hits.length) {
              $box.html('<p class="muted">No stock clips found. Try different keywords.</p>');
              return;
            }
            let html = '<div class="stockgrid">';
            hits.forEach(function(h) {
              html += `
                <div class="stockcard">
                  <p><b>${$('<div>').text(h.tags || 'Video').html()}</b></p>
                  <video src="${h.preview_url}" style="width:100%;height:100px;object-fit:cover;border-radius:4px;" controls muted preload="metadata"></video>
                  <p><small>${h.duration}s · by ${h.user}</small></p>
                  <button type="button" class="btn-select-stock-hit" data-scene-id="${sid}" data-download-url="${h.download_url}" data-title="${h.tags || 'Pixabay video'}">Use this clip</button>
                </div>
              `;
            });
            html += '</div>';
            $box.html(html);
          })
          .catch(function(err) {
            $box.html(`<p class="muted" style="color:#991b1b;">Search failed: ${$('<div>').text(err.message).html()}</p>`);
          })
          .finally(function() {
            StudioState.setBusy(false);
          });
      });

      // Stock selection -> queue stock job
      $(document).on('click', '.btn-select-stock-hit', function() {
        const sid = $(this).data('scene-id');
        const downloadUrl = $(this).data('download-url');
        const title = $(this).data('title');
        Jobs.queue('stock', {
          scene_id: sid,
          query: title,
          direct_url: downloadUrl
        });
      });

      $(document).on('studio:editor-populated', function() {
        Media.renderMusicPanel();
      });
    },

    uploadFile: function(file, kind) {
      const fd = new FormData();
      fd.append('file', file);
      return StudioApi.upload('/assets', fd).then(function(asset) {
        if (kind && asset.kind !== kind) {
          throw new Error('Please select a ' + kind + ' file.');
        }
        return asset;
      });
    },

    openLibrary: function(targetSceneId) {
      libraryTargetScene = targetSceneId || 'all';
      $('#media-library-modal, #modal-media-library').show();
      Media.loadLibraryAssets();
    },

    loadLibraryAssets: function() {
      const $grid = $('#media-assets-grid, #media-library-grid').html('<p class="muted">Loading media assets...</p>');
      return StudioApi.request('/assets').then(function(assets) {
        const videoAssets = (assets || []).filter(a => a.kind === 'video');
        if (!videoAssets.length) {
          $grid.html('<p class="muted">No media assets in library yet. Upload clips or search Pixabay stock above.</p>');
          return;
        }

        let html = '';
        videoAssets.forEach(function(a) {
          const name = a.name || a.id;
          const durStr = a.duration ? a.duration.toFixed(1) + 's' : '';
          const singleBtn = (libraryTargetScene !== 'all')
            ? `<button type="button" class="primary btn-assign-asset-scene" data-asset-id="${a.id}">Use for this scene</button>`
            : '';

          html += `
            <div class="mediacard">
              <video class="mediathumb" preload="metadata" src="/api/assets/${a.id}/media"></video>
              <div class="mediainfo">
                <b>${$('<div>').text(name).html()}</b>
                <small>${durStr} · ${a.provider || 'upload'}</small>
                <div class="mediaactions">
                  ${singleBtn}
                  <button type="button" class="btn-assign-asset-all" data-asset-id="${a.id}">Use across all scenes</button>
                </div>
              </div>
            </div>
          `;
        });
        $grid.html(html);
      });
    },

    assignAsset: function(assetId, target) {
      if (!StudioState.current) return;
      StudioState.setBusy(true);
      const pid = StudioState.current.id;

      if (target === 'all') {
        StudioApi.request('/projects/' + pid + '/assign-media', 'POST', {
          asset_id: assetId,
          scene_ids: ['all']
        })
        .then(function(updatedRow) {
          Projects.sync(updatedRow);
          $('#modal-media-library').hide();
          StudioState.setNotice('Clip assigned to all scenes.');
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
      } else {
        Scenes.updateSceneField(target, {
          video_asset: assetId,
          visual: 'upload',
          rights_reviewed: true,
          trim_start: 0
        });
        $('#modal-media-library').hide();
        Scenes.renderSceneVisuals();
        StudioState.setNotice('Clip assigned to scene. Save project to keep selection.');
        StudioState.setBusy(false);
      }
    },

    renderMusicPanel: function() {
      if (!StudioState.current || !StudioState.current.project) return;
      const p = StudioState.current.project;

      const $audioBox = $('#music-player-container');
      const $controlsBox = $('#music-controls-container');

      if (p.music_asset) {
        $audioBox.html(`<audio controls src="/api/assets/${p.music_asset}/media"></audio>`).show();
        $('#input-music-asset').val(p.music_asset);
        $('#btn-remove-music').show();
      } else {
        $audioBox.empty().hide();
        $('#input-music-asset').val('');
        $('#btn-remove-music').hide();
      }
      $controlsBox.show();
      $('#range-music-gain').val(p.music_gain !== undefined ? p.music_gain : 0.12);
      $('#music-gain-label').text('Music level: ' + Math.round((p.music_gain !== undefined ? p.music_gain : 0.12) * 100) + '%');
      $('#check-music-rights').prop('checked', !!p.music_rights_reviewed);
    }
  };

  window.Media = Media;
  window.StudioMedia = Media;
})(jQuery);
