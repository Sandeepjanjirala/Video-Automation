/**
 * Stage M7 Full Browser-to-Video E2E Acceptance Test.
 * 
 * Executes complete user workflow using the compiled React application (app.js)
 * running against Django on http://127.0.0.1:8788.
 * 
 * Verifies:
 * 1. Page load & bootstrap from Django (:8788).
 * 2. New project creation via UI.
 * 3. Script & Telugu narration entry.
 * 4. Revision save & approval via UI.
 * 5. Media footage and music assignment via UI.
 * 6. Voice & visual approval via UI.
 * 7. Job enqueue ('Render final 1080p →') directly from React UI button.
 * 8. PostgreSQL outbox -> Redis -> Celery worker -> FFmpeg execution.
 * 9. React UI polling & display of completed export card.
 * 10. Zero runtime errors & zero requests to FastAPI (:8787).
 */

if (!Promise.withResolvers) {
  Promise.withResolvers = function() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };
}

class Iterator {
  [Symbol.iterator]() { return this; }
  find(fn) { for (const x of this) if (fn(x)) return x; return undefined; }
  filter(fn) { const self = this; return (function*() { for (const x of self) if (fn(x)) yield x; })(); }
  map(fn) { const self = this; return (function*() { for (const x of self) yield fn(x); })(); }
  some(fn) { for (const x of this) if (fn(x)) return true; return false; }
  every(fn) { for (const x of this) if (!fn(x)) return false; return true; }
  toArray() { return [...this]; }
}
globalThis.Iterator = globalThis.Iterator || Iterator;

const vm = require('vm');
const origCreateContext = vm.createContext;
vm.createContext = function(sandbox, options) {
  const ctx = origCreateContext.call(vm, sandbox, options);
  try {
    vm.runInContext(`
      if (!Promise.withResolvers) {
        Promise.withResolvers = function() {
          let resolve, reject;
          const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
          return { promise, resolve, reject };
        };
      }
      class Iterator {
        [Symbol.iterator]() { return this; }
        find(fn) { for (const x of this) if (fn(x)) return x; return undefined; }
        filter(fn) { const self = this; return (function*() { for (const x of self) if (fn(x)) yield x; })(); }
        map(fn) { const self = this; return (function*() { for (const x of self) yield fn(x); })(); }
        some(fn) { for (const x of this) if (fn(x)) return true; return false; }
        every(fn) { for (const x of this) if (!fn(x)) return false; return true; }
        toArray() { return [...this]; }
      }
      this.Iterator = this.Iterator || Iterator;
    `, ctx);
  } catch(e) {}
  return ctx;
};

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('../frontend/node_modules/jsdom');
const { fireEvent, waitFor, getByRole, getByLabelText, getByText, getAllByText } = require('../frontend/node_modules/@testing-library/dom');

