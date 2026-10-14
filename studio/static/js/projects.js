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
      });

      $(document).on('click', '#btn-new-story, #btn-new-project, #btn-welcome-create', function() {
        Projects.createProject();
      });

      $(document).on('click', '#btn-save-project', function() {
        Projects.saveProject();
      });

      $(document).on('click', '#btn-approve-project, #btn-approve-script, #btn-approve-voice, #btn-approve-export', function() {
        Projects.approveRevision();
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
      $(document).on('input change', '#input-project-title, #select-project-category, #input-project-topic, #textarea-project-topic, #textarea-evidence-notes, #select-project-tone, #select-project-duration, #input-target-seconds, #select-duration-mode, .project-field-bind', function() {
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
        } else if (id === 'select-project-duration') {
          p.target_seconds = parseInt($(this).val(), 10) || 60;
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

      $(document).on('studio:dirty-changed studio:busy-ended', function() {
        Projects.updateHeaderBadges();
      });
    },

    renderProjectList: function() {
      const $nav = $('#projects-nav, #project-list-nav').empty();
      const curId = StudioState.current ? StudioState.current.id : '';

      StudioState.projects.forEach(function(r) {
        const activeClass = (r.id === curId) ? ' active' : '';
        const title = r.project.title || 'Untitled story';
        const cat = r.project.category || 'Storytelling';
        const dur = r.project.target_seconds || 60;

        const $btn = $(`
          <button type="button" class="project project-item${activeClass}" data-id="${r.id}">
            <b>${$('<div>').text(title).html()}</b>
            <small>${cat} · ${dur}s target</small>
          </button>
        `);
        $nav.append($btn);
      });
    },

    switchProject: function(pid) {
      if (StudioState.current && StudioState.current.id === pid) return;
      if (StudioState.isDirty) {
        if (!confirm('Discard unsaved edits and switch project?')) return;
      }
      Projects.loadProject(pid);
    },

    loadProject: function(pid) {
      StudioState.setBusy(true);
      StudioApi.request('/projects/' + pid)
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
      StudioState.current = row;
      StudioState.isDirty = false;
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
      Projects.populateEditor();
      Projects.checkPreflight();
      $(document).trigger('studio:project-synced');
    },

    createProject: function() {
      StudioState.setBusy(true);
      const emptyBody = StudioState.createEmptyProject();
      StudioApi.request('/projects', 'POST', emptyBody)
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
      $('#textarea-draft-script').val(p.draft_script || '');
      $('#textarea-evidence-notes').val(p.evidence_notes || '');

      // Export settings
      $('#range-caption-size').val(p.caption_size || 56);
      $('#caption-size-val').text(p.caption_size || 56);
      $('#range-caption-bottom').val(p.caption_bottom || 300);
      $('#caption-bottom-val').text(p.caption_bottom || 300);
      $('#export-target-seconds').text((p.target_seconds || 60) + 's');
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
      const renderReady = StudioState.preflight && StudioState.preflight.ready;

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
          const $item = $(`<div><a href="${s.url}" target="_blank" rel="noreferrer">${idx + 1}. ${$('<div>').text(s.title).html()}</a></div>`);
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
          const $list = $('#history-modal-list').empty();
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
          $('#modal-history').show();
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
    $('#modal-history').hide();
    Projects.populateEditor();
    StudioState.setNotice('Revision loaded as unsaved draft. Save to create a new revision.');
  });

  $(document).on('click', '#btn-close-history-modal', function() {
    $('#modal-history').hide();
  });

  Projects.save = Projects.saveProject;
  Projects.updatePreflight = Projects.checkPreflight;
  Projects.setConflicts = function(c) {
    StudioState.conflicts = c || [];
  };

  window.Projects = Projects;
  window.StudioProjects = Projects;
})(jQuery);
