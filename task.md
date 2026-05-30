# Timetable-Driven Session Scheduling — Tasks

## Backend (src/app.py)
- [x] `_ss_serialize()` and `_ss_overlap()` helpers
- [x] `GET /api/scheduled-sessions/today` with lazy expiry
- [x] `GET /api/scheduled-sessions` with date range filter
- [x] `POST /api/scheduled-sessions` — manual creation + overlap prevention
- [x] `POST /api/scheduled-sessions/generate` — generate from timetable entries, dedup + overlap
- [x] `POST /api/scheduled-sessions/<id>/start` — start → creates sessions doc, links session_id
- [x] `POST /api/scheduled-sessions/<id>/cancel`
- [x] `PUT /api/scheduled-sessions/<id>` — edit with overlap re-check
- [x] `DELETE /api/scheduled-sessions/<id>`

## timetable.html (Full Rewrite)
- [x] 2-tab layout: Weekly Schedule | Upload & OCR
- [x] Stats bar (total, today, subjects)
- [x] Day tabs (All / Mon–Sat) with today highlight
- [x] Entry cards with edit/delete actions
- [x] Add/Edit entry modal
- [x] Generate Sessions panel with date range picker → POST /api/scheduled-sessions/generate
- [x] OCR drag-and-drop upload (Tesseract.js 5, lazy-loaded)
- [x] Smart time-range OCR parser (extracts structured rows)
- [x] Editable OCR preview table (inline edit per cell, delete rows, add blank)
- [x] Import OCR rows to Firestore timetable
- [x] Raw OCR text collapsible

## session.html (Significant Update)
- [x] Scheduled Sessions Management panel replacing simple today panel
- [x] Status chips summary (N Scheduled / N Active / N Completed / N Expired / N Cancelled)
- [x] State-aware per-entry action buttons (Start / End / Cancel / Edit)
- [x] Active session green pulse indicator
- [x] Expired session "Start Late" affordance
- [x] Refresh button on panel
- [x] Add Extra Class modal (one-off manual session)
- [x] Edit modal (reuses Extra Class modal with pre-fill)
- [x] `loadScheduledSessions()` / `renderScheduledSessions()`
- [x] `startScheduledSession()` — calls /start endpoint, refreshes panel
- [x] `cancelScheduledSession()` — calls /cancel, refreshes
- [x] `openExtraClassModal()` / `closeExtraClassModal()` / `saveExtraClass()`
- [x] `openEditSchedModal()` — pre-fills modal from scheduledSessions array

## Validation
- [x] Python syntax check → PASS
- [x] Route registration check → 8 scheduled routes registered
- [x] Import check → PASS
