/**
 * Telugu Reel Studio - Projects & Editor Management (Stage M8)
 */

(function($) {
  'use strict';

  let autosaveTimer = null;

  const Projects = {
    load: function(pid) {
      return Projects.loadProject(pid);
    },
    create: function() {
      return Projects.createProject();
    },
    save: function() {
      return Projects.saveProject();
    },
    approve: function() {
      return Projects.approveRevision();
    },
    renderProject: function() {
      return Projects.populateEditor();
    },
    updatePreflight: function() {
      return Projects.checkPreflight();
    },
    setConflicts: function(conflicts) {
      StudioState.conflicts = conflicts || [];
      if (conflicts && conflicts.length) {
        $('#alert-conflict-content').html(`<b>Revision Conflict:</b> ${conflicts.length} manual edit(s) preserved.`);
        $('#alert-conflict').show();
      } else {
        $('#alert-conflict').hide();
      }
    },
    renderWelcome: function() {
      $('#project-editor-workspace').hide();
      $('#welcome-panel').show();
    },

    init: function() {
      // Event bindings
      $(document).on('click', '.project-item, .project', function() {
        const pid = $(this).data('id');
        if (pid) Projects.switchProject(pid);
        Projects.toggleDrawer(false);
      });

      // Phones: the project list and Settings live in a drawer opened from the top bar.
      $(document).on('click', '#btn-mobile-projects', function(e) {
        e.stopPropagation();
        Projects.toggleDrawer(!$('body').hasClass('drawer-open'));
      });
      $(document).on('click', function(e) {
        if ($('body').hasClass('drawer-open') && !$(e.target).closest('#studio-sidebar').length) Projects.toggleDrawer(false);
      });
      $(document).on('click', '#btn-new-project, #btn-open-settings', function() { Projects.toggleDrawer(false); });
      $(document).on('keydown', function(e) {
        if (e.key === 'Escape') Projects.toggleDrawer(false);
      });

      $(document).on('click', '#btn-new-story, #btn-new-project, #btn-welcome-create', function() {
        Projects.createProject();
      });

      $(document).on('click', '#btn-save-project', function() {
        Projects.saveProject();
      });

      $(document).on('click', '#btn-approve-project, #btn-approve-script, #btn-approve-voice, #btn-approve-captions, #btn-approve-export', function() {
        Projects.approveRevision();
      });

      $(document).on('click', '.project-delete', function(e) {
        e.stopPropagation();
        Projects.deleteProject($(this).data('id'));
      });

      $(document).on('click', '#btn-delete-project', function() {
        if (StudioState.current) Projects.deleteProject(StudioState.current.id);
      });

      $(document).on('click', '#btn-history-modal, #btn-open-history', function() {
        Projects.openHistoryModal();
      });

      $(document).on('click', '#btn-restore-draft', function() {
        Projects.restoreDraft();
      });

      $(document).on('click', '#btn-discard-draft', function() {
        Projects.discardDraft();
      });

      $(document).on('click', '#btn-dismiss-conflicts', function() {
        StudioState.conflicts = [];
        $('#conflicts-banner').empty();
      });

      $(document).on('click', '#btn-toggle-research', function() {
        StudioState.researchOpen = !StudioState.researchOpen;
        Projects.renderResearchView();
      });

      // Form input change handlers
      $(document).on('input change', '#input-project-title, #select-project-category, #select-project-format, #input-project-topic, #textarea-project-topic, #textarea-evidence-notes, #select-project-tone, #select-project-duration, #input-target-seconds, #select-duration-mode, .project-field-bind', function() {
        if (!StudioState.current || !StudioState.current.project) return;
        const p = StudioState.current.project;
        const id = $(this).attr('id');
        if (id === 'input-project-title') {
          p.title = $(this).val();
          $('#header-project-title').text(p.title || 'Untitled Reel');
        } else if (id === 'select-project-category') {
          p.category = $(this).val();
        } else if (id === 'input-project-topic' || id === 'textarea-project-topic') {
          p.topic = $(this).val();
        } else if (id === 'textarea-evidence-notes') {
          p.evidence_notes = $(this).val();
        } else if (id === 'select-project-tone') {
          p.delivery_preset = $(this).val();
        } else if (id === 'select-project-format') {
          if (!Projects.changeFormat($(this).val())) return;
        } else if (id === 'select-project-duration') {
          p.target_seconds = parseInt($(this).val(), 10) || 60;
          Projects.renderProjectList();
        } else {
          const field = $(this).data('field');
          if (field) {
            let val = $(this).val();
            if ($(this).attr('type') === 'number') val = +val;
            else if ($(this).attr('type') === 'checkbox') val = $(this).is(':checked');
            p[field] = val;
          }
        }
        StudioState.markDirty(true);
        Projects.triggerAutosave();
        Projects.updateHeaderBadges();
      });

      // The captions switch appears on two tabs; keep both in step.
      $(document).on('change', '.captions-enabled-toggle', function() {
        $('.captions-enabled-toggle').prop('checked', $(this).is(':checked'));
      });

      $(document).on('studio:dirty-changed studio:busy-ended', function() {
        Projects.updateHeaderBadges();
      });

      // Browsers show their own generic wording; returnValue only requests the prompt.
      $(window).on('beforeunload', function(e) {
        if (!StudioState.isDirty && !(window.StudioCaptions && StudioCaptions.hasUnsaved())) return undefined;
        e.preventDefault();
        if (e.originalEvent) e.originalEvent.returnValue = '';
        return '';
      });
    },

    captionsAllow: function(action) {
      return !window.StudioCaptions || StudioCaptions.confirmLeave(action);
    },

    renderProjectList: function() {
      const $nav = $('#projects-nav, #project-list-nav').empty();
      const curId = StudioState.current ? StudioState.current.id : '';

      StudioState.projects.forEach(function(r) {
        const activeClass = (r.id === curId) ? ' active' : '';
        const title = r.project.title || 'Untitled story';
        const fmt = StudioState.formatOf(r.project);
        const dur = Projects.durationLabel(r.project.target_seconds || 60);

        const $row = $(`
          <div class="projectrow">
            <button type="button" class="project project-item${activeClass}" data-id="${r.id}">
              <b>${$('<div>').text(title).html()}</b>
              <small>${fmt.short} · ${dur} target</small>
            </button>
            <button type="button" class="project-delete" data-id="${r.id}">&#128465;</button>
          </div>
        `);
        $row.find('.project-delete').attr({title: 'Delete this project', 'aria-label': 'Delete ' + title});
        $nav.append($row);
      });
    },

    // Permanent: the server removes the project's revisions, jobs, voice takes and rendered videos.
    // Clips in the media library stay because other projects can use them.
    deleteProject: function(pid) {
      const row = StudioState.projects.find(p => p.id === pid);
      const title = (row && row.project && row.project.title) || 'Untitled story';
      const isCurrent = !!(StudioState.current && StudioState.current.id === pid);
      if (isCurrent && window.StudioAutopilot && StudioAutopilot.isRunning()) {
        StudioState.setNotice('Wait for "Create draft video automatically" to finish before deleting this project.', true);
        return Promise.resolve(false);
      }
      if (!confirm(`Delete "${title}" permanently?\n\nIts scenes, revision history, voice takes, production jobs and `
          + 'rendered videos are removed. This cannot be undone. Clips in the media library are kept.')) {
        return Promise.resolve(false);
      }
      StudioState.setBusy(true);
      return StudioApi.request('/projects/' + pid, 'DELETE')
        .then(function(res) {
          StudioState.setProjects(StudioState.projects.filter(p => p.id !== pid));
          StudioState.setNotice(`Deleted "${res.title}".`);
          if (!isCurrent) {
            Projects.renderProjectList();
            return true;
          }
          clearTimeout(autosaveTimer);
          StudioState.current = null;
          StudioState.markDirty(false);
          StudioState.draftRecoverable = null;
          Projects.renderProjectList();
          const next = StudioState.projects[0];
          if (next) return Projects.loadProject(next.id).then(() => true);
          Projects.populateEditor();
          return true;
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
          return false;
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    },

    toggleDrawer: function(open) {
      $('body').toggleClass('drawer-open', !!open);
      $('#btn-mobile-projects').attr('aria-expanded', open ? 'true' : 'false');
    },

    durationLabel: function(seconds) {
      return seconds >= 120 ? Math.round(seconds / 60) + ' min' : seconds + 's';
    },

    /** Format choice, duration options and every format-dependent label for project `p`. */
    renderFormat: function(p) {
      const fmt = StudioState.formatOf(p), [dw, dh] = fmt.draft;
      $('#select-project-format').val(p.format || 'reel');
      const options = fmt.durations.slice();
      if (!options.some(([v]) => v === p.target_seconds)) options.push([p.target_seconds, Projects.durationLabel(p.target_seconds) + ' (custom)']);
      $('#select-project-duration').html(options.sort((a, b) => a[0] - b[0])
        .map(([v, l]) => `<option value="${v}">${l}</option>`).join('')).val(String(p.target_seconds));
      $('#format-hint').text(p.format === 'youtube'
        ? `1920×1080 landscape, up to ${fmt.maxScenes} scenes; the picture changes about every 6 seconds. Paste a long script below and use "Create draft video automatically".`
        : `1080×1920 vertical, up to ${fmt.maxScenes} scenes; the picture changes about every 4 seconds.`);
      $('#badge-format').text(`${fmt.short} ${fmt.aspect}`);
      $('.format-size').text(`${fmt.width}×${fmt.height}`);
      $('.format-draft-size').text(`${dw}×${dh}`);
      $('.format-name').text(fmt.label);
      $('body').toggleClass('format-youtube', p.format === 'youtube');
    },

    /** Switch the open project between a reel and a YouTube video; false when not allowed. */
    changeFormat: function(format) {
      const p = StudioState.current.project, fmt = StudioState.FORMATS[format];
      if (!fmt) return false;
      if ((p.scenes || []).length > fmt.maxScenes) {
        StudioState.setNotice(`${fmt.label} supports up to ${fmt.maxScenes} scenes; this project has ${p.scenes.length}.`, true);
        $('#select-project-format').val(p.format || 'reel');
        return false;
      }
      p.format = format;
      // Keep the target inside the format's range: a reel lasts up to 3 minutes, a YouTube video is long-form.
      if (p.target_seconds > fmt.maxSeconds || (format === 'youtube' && p.target_seconds < 300)) {
        p.target_seconds = format === 'youtube' ? 600 : 60;
      }
      Projects.renderFormat(p);
      Projects.renderProjectList();
      if (window.Scenes && Scenes.renderSceneVisuals) Scenes.renderSceneVisuals();
      $(document).trigger('studio:format-changed');
      return true;
    },

    switchProject: function(pid) {
      if (StudioState.current && StudioState.current.id === pid) return;
      if (StudioState.isDirty) {
        if (!confirm('Discard unsaved edits and switch project?')) return;
      } else if (!Projects.captionsAllow('Discard them and switch project?')) {
        return;
      }
      Projects.loadProject(pid);
    },

    loadProject: function(pid) {
      StudioState.setBusy(true);
      return StudioApi.request('/projects/' + pid)
        .then(function(row) {
          Projects.sync(row);
          // Check for recoverable draft
          return StudioApi.request('/projects/' + pid + '/draft');
        })
        .then(function(draft) {
          const r = StudioState.current;
          if (draft && draft.updated && (!r.updated || new Date(draft.updated).getTime() > new Date(r.updated).getTime())) {
            StudioState.draftRecoverable = draft;
          } else {
            StudioState.draftRecoverable = null;
          }
          Projects.renderDraftBanner();
          // Load voice takes
          return StudioApi.request('/projects/' + pid + '/takes');
        })
        .then(function(takes) {
          StudioState.takeHistory = takes || [];
          $(document).trigger('studio:takes-updated');
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
          $(document).trigger('studio:project-loaded');
        });
    },

    sync: function(row) {
      clearTimeout(autosaveTimer);
      StudioState.current = row;
      StudioState.markDirty(false);
      StudioState.draftRecoverable = null;
      StudioState.conflicts = [];
      StudioState.candidateTake = null;

      if (row.project && row.project.scenes && row.project.scenes.length) {
        if (!StudioState.voiceLabSceneId || !row.project.scenes.find(s => s.id === StudioState.voiceLabSceneId)) {
          StudioState.voiceLabSceneId = row.project.scenes[0].id;
        }
      }

      // Update project list
      const idx = StudioState.projects.findIndex(p => p.id === row.id);
      if (idx >= 0) {
        StudioState.projects[idx] = row;
      } else {
        StudioState.projects.unshift(row);
      }

      Projects.renderProjectList();
      Projects.renderDraftBanner();
      Projects.populateEditor();
      Projects.checkPreflight();
      $(document).trigger('studio:project-synced');
    },

    createProject: function() {
      if (StudioState.isDirty && !confirm('Discard unsaved edits and create a new project?')) return Promise.resolve(null);
      if (!StudioState.isDirty && !Projects.captionsAllow('Discard them and create a new project?')) return Promise.resolve(null);
      StudioState.setBusy(true);
      const emptyBody = StudioState.createEmptyProject();
      return StudioApi.request('/projects', 'POST', emptyBody)
        .then(function(row) {
          StudioState.setNotice('Project created.');
          Projects.sync(row);
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    },

    saveProject: function() {
      if (!StudioState.current) return Promise.resolve(null);
      StudioState.setBusy(true);
      const pid = StudioState.current.id;
      const payload = {
        revision: StudioState.current.revision,
        project: StudioState.current.project
      };

      return StudioApi.request('/projects/' + pid, 'PUT', payload)
        .then(function(updatedRow) {
          Projects.sync(updatedRow);
          StudioState.setNotice('Project saved.');
          return updatedRow;
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
          throw err;
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    },

    approveRevision: function() {
      if (!StudioState.current) return Promise.resolve(null);
      if (!Projects.captionsAllow('They are not part of this revision. Approve without them?')) return Promise.resolve(null);
      StudioState.setBusy(true);
      const pid = StudioState.current.id;

      const savePromise = StudioState.isDirty
        ? Projects.saveProject()
        : Promise.resolve(StudioState.current);

      return savePromise
        .then(function(row) {
          return StudioApi.request('/projects/' + pid + '/approve', 'POST', { revision: row.revision });
        })
        .then(function(approvedRow) {
          Projects.sync(approvedRow);
          StudioState.setNotice('Revision v' + approvedRow.revision + ' approved. You can prepare narration or render.');
          return approvedRow;
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    },

    triggerAutosave: function() {
      if (!StudioState.current) return;
      clearTimeout(autosaveTimer);
      autosaveTimer = setTimeout(function() {
        if (!StudioState.isDirty || !StudioState.current) return;
        const pid = StudioState.current.id;
        StudioApi.request('/projects/' + pid + '/draft', 'PUT', StudioState.current.project)
          .catch(function() {
            // Background autosave failure is non-blocking
          });
      }, 1500);
    },

    restoreDraft: function() {
      if (!StudioState.draftRecoverable || !StudioState.current) return;
      StudioState.current.project = StudioState.draftRecoverable.body;
      StudioState.markDirty(true);
      StudioState.draftRecoverable = null;
      Projects.renderDraftBanner();
      Projects.populateEditor();
      StudioState.setNotice('Autosaved draft restored. Review and save project.');
    },

    discardDraft: function() {
      if (!StudioState.current) return;
      const pid = StudioState.current.id;
      StudioState.setBusy(true);
      StudioApi.request('/projects/' + pid + '/draft', 'DELETE')
        .then(function() {
          StudioState.draftRecoverable = null;
          Projects.renderDraftBanner();
          StudioState.setNotice('Draft discarded.');
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    },

    checkPreflight: function() {
      if (!StudioState.current) return;
      const pid = StudioState.current.id;
      StudioApi.request('/projects/' + pid + '/preflight')
        .then(function(report) {
          if (!StudioState.current || StudioState.current.id !== pid) return;
          StudioState.preflight = report;
          Projects.renderPreflightChecklist();
          Projects.updateHeaderBadges();
        })
        .catch(function() {});
    },

    populateEditor: function() {
      if (!StudioState.current || !StudioState.current.project) {
        $('#view-welcome, #welcome-panel').show();
        $('#view-editor, #project-editor-workspace').hide();
        return;
      }

      $('#view-welcome, #welcome-panel').hide();
      $('#view-editor, #project-editor-workspace').show();

      const p = StudioState.current.project;

      // Header title
      $('#header-project-title').text(p.title || 'Untitled Reel');

      // Brief fields
      $('#input-project-title').val(p.title || '');
      $('#input-project-topic, #textarea-project-topic').val(p.topic || '');
      $('#select-project-category').val(p.category || 'Storytelling');
      $('#input-target-seconds').val(p.target_seconds || 60);
      $('#select-duration-mode').val(p.duration_mode || 'natural');
      $('#textarea-draft-script, #input-import-script').val(p.draft_script || '');
      $('#select-project-tone').val(p.delivery_preset || 'conversational');
      Projects.renderFormat(p);
      $('#textarea-evidence-notes').val(p.evidence_notes || '');

      // Export settings
      $('.captions-enabled-toggle').prop('checked', p.captions_enabled !== false);
      $('#range-caption-size').val(p.caption_size || 56);
      $('#caption-size-val').text(p.caption_size || 56);
      $('#range-caption-bottom').val(p.caption_bottom || 300);
      $('#caption-bottom-val').text(p.caption_bottom || 300);
      $('#export-target-seconds').text(Projects.durationLabel(p.target_seconds || 60));
      $('#export-duration-mode-desc').text(p.duration_mode === 'natural' ? 'Target guide · natural pace' : 'Target fit · bounded speed');

      Projects.updateHeaderBadges();
      Projects.renderResearchView();
      $(document).trigger('studio:editor-populated');
    },

    updateHeaderBadges: function() {
      if (!StudioState.current) return;
      const row = StudioState.current;
      const p = row.project;

      const scriptApproved = row.approved && !StudioState.isDirty;
      const visualsReviewed = p.scenes && p.scenes.every(s => s.rights_reviewed);
      const renderReady = !StudioState.isDirty && StudioState.preflight && StudioState.preflight.ready;

      $('#badge-revision').text('v' + row.revision);
      $('#header-project-title').text(p.title || 'Untitled Reel');

      // Script / Approval badge
      const $b1 = $('#badge-script-status, #badge-approval');
      if (StudioState.isDirty) {
        $b1.attr('class', 'badge').text('Unsaved edits');
      } else if (scriptApproved) {
        $b1.attr('class', 'badge approved').text('Approved · v' + row.revision);
      } else {
        $b1.attr('class', 'badge amber').text('Draft · v' + row.revision);
      }

      // Visuals badge
      const $b2 = $('#badge-visuals-status');
      if (visualsReviewed) {
        $b2.attr('class', 'badge approved').text('Visuals Reviewed');
      } else {
        $b2.attr('class', 'badge amber').text('Visuals Review Pending');
      }

      // Preflight badge
      const $b3 = $('#badge-preflight-status, #badge-preflight');
      if (renderReady) {
        $b3.attr('class', 'badge approved').text('Render Ready');
      } else {
        const errCount = (StudioState.preflight && StudioState.preflight.errors) ? StudioState.preflight.errors.length : 0;
        $b3.attr('class', 'badge red').text(errCount > 0 ? errCount + ' blocking issue(s)' : 'Preflight Issues');
      }

      // Save button disabled state
      $('#btn-save-project').prop('disabled', StudioState.isBusy || !StudioState.isDirty);
    },

    renderDraftBanner: function() {
      $('#alert-draft-recovery').toggle(!!StudioState.draftRecoverable);
      const $box = $('#draft-banner').empty();
      if (!StudioState.draftRecoverable) return;

      const dateStr = new Date(StudioState.draftRecoverable.updated).toLocaleString();
      const html = `
        <div class="notice" role="alert" style="background:#fef3c7;border-color:#fde68a;color:#92400e;">
          <div>
            <b>Unsaved draft found</b> from ${dateStr}. Would you like to recover it?
          </div>
          <div class="actions" style="margin:0;">
            <button type="button" class="primary" id="btn-restore-draft">Restore draft</button>
            <button type="button" id="btn-discard-draft">Discard</button>
          </div>
        </div>
      `;
      $box.html(html);
    },

    renderConflictsBanner: function() {
      const $box = $('#conflicts-banner').empty();
      if (!StudioState.conflicts || !StudioState.conflicts.length) return;

      let listHtml = '';
      StudioState.conflicts.forEach(function(c) {
        listHtml += `<li>${c.scene_id}: ${c.reason}</li>`;
      });

      const html = `
        <div class="notice" role="alert" style="background:#fef3c7;border-color:#fde68a;color:#92400e;">
          <div>
            <b>Conflict-aware merge preserved your manual edits:</b>
            <ul>${listHtml}</ul>
          </div>
          <button type="button" id="btn-dismiss-conflicts">×</button>
        </div>
      `;
      $box.html(html);
    },

    renderResearchView: function() {
      if (!StudioState.current) return;
      const p = StudioState.current.project;
      const $container = $('#research-view-container');

      if (!StudioState.researchOpen) {
        $container.hide();
        $('#btn-toggle-research').text('Show research');
        return;
      }

      $container.show();
      $('#btn-toggle-research').text('Hide research');

      $('#research-report-pre').text(p.research_report || 'No research report applied yet. Completed jobs appear below.');

      const $srcList = $('#research-sources-list').empty();
      if (p.research_sources && p.research_sources.length) {
        p.research_sources.forEach(function(s, idx) {
          let url;
          try { url = new URL(s.url); } catch (e) { return; }
          if (!['http:', 'https:'].includes(url.protocol)) return;
          const $item = $('<div>').append($('<a>').attr({href: url.href, target: '_blank', rel: 'noreferrer'}).text(`${idx + 1}. ${s.title}`));
          $srcList.append($item);
        });
      }

      const $iframeBox = $('#research-grounding-iframe').empty();
      if (p.research_grounding && p.research_grounding.searchEntryPoint && p.research_grounding.searchEntryPoint.renderedContent) {
        const $ifr = $('<iframe title="Google Search suggestions" sandbox="allow-popups allow-popups-to-escape-sandbox"></iframe>');
        $ifr.attr('srcdoc', p.research_grounding.searchEntryPoint.renderedContent);
        $iframeBox.append($ifr);
      }
    },

    renderPreflightChecklist: function() {
      const pf = StudioState.preflight;
      const $box = $('#preflight-checklist-items').empty();
      const $badge = $('#preflight-summary-badge');

      if (!pf) return;
      if (StudioState.isDirty) {
        $badge.attr('class', 'badge amber').text('Save to check');
        $box.text('Save your changes to check the current storyboard.');
        return;
      }

      if (pf.ready) {
        $badge.attr('class', 'badge approved').text('All checks passed');
        $box.html(`
          <div class="checkitem ok">
            <span>✓</span> Storyboard, footage, narration, and rights reviews are verified. Ready for rendering.
          </div>
        `);
      } else {
        const errCount = (pf.errors && pf.errors.length) || 0;
        $badge.attr('class', 'badge red').text(errCount + ' blocking issue(s)');
        let html = '';
        if (pf.errors) {
          pf.errors.forEach(function(err) {
            html += `<div class="checkitem err"><span>✕</span> ${$('<div>').text(err.message).html()}</div>`;
          });
        }
        if (pf.warnings) {
          pf.warnings.forEach(function(warn) {
            html += `<div class="checkitem warn"><span>⚠</span> ${$('<div>').text(warn.message).html()}</div>`;
          });
        }
        $box.html(html);
      }
    },

    openHistoryModal: function() {
      if (!StudioState.current) return;
      StudioState.setBusy(true);
      const pid = StudioState.current.id;

      StudioApi.request('/projects/' + pid + '/history')
        .then(function(history) {
          const $list = $('#history-revisions-list').empty();
          if (!history || !history.length) {
            $list.html('<p class="muted">No historical revisions recorded.</p>');
          } else {
            history.forEach(function(h) {
              const dateStr = new Date(h.created).toLocaleString();
              const $item = $(`
                <div class="job">
                  <div>
                    <b>Revision ${h.revision}</b>
                    <p>${dateStr}</p>
                  </div>
                  <button type="button" class="btn-restore-history-item" data-rev="${h.revision}">Load draft</button>
                </div>
              `);
              $item.find('.btn-restore-history-item').data('body', h.project);
              $list.append($item);
            });
          }
          $('#history-modal').show();
        })
        .catch(function(err) {
          StudioState.setNotice(err.message, true);
        })
        .finally(function() {
          StudioState.setBusy(false);
        });
    }
  };

  $(document).on('click', '.btn-restore-history-item', function() {
    const body = $(this).data('body');
    if (!body || !StudioState.current) return;
    StudioState.current.project = body;
    StudioState.markDirty(true);
    $('#history-modal').hide();
    Projects.populateEditor();
    StudioState.setNotice('Revision loaded as unsaved draft. Save to create a new revision.');
  });

  $(document).on('click', '#btn-close-history-modal', function() {
    $('#history-modal').hide();
  });

  Projects.save = Projects.saveProject;
  Projects.updatePreflight = Projects.checkPreflight;
  Projects.setConflicts = function(c) {
    StudioState.conflicts = c || [];
  };

  window.Projects = Projects;
  window.StudioProjects = Projects;
})(jQuery);
