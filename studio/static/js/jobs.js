// jobs.js — Production jobs, controlled polling, retry, cancel, result application, exports

const StudioJobs = (() => {
  let pollInterval = null;
  let activeProjectId = null;

  function init() {
    // Retry buttons
    $(document).on('click', '.btn-retry-job', function() {
      const jid = $(this).data('jid');
      const mode = $(this).data('mode') || 'original';
      retryJob(jid, mode);
    });

    // Cancel buttons
    $(document).on('click', '.btn-cancel-job', function() {
      const jid = $(this).data('jid');
      cancelJob(jid);
    });

    // Apply buttons
    $(document).on('click', '.btn-apply-job', function() {
      const jid = $(this).data('jid');
      const force = $(this).data('force') === true || $(this).data('force') === 'true';
      applyJob(jid, force);
    });

    // Queue job trigger buttons
    $(document).on('click', '[data-action="queue-job"]', function() {
      const kind = $(this).data('kind');
      const extraJson = $(this).attr('data-extra');
      let extra = {};
      if (extraJson) {
        try { extra = JSON.parse(extraJson); } catch(e) {}
      }
      queue(kind, extra);
    });
  }

  async function queue(kind, extra = {}) {
    const row = StudioState.getRow();
    const p = StudioState.getProject();
    if (!row) {
      StudioState.tell('Please select or create a project first.', true);
      return;
    }

    try {
      StudioState.setBusy(true);
      // If dirty, save first
      let currentRev = row.revision;
      if (StudioState.isDirty) {
        const saved = await StudioProjects.save();
        if (saved) currentRev = saved.revision;
      }

      const payload = { revision: currentRev, kind, ...extra };
      const job = await StudioApi.req(`/projects/${row.id}/jobs`, payload, 'POST');
      
      const currentJobs = StudioState.getJobs() || [];
      StudioState.setJobs([job, ...currentJobs]);
      StudioState.tell(`Queued ${kind} job.`);
      
      // Ensure polling is active
      startPolling(row.id);
    } catch (e) {
      console.error('[queue error]', e);
      StudioState.tell(e.message, true);
    } finally {
      StudioState.setBusy(false);
    }
  }

  async function retryJob(jid, mode = 'original') {
    try {
      StudioState.setBusy(true);
      await StudioApi.req(`/jobs/${jid}/retry?mode=${encodeURIComponent(mode)}`, {}, 'POST');
      StudioState.tell(`Retry queued with ${mode} inputs.`);
      const row = StudioState.getRow();
      if (row) pollJobsOnce(row.id);
    } catch (e) {
      StudioState.tell(e.message, true);
    } finally {
      StudioState.setBusy(false);
    }
  }

  async function cancelJob(jid) {
    try {
      StudioState.setBusy(true);
      await StudioApi.req(`/jobs/${jid}/cancel`, {}, 'POST');
      StudioState.tell('Job cancellation requested.');
      const row = StudioState.getRow();
      if (row) pollJobsOnce(row.id);
    } catch (e) {
      StudioState.tell(e.message, true);
    } finally {
      StudioState.setBusy(false);
    }
  }

  async function applyJob(jid, force = false) {
    const row = StudioState.getRow();
    if (!row) return;

    try {
      StudioState.setBusy(true);
      const url = `/projects/${row.id}/apply-job/${jid}${force ? '?force=true' : ''}`;
      const res = await StudioApi.req(url, {}, 'POST');

      StudioProjects.sync(res.project);

      if (res.conflicts && res.conflicts.length > 0) {
        StudioProjects.setConflicts(res.conflicts);
        StudioState.tell(`Result applied with ${res.conflicts.length} manual edit(s) preserved.`, true);
      } else {
        StudioProjects.setConflicts([]);
        StudioState.tell(`Result applied successfully to project (v${res.project.revision}).`);
      }

      // Refresh jobs list
      const updatedJobs = await StudioApi.req(`/projects/${row.id}/jobs`);
      StudioState.setJobs(updatedJobs);
    } catch (e) {
      StudioState.tell(e.message, true);
    } finally {
      StudioState.setBusy(false);
    }
  }

  function startPolling(projectId) {
    if (activeProjectId === projectId && pollInterval) {
      return;
    }
    stopPolling();
    activeProjectId = projectId;

    // Immediate first poll
    pollJobsOnce(projectId);

    // Controlled interval
    pollInterval = setInterval(() => {
      pollJobsOnce(projectId);
    }, 2000);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    activeProjectId = null;
  }

  async function pollJobsOnce(projectId) {
    if (!projectId) return;
    try {
      const jobs = await StudioApi.req(`/projects/${projectId}/jobs`);
      StudioState.setJobs(jobs);

      // Check if any jobs are currently queued or running
      const pending = jobs.filter(j => j.state === 'queued' || j.state === 'running');
      if (pending.length === 0 && pollInterval) {
        // Slow down or clear high-frequency poll if everything is settled
        // We will keep a quiet 8s poll or stop until next action
        clearInterval(pollInterval);
        pollInterval = setInterval(() => {
          pollQuietly(projectId);
        }, 8000);
      }
    } catch (e) {
      // Ignore network errors during poll
    }
  }

  async function pollQuietly(projectId) {
    if (!projectId || projectId !== activeProjectId) return;
    try {
      const jobs = await StudioApi.req(`/projects/${projectId}/jobs`);
      StudioState.setJobs(jobs);
      const pending = jobs.filter(j => j.state === 'queued' || j.state === 'running');
      if (pending.length > 0) {
        // Resume fast polling
        startPolling(projectId);
      }
    } catch (e) {}
  }

  function renderJobs(jobs) {
    const $container = $('#jobs-list');
    const $badge = $('#jobs-pending-badge');
    if (!$container.length) return;

    const list = jobs || [];
    const pending = list.filter(j => j.state === 'queued' || j.state === 'running').length;
    
    if ($badge.length) {
      $badge.text(pending ? `${pending} in progress` : 'Queue clear');
    }

    if (list.length === 0) {
      $container.html('<p class="muted">Research, voice preparation, downloads, and renders appear here.</p>');
      renderExportCards([]);
      return;
    }

    let html = '';
    list.forEach(j => {
      const kindTitle = j.kind === 'stock' ? 'Footage download' : (j.kind.charAt(0).toUpperCase() + j.kind.slice(1));
      const isFailed = j.state === 'failed';
      const isSucceeded = j.state === 'succeeded';
      const isRunning = j.state === 'running' || j.state === 'queued';
      const canApply = isSucceeded && ['research', 'draft', 'stock', 'stock_all'].includes(j.kind);

      html += `<div class="job ${escapeHtml(j.state)}" data-jid="${escapeHtml(j.id)}">`;
      html += `  <div class="job-meta">`;
      html += `    <b>${escapeHtml(kindTitle)} <span>· v${escapeHtml(j.revision || 1)}</span></b>`;
      html += `    <p class="job-progress">${escapeHtml(j.progress || j.state)}</p>`;
      if (j.error) {
        html += `    <p class="job-error" style="color:var(--danger);font-size:0.85rem;">${escapeHtml(j.error)}</p>`;
      }
      html += `  </div>`;
      html += `  <div class="job-status">`;
      html += `    <span class="badge ${j.applied ? 'approved' : ''}">${escapeHtml(j.applied ? 'Applied' : j.state)}</span>`;
      html += `  </div>`;

      // Actions
      html += `  <div class="jobactions" style="margin-top:0.5rem;display:flex;gap:0.5rem;flex-wrap:wrap;">`;
      if (isRunning) {
        html += `    <button type="button" class="btn-cancel-job ghost" data-jid="${escapeHtml(j.id)}" style="font-size:0.8rem;padding:0.25rem 0.5rem;">Cancel</button>`;
      }
      if (isFailed) {
        html += `    <button type="button" class="btn-retry-job" data-jid="${escapeHtml(j.id)}" data-mode="original" style="font-size:0.8rem;padding:0.25rem 0.5rem;">Retry original</button>`;
        html += `    <button type="button" class="btn-retry-job" data-jid="${escapeHtml(j.id)}" data-mode="current" style="font-size:0.8rem;padding:0.25rem 0.5rem;">Re-run with current</button>`;
      }
      if (canApply) {
        html += `    <button type="button" class="btn-apply-job ${j.applied ? 'ghost' : 'primary'}" data-jid="${escapeHtml(j.id)}" style="font-size:0.8rem;padding:0.25rem 0.5rem;">${j.applied ? 'Re-apply result' : 'Apply result'}</button>`;
      }
      html += `  </div>`;

      // Details for stock_all
      if (j.kind === 'stock_all' && isSucceeded && j.result && j.result.selections) {
        html += `  <details style="margin-top:0.5rem;font-size:0.85rem;">`;
        html += `    <summary>Footage search results</summary>`;
        j.result.selections.forEach(x => {
          html += `    <p>${escapeHtml(x.scene_id)}: ${escapeHtml(x.error || 'Candidate downloaded; review after applying.')}</p>`;
        });
        html += `  </details>`;
      }

      // Details for research
      if (j.kind === 'research' && isSucceeded && j.result) {
        html += `  <details style="margin-top:0.5rem;font-size:0.85rem;">`;
        html += `    <summary>Read report</summary>`;
        if (j.result.text) {
          html += `    <pre class="research" style="white-space:pre-wrap;font-size:0.85rem;max-height:200px;overflow-y:auto;">${escapeHtml(j.result.text)}</pre>`;
        }
        if (j.result.sources && Array.isArray(j.result.sources)) {
          html += `    <div style="margin-top:0.25rem;">`;
          j.result.sources.forEach(s => {
            html += `      <div><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.title || s.url)}</a></div>`;
          });
          html += `    </div>`;
        }
        if (j.result.grounding && j.result.grounding.searchEntryPoint && j.result.grounding.searchEntryPoint.renderedContent) {
          html += `    <iframe title="Search suggestions" sandbox="allow-popups allow-popups-to-escape-sandbox" srcdoc="${escapeHtml(j.result.grounding.searchEntryPoint.renderedContent)}" style="width:100%;border:none;margin-top:0.5rem;min-height:80px;"></iframe>`;
        }
        html += `  </details>`;
      }

      html += `</div>`;
    });

    $container.html(html);

    // Update export cards in Tab 03
    renderExportCards(list);
  }

  function renderExportCards(jobs) {
    const $exportContainer = $('#export-cards-container');
    if (!$exportContainer.length) return;

    const exportJobs = (jobs || []).filter(j => j.kind === 'render' && j.state === 'succeeded');
    if (exportJobs.length === 0) {
      $exportContainer.html('<p class="muted">No exported reels yet. Complete the steps above and click Render.</p>');
      return;
    }

    let html = '';
    exportJobs.forEach(j => {
      const videoSrc = `/api/exports/${encodeURIComponent(j.id)}/reel.mp4`;
      const thumbSrc = `/api/exports/${encodeURIComponent(j.id)}/thumb.jpg`;
      const resLabel = j.params && j.params.resolution ? j.params.resolution : '1080x1920';

      html += `<div class="card export-card" style="margin-bottom:1.5rem;padding:1rem;border-radius:var(--radius);background:var(--surface);border:1px solid var(--border);" data-jid="${escapeHtml(j.id)}">`;
      html += `  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;">`;
      html += `    <div>`;
      html += `      <h4 style="margin:0;">1080×1920 Vertical Reel</h4>`;
      html += `      <span class="muted" style="font-size:0.85rem;">Job: ${escapeHtml(j.id)} · v${escapeHtml(j.revision || 1)} · ${escapeHtml(resLabel)}</span>`;
      html += `    </div>`;
      html += `    <a href="${videoSrc}" download="reel_${escapeHtml(j.id)}.mp4" class="btn primary" style="font-size:0.85rem;padding:0.4rem 0.8rem;text-decoration:none;">Download MP4</a>`;
      html += `  </div>`;
      html += `  <div class="video-preview-wrapper" style="max-width:360px;margin:0 auto;border-radius:var(--radius);overflow:hidden;background:#000;">`;
      html += `    <video controls preload="metadata" poster="${thumbSrc}" style="width:100%;display:block;max-height:540px;" src="${videoSrc}">`;
      html += `      Your browser does not support the video tag.`;
      html += `    </video>`;
      html += `  </div>`;
      html += `</div>`;
    });

    $exportContainer.html(html);
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  return {
    init,
    queue,
    retryJob,
    cancelJob,
    applyJob,
    startPolling,
    stopPolling,
    pollJobsOnce,
    renderJobs,
    renderExportCards
  };
})();

window.StudioJobs = StudioJobs;
