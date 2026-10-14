# Telugu Reel Studio — M8 Frontend Feature Parity Matrix

This document tracks 1:1 feature parity between the existing React/TypeScript frontend (`frontend/app.tsx`) and the replacement Django Templates + jQuery frontend.

## 1. Feature Parity Inventory

| Feature / UI Component | React Source Location | Replacement Django Template / Partial | Replacement jQuery Handler Module | Backend API Endpoint | Parity Status |
|---|---|---|---|---|---|
| **Bootstrap & Session Token** | `app.tsx:125-132` | `templates/studio/base.html` | `static/js/api.js` | `GET /api/bootstrap` | **VERIFIED** |
| **Health Check** | `app.tsx:1335` | `partials/settings_modal.html` | `static/js/settings.js` | `GET /api/health` | **VERIFIED** |
| **Project List Navigation** | `app.tsx:380-389` | `partials/project_sidebar.html` | `static/js/projects.js` | `GET /api/projects` | **VERIFIED** |
| **Create New Project** | `app.tsx:378` | `partials/project_sidebar.html` | `static/js/projects.js` | `POST /api/projects` | **VERIFIED** |
| **Project Selection & Dirty Guard** | `app.tsx:382-384` | `partials/project_sidebar.html` | `static/js/projects.js` | `GET /api/projects/{pid}` | **VERIFIED** |
| **Top Status Badges & Save** | `app.tsx:403-421` | `partials/project_header.html` | `static/js/projects.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Autosave Draft (Debounced)** | `app.tsx:69, 529-532` | `partials/project_editor.html` | `static/js/projects.js` | `PUT /api/projects/{pid}/draft` | **VERIFIED** |
| **Draft Recovery Banner** | `app.tsx:423-442` | `partials/alerts.html` | `static/js/projects.js` | `GET /api/projects/{pid}/draft` | **VERIFIED** |
| **Draft Discard** | `app.tsx:435-439` | `partials/alerts.html` | `static/js/projects.js` | `DELETE /api/projects/{pid}/draft` | **VERIFIED** |
| **Conflict Resolution Notice** | `app.tsx:444-452` | `partials/alerts.html` | `static/js/projects.js` | UI State / Merged Body | **VERIFIED** |
| **Notification Banners** | `app.tsx:454-459` | `partials/alerts.html` | `static/js/app.js` | UI State | **VERIFIED** |
| **Welcome / Empty Slate** | `app.tsx:461-469` | `partials/welcome.html` | `static/js/projects.js` | `POST /api/projects` | **VERIFIED** |
| **Workflow Tab Switching** | `app.tsx:471-481` | `index.html` | `static/js/app.js` | UI State | **VERIFIED** |
| **Brief & Metadata Inputs** | `app.tsx:485-524` | `partials/tab_story.html` | `static/js/projects.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **AI Research & Draft Enqueue** | `app.tsx:520-521` | `partials/tab_story.html` | `static/js/jobs.js` | `POST /api/projects/{pid}/jobs` | **VERIFIED** |
| **Script Importer & Segmenter** | `app.tsx:526-540` | `partials/tab_story.html` | `static/js/scenes.js` | `POST /api/projects/{pid}/jobs` | **VERIFIED** |
| **Script Editor (Telugu / Roman)** | `app.tsx:542-573` | `partials/tab_story.html` | `static/js/scenes.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Scene Reordering & Removal** | `app.tsx:561-567` | `partials/tab_story.html` | `static/js/scenes.js` | UI State | **VERIFIED** |
| **Add Scene Manually** | `app.tsx:535-538, 572` | `partials/tab_story.html` | `static/js/scenes.js` | UI State | **VERIFIED** |
| **Evidence & Research Review** | `app.tsx:575-594` | `partials/tab_story.html` | `static/js/projects.js` | UI State | **VERIFIED** |
| **Save & Approve Revision** | `app.tsx:592` | `partials/tab_story.html` | `static/js/projects.js` | `POST /api/projects/{pid}/approve`| **VERIFIED** |
| **Revision History Modal** | `app.tsx:1342-1365` | `partials/history_modal.html` | `static/js/projects.js` | `GET /api/projects/{pid}/history`| **VERIFIED** |
| **Voice Profile & TTS Provider** | `app.tsx:611-652` | `partials/tab_voice.html` | `static/js/voice.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Provider Capabilities Strip** | `app.tsx:654-676` | `partials/tab_voice.html` | `static/js/voice.js` | `GET /api/voice/capabilities` | **VERIFIED** |
| **Voice Lock Toggle** | `app.tsx:678-682` | `partials/tab_voice.html` | `static/js/voice.js` | `POST /api/projects/{pid}/voice_lock` | **VERIFIED** |
| **Delivery Preset & Speech Rate**| `app.tsx:684-705` | `partials/tab_voice.html` | `static/js/voice.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Prepare Voice Previews Enqueue**| `app.tsx:710` | `partials/tab_voice.html` | `static/js/jobs.js` | `POST /api/projects/{pid}/jobs` | **VERIFIED** |
| **Pronunciation Dict Editor** | `app.tsx:714-733` | `partials/tab_voice.html` | `static/js/voice.js` | `POST /api/projects/{pid}/pronunciation_dict` | **VERIFIED** |
| **Pronunciation Normalizer Test**| `app.tsx:734-746` | `partials/tab_voice.html` | `static/js/voice.js` | `POST /api/voice/normalize` | **VERIFIED** |
| **Colloquial Rewrite Polish** | `app.tsx:749-779` | `partials/tab_voice.html` | `static/js/voice.js` | `POST /api/voice/rewrite_preview` | **VERIFIED** |
| **Voice Lab A/B Test Candidate** | `app.tsx:782-861` | `partials/tab_voice.html` | `static/js/voice.js` | `POST /api/voice/preview` | **VERIFIED** |
| **Voice Take Acceptance** | `app.tsx:847-849, 898` | `partials/tab_voice.html` | `static/js/voice.js` | `POST /api/projects/{pid}/takes/{take_id}/accept` | **VERIFIED** |
| **Project Voice Takes History** | `app.tsx:863-907` | `partials/tab_voice.html` | `static/js/voice.js` | `GET /api/projects/{pid}/takes` | **VERIFIED** |
| **Scene Visual Configuration** | `app.tsx:911-999` | `partials/tab_voice.html` | `static/js/media.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Pixabay Stock Sourcing** | `app.tsx:960-964` | `partials/tab_voice.html` | `static/js/media.js` | `GET /api/stock?q=...` | **VERIFIED** |
| **Per-Scene Footage Upload** | `app.tsx:966-970` | `partials/tab_voice.html` | `static/js/media.js` | `POST /api/assets` | **VERIFIED** |
| **Find All Stock Footage** | `app.tsx:607` | `partials/tab_voice.html` | `static/js/jobs.js` | `POST /api/projects/{pid}/jobs` | **VERIFIED** |
| **Manual Caption Cues Editor** | `app.tsx:1003-1046`| `partials/tab_voice.html` | `static/js/scenes.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Background Music & Ducking** | `app.tsx:1051-1074`| `partials/tab_voice.html` | `static/js/media.js` | `POST /api/assets` | **VERIFIED** |
| **Preview & Export Settings** | `app.tsx:1078-1096`| `partials/tab_export.html` | `static/js/projects.js` | `PUT /api/projects/{pid}` | **VERIFIED** |
| **Preflight Readiness Checklist**| `app.tsx:1097-1121`| `partials/tab_export.html` | `static/js/projects.js` | `GET /api/projects/{pid}/preflight` | **VERIFIED** |
| **Render Trigger (540p / 1080p)**| `app.tsx:1125-1130`| `partials/tab_export.html` | `static/js/jobs.js` | `POST /api/projects/{pid}/jobs` | **VERIFIED** |
| **Export Cards & Video Player** | `app.tsx:1134-1148`| `partials/tab_export.html` | `static/js/media.js` | `GET /api/exports/{jid}/{file}` | **VERIFIED** |
| **Production Jobs Panel** | `app.tsx:1152-1213`| `partials/job_panel.html` | `static/js/jobs.js` | `GET /api/projects/{pid}/jobs` | **VERIFIED** |
| **Job Retry & Cancellation** | `app.tsx:1173-1180`| `partials/job_panel.html` | `static/js/jobs.js` | `POST /api/jobs/{jid}/retry` / cancel | **VERIFIED** |
| **Apply Job Results to Project** | `app.tsx:1184-1188`| `partials/job_panel.html` | `static/js/jobs.js` | `POST /api/projects/{pid}/jobs/{jid}/apply` | **VERIFIED** |
| **Reusable Media Library Modal** | `app.tsx:1220-1274`| `partials/media_library.html` | `static/js/media.js` | `GET /api/assets`, `POST /api/projects/{pid}/assign-media` | **VERIFIED** |
| **Settings & Providers Modal** | `app.tsx:1276-1340`| `partials/settings_modal.html`| `static/js/settings.js` | `POST /api/settings`, `GET /api/health` | **VERIFIED** |