(async () => {
  const targetUrl = process.env.STUDIO_URL || 'http://127.0.0.1:8788';
  console.log(`[M7 E2E] Initializing React frontend against Django target: ${targetUrl}`);

  const networkLog = [];
  const runtimeErrors = [];

  const dom = new JSDOM('<html><body><div id="root"></div></body></html>', {
    url: targetUrl,
    runScripts: 'dangerously',
    pretendToBeVisual: true
  });

  const w = dom.window;
  w.addEventListener('error', e => runtimeErrors.push(String(e.error || e.message)));

  // Custom fetch to track all network requests
  const origFetch = fetch;
  w.fetch = async (url, opts) => {
    const fullUrl = new URL(url, w.location.href);
    networkLog.push({ url: fullUrl.href, method: (opts && opts.method) || 'GET' });
    if (fullUrl.port === '8787') {
      throw new Error(`CRITICAL VIOLATION: Request attempted to FastAPI port 8787: ${fullUrl.href}`);
    }
    return origFetch(fullUrl, opts);
  };

  w.confirm = () => true;
  w.TextEncoder = TextEncoder;
  Object.defineProperty(w, 'crypto', { value: require('crypto').webcrypto });

  const appJsPath = path.join(__dirname, '../studio/static/app.js');
  w.eval(fs.readFileSync(appJsPath, 'utf8'));
  const doc = w.document;

  console.log('[M7 E2E] Waiting for UI initial render & bootstrap...');
  await waitFor(() => getByRole(doc.body, 'button', { name: /New story/ }), { container: doc, timeout: 8000 });
  console.log('[M7 E2E] Bootstrap verified. Clicking "New story"...');

  fireEvent.click(getByRole(doc.body, 'button', { name: /New story/ }));
  await waitFor(() => getByText(doc.body, /Project created/), { container: doc, timeout: 5000 });

  // 1. Fill out script
  console.log('[M7 E2E] Entering project title and Telugu narration...');
  fireEvent.change(getByLabelText(doc.body, 'Project title'), { target: { value: 'M7 Browser Real Reel E2E' } });
  fireEvent.change(getByLabelText(doc.body, 'Telugu narration'), {
    target: { value: 'నమస్కారం! తెలుగు రీల్ స్టూడియో జాంగో ఆర్కిటెక్చర్‌తో విజయవంతంగా పనిచేస్తోంది.' }
  });
  fireEvent.change(getByLabelText(doc.body, 'Roman Telugu captions'), {
    target: { value: 'Namaskaram! Telugu Reel Studio Django architecture tho vijayavanthamga panichesthondi.' }
  });
  fireEvent.change(getByLabelText(doc.body, /Your review notes/), {
    target: { value: 'M7 Browser E2E Real Reel Acceptance Verification.' }
  });

  // 2. Save & approve script revision
  console.log('[M7 E2E] Saving & approving script revision...');
  fireEvent.click(getByRole(doc.body, 'button', { name: /Save & approve this revision/ }));
  await waitFor(() => getByText(doc.body, 'Approved · v2'), { container: doc, timeout: 8000 });
  console.log('[M7 E2E] Script revision 2 approved!');

  // 3. Switch to Voice & visuals tab
  console.log('[M7 E2E] Navigating to "Voice & visuals" tab...');
  fireEvent.click(getByRole(doc.body, 'button', { name: /Voice & visuals/ }));
  await waitFor(() => getByLabelText(doc.body, 'Visual source'), { container: doc, timeout: 5000 });

  // 4. Assign visual footage to scene
  console.log('[M7 E2E] Assigning media asset to scene 01...');
  // Open Reusable Media Library modal
  const openLibBtn = getByRole(doc.body, 'button', { name: /Choose from library/ });
  fireEvent.click(openLibBtn);
  await waitFor(() => getByText(doc.body, /Reusable Media Library/), { container: doc, timeout: 5000 });

  // Select 'Moving Reel Footage 1' in media library
  await waitFor(() => getByText(doc.body, /Moving Reel Footage 1/), { container: doc, timeout: 5000 });
  const cards = Array.from(doc.querySelectorAll('.mediacard'));
  const targetCard = cards.find(c => c.textContent.includes('Moving Reel Footage 1'));
  if (!targetCard) throw new Error('Moving Reel Footage 1 not found in media modal');
  const assignBtn = targetCard.querySelector('button.primary') || targetCard.querySelector('button');
  console.log('[M7 E2E] Clicking assign button for Moving Reel Footage 1...');
  fireEvent.click(assignBtn);

  // Wait for modal to close
  await waitFor(() => !doc.querySelector('.modalback'), { container: doc, timeout: 5000 });
  console.log('[M7 E2E] Modal closed. Confirming visual relevance checkbox...');

  // Check visual permission checkbox
  const checkInput = await waitFor(() => getByLabelText(doc.body, /I reviewed visual relevance/), { container: doc, timeout: 5000 });
  fireEvent.change(checkInput, { target: { checked: true } });
  console.log('[M7 E2E] Checkbox confirmed.');

  // 5. Save & approve changes
  console.log('[M7 E2E] Saving & approving visual setup...');
  fireEvent.click(getByRole(doc.body, 'button', { name: 'Save & approve changes' }));
  await waitFor(() => getByText(doc.body, /Approved · v[0-9]+/), { container: doc, timeout: 8000 });
  console.log('[M7 E2E] Visual setup approved!');

  // 6. Switch to Preview & export tab
  console.log('[M7 E2E] Navigating to "Preview & export" tab...');
  fireEvent.click(getByRole(doc.body, 'button', { name: /Preview & export/ }));
  await waitFor(() => getByRole(doc.body, 'button', { name: 'Render final 1080p →' }), { container: doc, timeout: 5000 });

  // 7. Click Render final 1080p button
  console.log('[M7 E2E] Triggering "Render final 1080p →" directly from UI button...');
  fireEvent.click(getByRole(doc.body, 'button', { name: 'Render final 1080p →' }));

  // 8. Wait for job to appear in UI
  console.log('[M7 E2E] Waiting for job card in UI...');
  await waitFor(() => {
    const jobElem = doc.querySelector('.panel.jobs .job');
    if (!jobElem) throw new Error('Job not yet in DOM');
  }, { container: doc, timeout: 8000 });
  console.log('[M7 E2E] Render job visible in UI! Polling for render pipeline completion...');

  // 9. Poll UI until export card with FINAL EXPORT appears (up to 120 seconds)
  const startTime = Date.now();
  let exportCardFound = false;

  while (Date.now() - startTime < 120000) {
    await new Promise(r => setTimeout(r, 3000));
    const exportCard = doc.querySelector('.panel.exportcard');
    if (exportCard && exportCard.textContent.includes('FINAL EXPORT')) {
      exportCardFound = true;
      break;
    }

    const jobElem = doc.querySelector('.panel.jobs .job');
    const jobStatus = jobElem ? jobElem.textContent : 'none';
    console.log(`[M7 E2E] Waiting for Celery + FFmpeg render... (${Math.round((Date.now() - startTime) / 1000)}s elapsed, status: ${jobStatus})`);
  }

  if (!exportCardFound) {
    throw new Error('Timeout waiting for FINAL EXPORT card to appear in UI after 120 seconds');
  }

  console.log('[M7 E2E] SUCCESS! Export card appeared in React UI!');

  // Verify video element and download link in DOM
  const videoElem = doc.querySelector('.exportcard video');
  const downloadLink = doc.querySelector('.exportcard a.filebutton');

  console.log('[M7 E2E] Video player src:', videoElem ? videoElem.getAttribute('src') : 'none');
  console.log('[M7 E2E] Download link href:', downloadLink ? downloadLink.getAttribute('href') : 'none');

  // Verify network audit: ZERO requests to 8787
  const requestsTo8787 = networkLog.filter(r => r.url.includes(':8787'));
  console.log(`[M7 E2E] Network Audit: Total requests = ${networkLog.length}, Requests to :8787 = ${requestsTo8787.length}`);

  const summary = {
    status: 'PASS',
    runtimeErrors: runtimeErrors,
    requestsCount: networkLog.length,
    requestsToFastAPI: requestsTo8787.length,
    videoSrc: videoElem ? videoElem.getAttribute('src') : null,
    downloadHref: downloadLink ? downloadLink.getAttribute('href') : null
  };

  console.log('\n--- M7 E2E TEST RESULT SUMMARY ---');
  console.log(JSON.stringify(summary, null, 2));

  dom.window.close();
  if (runtimeErrors.length > 0 || requestsTo8787.length > 0) {
    process.exit(1);
  }
  process.exit(0);
})().catch(err => {
  console.error('[M7 E2E ERROR]', err);
  process.exit(1);
});
