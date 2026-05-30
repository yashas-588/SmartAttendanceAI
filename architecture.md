# Technical Architecture & System Design Documentation
## Project: Smart AI Attendance System
**Version:** 2.0  
**Security Status:** Confidential / Academic-Production  
**Target Readers:** Engineers, AI Coding Assistants, Deployment Architects  

---

## 1. Project Overview

The **Smart AI Attendance System** is an enterprise-grade, privacy-first, serverless-ready biometric attendance validation platform. It is architected specifically to solve classic proxy attendance problems using edge-computed facial identification, multi-layered anti-spoofing (active challenge-response and passive texture analysis), dynamic geographic positioning (Geofencing), and automated session management.

By design, the system utilizes a **hybrid edge-cloud topology** to maximize privacy and efficiency:
* **Edge Processing**: High-performance face detection, landmark localization, face embedding extraction, and active liveness checking occur entirely inside the student's browser. This guarantees that raw student webcam images never leave the client device, complying with biometric privacy regulations and saving massive server compute overhead.
* **Serverless Backend**: The Flask microservice acts as a secure trust broker, managing cryptographic HMAC-SHA256 challenge tokens, validating classroom geofences, performing atomic database operations, and triggering transactional email notifications.

---

## 2. Current Architecture Overview

The system architecture consists of a static frontend deployed to **Firebase Hosting** and a containerized Python Flask backend ready for **Google Cloud Run**. The frontend communicates with the backend via secure REST API requests rewritten under `/api/**`.

```mermaid
graph TD
  subgraph "Client Edge Device (Web Browser)"
    StudentUI["Student Portal (mark.html)"]
    LivenessEngine["Liveness Engine (liveness.js)"]
    FaceEngine["Face Engine (face-api.js)"]
    StudentUI -->|Webcam Frames| LivenessEngine
    StudentUI -->|128-d Descriptors| FaceEngine
  end

  subgraph "Serverless Infrastructure (Firebase & Cloud Run)"
    FirebaseHosting["Firebase Hosting (Static Web App)"]
    CloudRun["Google Cloud Run (Flask API Container)"]
    Firestore["Firestore (NoSQL Database)"]
    Resend["Resend API (Transactional Email)"]
    
    FirebaseHosting -.->|/api/.. Rewrites| CloudRun
    CloudRun -->|Read/Write Documents| Firestore
    CloudRun -->|Send Emails| Resend
  end

  StudentUI -->|1. GET /api/request-challenge| CloudRun
  StudentUI -->|2. POST /api/verify-liveness| CloudRun
  StudentUI -->|3. POST /api/mark-attendance| CloudRun
```

### Flow of Operations (Summary)
1. **Onboarding**: Teachers import a CSV list of students. They capture 5 live face frames of each student; the client-side `FaceEngine` computes the mean 128-d descriptor centroid and writes it to Firestore. The backend automatically creates a Firebase Auth account for the student and triggers a welcome email.
2. **Session Creation**: Teachers generate scheduled classes from templates or start a manual class. The session becomes `active` with a set geofence (location + radius) and duration.
3. **Liveness Check**: When a student triggers the portal, the backend issues a signed, randomized sequence of 3 liveness challenges. The client validates these challenges frame-by-frame via MediaPipe FaceMesh.
4. **Verification & Logging**: On challenge completion, the backend verifies timing and anti-spoofing texture scores, then issues an HMAC liveness token.
5. **Marking Attendance**: The student's device matches their webcam descriptors with their stored template via Euclidean distance, fetches location coordinates, validates the geofence, and submits the token. The backend commits the attendance atomically.

---

## 3. Frontend Architecture

The frontend is constructed using semantic **HTML5**, modern **Vanilla CSS**, and native **JavaScript (ES6)**. It utilizes a glassmorphism dark-theme dashboard design system built using CSS custom properties (`--bg-glass`, `--border-glass`, `--neon-blue`, etc.).

### Navigation & Layout Structure
* **Authentication Guard (`web/js/auth-guard.js`)**:
  * Decouples pages from public access by listening to Firebase Auth state transitions.
  * Intercepts unauthorized hits, verifies roles in Firestore `users/{uid}`, renders corresponding sidebar templates dynamically, and initializes UI assets after dispatching an `authReady` event.
* **Responsive Layouts**: Designed around a 240px persistent sidebar on desktops, adapting to a top navbar with drawer layout on mobile devices.

### Core Teacher Portal Interfaces (`web/admin/`)
* **`dashboard.html`**: Visualizes real-time metrics (Today's Scheduled Classes, Present Count, Active geofence status).
* **`students.html`**: A grid management UI supporting bulk CSV import, multi-select student deletions, and a live face registration modal.
* **`session.html`**: The command center for session lifecycles. Supports an "Add Extra Class" modal, an active session monitor, and a real-time table showing student arrivals.
* **`timetable.html`**: A dual-mode scheduling interface allowing teachers to view their weekly grid and upload timetable images for OCR conversion.
* **`live.html`**: Admin-controlled classroom camera interface that runs continuous multi-face scans, identifying matching student names and bounding boxes in real-time.
* **`manual.html` & `reports.html`**: Management UIs to manually mark overrides and export styled Excel/CSV sheets.

### Core Student Portal Interfaces (`web/student/`)
* **`mark.html`**: The single action hub for student check-ins. Loads models, starts the webcam, runs liveness visual challenges, runs face matching, grabs location coordinates, and displays real-time status chips.
* **`history.html`**: Personal dashboard displaying attendance analytics, present ratios, and historic records.

---

## 4. Backend Architecture

The backend is built as a Flask REST API (`src/app.py`), structured to operate statelessly as a **Trust Broker**. The backend handles all database updates and security authorizations.

### Architectural Rules of the Trust Broker
1. **Stateless Operations**: The backend keeps no in-memory session registers. All states (active sessions, liveness cooldowns) are read from Firestore on-demand to guarantee infinite horizontal scalability in serverless runtime environments.
2. **边缘计算 Edge Delegation**: Heavy OpenCV/neural-network image processing is pushed to the client browser. The backend only uses lightweight Python calculations, allowing it to serve thousands of concurrent requests inside standard 512MB RAM serverless instances.
3. **Double Verification**: The backend relies on cryptographic verification (HMAC-SHA256 signatures) to ensure that the client-side liveness engine actually approved the student before attendance is logged.

```mermaid
graph LR
  subgraph "Flask Backend APIs (app.py)"
    AuthDecorator["@_require_teacher"]
    ChallengeAPI["/api/request-challenge"]
    VerifyAPI["/api/verify-liveness"]
    MarkAPI["/api/mark-attendance"]
    ImportAPI["/api/students/import"]
  end

  subgraph "Helper Services"
    SM["Session Manager (session_manager.py)"]
    EM["Email Service (email_service.py)"]
  end

  ChallengeAPI --> SM
  VerifyAPI --> SM
  MarkAPI --> SM
  ImportAPI --> EM
```

---

## 5. Database Design

The database utilizes **Cloud Firestore (NoSQL Document Store)**. Data is organized into collections optimizing read speed for live student check-ins.

### Database Relationships
* **`timetable` (Templates) ── 1:N ──> `scheduled_sessions` (Instances)**: Weekly timetable slots act as templates. The system instances them into `scheduled_sessions` documents across date ranges.
* **`sessions` (Live Channels) ── 1:1 ──> `scheduled_sessions`**: Starting a scheduled session links it to a live execution document under `sessions`.
* **`students` ── 1:1 ──> `users`**: Authenticated students have a matching document in the `users` credentials mapping.

### Performance Indexing Strategy
* Compound query performance is maximized by structuring collections to avoid complex composite index requirements. For example, the `timetable` query uses dynamic client-side array sorting on weekday orders to avoid creating composite indexes on `[teacher_id, day, start_time]`.
* Atomic increment operators (`firestore.Increment(1)`) are utilized on `attendance_count` values inside active sessions to prevent race condition write blocks.

---

## 6. Firebase Structure

### Authentication
* Authenticated users are divided into `teacher` and `student` roles.
* Student authentication accounts are programmatically generated using their institutional emails as the primary key.

### Firestore Security Rules (`firestore.rules`)
```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    function isTeacher() {
      return request.auth != null && 
             get(/databases/$(database)/documents/users/$(request.auth.uid)).data.role == 'teacher';
    }

    match /users/{userId} {
      allow read: if request.auth != null && (request.auth.uid == userId || isTeacher());
      allow write: if request.auth != null && request.auth.uid == userId;
    }

    match /attendance/{docId} {
      allow read: if request.auth != null && (request.auth.uid == resource.data.student_uid || request.auth.uid == resource.data.studentId || isTeacher());
      allow write: if false; // Strict! Only Admin SDK (Flask API) can write
    }

    match /sessions/{sessionId} {
      allow read: if request.auth != null;
      allow write: if isTeacher();
    }

    match /students/{studentId} {
      allow read: if request.auth != null && (request.auth.uid == resource.data.uid || isTeacher());
      allow write: if isTeacher();
    }
  }
}
```

### Firebase Storage Rules (`storage.rules`)
Allows optional student document storage. Foldered by student UID.
```javascript
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /student_documents/{studentId}/{allPaths=**} {
      allow read: if request.auth != null && (
        request.auth.uid == studentId ||
        firestore.get(/databases/(default)/documents/users/$(request.auth.uid)).data.role in ['teacher', 'admin']
      );
      allow write: if request.auth != null && (
        firestore.get(/databases/(default)/documents/users/$(request.auth.uid)).data.role in ['teacher', 'admin']
      );
    }
  }
}
```

---

## 7. Authentication Flow

The backend handles security gates using a Bearer token verification loop.

```mermaid
sequenceDiagram
    participant User as Teacher Client
    participant Auth as Firebase Auth
    participant Db as Firestore
    participant API as Flask API

    User->>Auth: Login with email/password
    Auth-->>User: Return ID Token (JWT)
    User->>API: GET /api/timetable (Header: Authorization Bearer JWT)
    API->>Auth: Verify JWT Token Signature
    Auth-->>API: Return Decoded UID
    API->>Db: Query users/{UID} role
    Db-->>API: Return role = 'teacher'
    API-->>User: 200 OK (Send Timetable Payload)
```

---

## 8. Face Enrollment Flow

The enrollment process focuses on biometric capturing without administrative friction.

1. **Importing**: Teacher uploads a CSV file of students (containing Email, Name, USN, Department, Section, and Parent Email). Student cards appear with a softer status badge: `Face Setup Needed`.
2. **Registration Modal**: Teacher opens the enrollment interface, prompting webcam stream access.
3. **Feature Capture**: The client `face-api.js` captures 5 separate high-confidence face images (200ms intervals).
4. **Embedding Mean Centroid**: The system computes the 128-dimensional descriptor vectors, averages them into a mean vector to reduce illumination/noise vectors, and writes them to the student's Firestore document as a map (`d0` through `d4`).
5. **Auto Provisioning**:
   * On successful descriptor write, the Flask backend creates a student login account in **Firebase Auth** with their imported email.
   * Generates a temporary setup link.
   * Creates a Firestore document in `users/{uid}` with role `student`.
   * Sends a styled HTML welcome notification via Resend. The student badge automatically turns to `Setup Complete`.

---

## 9. Attendance Verification Flow

The attendance marking flow is heavily guarded to block spoofing attempts.

```mermaid
graph TD
    Start([Student clicks Mark Attendance]) --> AuthCheck{Is authenticated?}
    AuthCheck -- No --> Login[Redirect to login.html]
    AuthCheck -- Yes --> GetActive{Session active & unexpired?}
    
    GetActive -- No --> ErrActive[Display No Active Session]
    GetActive -- Yes --> RequestChallenge[POST /api/request-challenge]
    
    RequestChallenge --> RunChallenges[Client executes 3 liveness gestures]
    RunChallenges --> VerifyLiveness[POST /api/verify-liveness]
    
    VerifyLiveness -- Fail --> CooldownCheck{3 failures in 15m?}
    CooldownCheck -- Yes --> Lockout[Block student for 5 mins]
    CooldownCheck -- No --> RequestChallenge
    
    VerifyLiveness -- Pass --> IssueHMAC[Server issues signed Liveness Token]
    IssueHMAC --> GeofenceCheck{Within class GPS geofence?}
    
    GeofenceCheck -- No --> ErrGeo[Display Geofence Error]
    GeofenceCheck -- Yes --> FaceMatch{face-api.js Euclidean distance <= 0.45?}
    
    FaceMatch -- No --> ErrFace[Display Face Recognition Error]
    FaceMatch -- Yes --> MarkAttendance[POST /api/mark-attendance]
    
    MarkAttendance --> Transact[Server Transaction: Set present, atomic increment]
    Transact --> Done([Attendance marked Present ✓])
```

---

## 10. Active Liveness / Anti-Spoofing Flow

The active liveness validation layer uses a multi-modal weighted fusion scoring model (IEEE-aligned algorithms) that prevents screen replay and picture presentation attacks.

### Active Gesture Challenge State Loop
When the client calls `/api/request-challenge`, the backend randomly selects **3 gestures** from a pool of 6:
* `blink twice` (EAR tracking)
* `turn head left` (Head Pose Yaw < 0.65)
* `turn head right` (Head Pose Yaw > 1.54)
* `smile` (Mouth smile ratio delta > 15%)
* `move closer` (Eye distance zoom increase > 15%)
* `raise eyebrows` (Eyebrow-to-eye landmark distance increase > 15%)

The client must complete these gestures in the exact sequence within a **90-second limit**.

### Passive Anti-Spoofing Score Fusion
In parallel, the engine computes passive mathematical scores:
1. **LBP Texture Analysis (IEEE TIFS 2011)**: Detects skin micro-textures. Uniform LBP patterns are compared against a threshold of 22. If texture score falls below 0.3, a screen replay attack is flagged (`REPLAY_SUSPICION`).
2. **rPPG green-channel fluctuation (IEEE Trans. Biomed. Eng. 2013)**: Detects pulse variance in the skin Green color channel. Spoilers (printed paper/screens) show zero pulse variance.
3. **Optical Flow temporal checks**: Detects 3D motion transitions to block flat 2D photograph presentation.
4. **Laplacian Blur variance**: Checks that the camera feed is sharp and in focus (Variance > 80) to avoid artificial blur overrides.

### Liveness Cooldown & Replay Protection
* **Failed Attempts Register**: If a student fails verification 3 times within 15 minutes, their account document triggers a 5-minute lockout timer (`liveness_cooldown`).
* **Cryptographic Issuance**: Successful check-ins result in an HMAC liveness token:
  $$\text{Payload} = \text{student\_uid} : \text{session\_id} : \text{timestamp}$$
  $$\text{HMAC} = H_{\text{SHA-256}}(\text{Payload}, \text{SecretKey})$$
  This token is valid for only 3 minutes (180s), blocking token-capture replays.

---

## 11. Session & Timetable System

The session manager utilizes a precise state-machine to prevent time gaps and class overlaps.

### Timetable Template vs Live Session States
```mermaid
stateDiagram-v2
    [*] --> scheduled : Session generated from Template
    scheduled --> active : Teacher clicks Start Session
    scheduled --> expired : Current time > Session end_time
    active --> completed : Teacher clicks End Session (Absences computed)
    expired --> cancelled : Teacher cancels/removes session
    scheduled --> cancelled : Teacher deletes scheduled class
```

### Key Logic Features
* **Overlap Prevention (`_ss_overlap`)**: Whenever a scheduled class is manually added or generated from a weekly template, the backend checks for overlapping times:
  $$\text{NewStart} < \text{ExistingEnd} \quad \text{AND} \quad \text{NewEnd} > \text{ExistingStart}$$
  Overlaps trigger a `409 Conflict` error.
* **Lazy Deactivation**: The system executes lazy-expiry checks. When GET `/api/scheduled-sessions/today` is loaded, any session in `scheduled` state whose `end_time` has passed is automatically marked `expired` in Firestore, maintaining clean dashboard interfaces.

---

## 12. CSV Import Pipeline

The student bulk CSV import workflow parses standard text records securely inside the Flask REST engine:
1. **CSV Upload**: The teacher uploads a `.csv` sheet containing header rows: `Name`, `USN`, `Email`, `Parent Email`, `Department`, `Section`.
2. **Sanitization Loop**:
   * Trims whitespace, parses text rows, and drops incomplete fields.
   * Runs duplicate checks against database records to prevent index overrides.
3. **Card Provisioning**: Inserts student records into Firestore (`students` collection) with `uid: null` and face state marked as `Face Setup Needed`.
4. **Biometric Awaiting state**: The student remains dormant until their face descriptors are physically captured via the dashboard camera.

---

## 13. Email Notification System

The email pipeline is built around the **Resend transactional email API** (`src/email_service.py`).

### Supported Email Channels
* **Student Welcome notifications**: Triggered on biometric enrollment completion. Provides students with access URLs (`/student/mark.html`) and login instructions.
* **Parental Absence alerts**: Dispatched immediately when a session is ended. Contains the student's name, subject, class ID, and a formatted timestamp.

### Performance Architecture
* **Non-Blocking Background Threads**: To prevent blocking Flask request threads, absence emails run inside a background daemon thread (`send_absent_emails_async`).
* **Audit Logging (`email_logs`)**: Every attempt writes an audit trace to Firestore containing recipient details, delivery status, unique message reference tags, and error logs for teacher monitoring.
* **idempotent De-duplication**: Before dispatching, the email loop queries `email_logs` for `session_id` + `student_uid` to guarantee parents never receive duplicate alerts for the same class session.

---

## 14. Security Architecture

1. **Dynamic Geofencing (Haversine formula)**:
   * Client-side geolocation coordinates are verified on the server.
   * Distance between student coordinates $(lat_1, lon_1)$ and classroom coordinates $(lat_2, lon_2)$ is computed:
     $$d = 2R \arcsin\left(\sqrt{\sin^2\left(\frac{\Delta lat}{2}\right) + \cos(lat_1)\cos(lat_2)\sin^2\left(\frac{\Delta lon}{2}\right)}\right)$$
   * Marks attendance only if distance $d \le \text{classroom radius}$ (typically 10-15 meters).
2. **Double-marking Prevention**: The backend marker uses a strict Firestore transactional query checking for `{session_id}_{student_uid}` document existences. This blocks double-writes and concurrency race conditions.
3. **Strict HMAC Authentication**: HMAC tokens are generated using server-side secrets. The student portal has zero knowledge of the secret key.

---

## 15. Deployment Architecture

The production environment runs serverless on Google Cloud Platform:
* **Static Assets**: Hosted via **Firebase Hosting** for global CDN speed.
* **Dynamic API Services**: The Python Flask backend is built as a Docker container using the base image `python:3.10-slim`, including runtime requirements for OpenCV/MediaPipe. Deployed to **Google Cloud Run** in `us-central1`.
* **Reverse Proxy Rewriting**: Static files call local APIs (`/api/**`). The Firebase configuration handles internal reverse-proxy rewrites directly to Cloud Run, resolving all cross-origin resource sharing (CORS) preflight issues.

---

## 16. Firestore Collections

### `users`
* *Path:* `/users/{userId}`
* *Purpose:* Teacher & student credentials mapping.
* *Properties:*
  ```json
  {
    "role": "teacher" | "student",
    "email": "string",
    "name": "string"
  }
  ```

### `students`
* *Path:* `/students/{studentId}`
* *Purpose:* Core biometric templates and information.
* *Properties:*
  ```json
  {
    "uid": "string | null",
    "name": "string",
    "email": "string",
    "parent_email": "string",
    "department": "string",
    "section": "string",
    "usn": "string",
    "descriptors": {
      "d0": [128, "floats"],
      "d1": [128, "floats"],
      "d2": [128, "floats"],
      "d3": [128, "floats"],
      "d4": [128, "floats"]
    },
    "updatedAt": "timestamp"
  }
  ```

### `sessions`
* *Path:* `/sessions/{sessionId}`
* *Purpose:* Live active attendance sessions.
* *Properties:*
  ```json
  {
    "teacher_id": "string",
    "class_id": "string",
    "subject": "string",
    "created_at": "timestamp",
    "expires_at": "timestamp",
    "is_active": "boolean",
    "duration_minutes": "integer",
    "location": { "lat": "number", "lon": "number" },
    "location_radius": "integer",
    "attendance_count": "integer"
  }
  ```

### `scheduled_sessions`
* *Path:* `/scheduled_sessions/{schedId}`
* *Purpose:* Calendar session instances.
* *Properties:*
  ```json
  {
    "teacher_id": "string",
    "date": "string (YYYY-MM-DD)",
    "day": "string",
    "start_time": "string (HH:MM)",
    "end_time": "string (HH:MM)",
    "subject": "string",
    "class_id": "string",
    "room": "string",
    "department": "string",
    "semester": "string",
    "section": "string",
    "status": "scheduled" | "active" | "completed" | "expired" | "cancelled",
    "session_id": "string | null",
    "timetable_entry_id": "string | null",
    "created_manually": "boolean",
    "createdAt": "timestamp",
    "updatedAt": "timestamp"
  }
  ```

### `timetable`
* *Path:* `/timetable/{entryId}`
* *Purpose:* Weekly timetable slots.
* *Properties:*
  ```json
  {
    "teacher_id": "string",
    "day": "Monday" | "Tuesday" | "Wednesday" | "Thursday" | "Friday" | "Saturday" | "Sunday",
    "start_time": "string (HH:MM)",
    "end_time": "string (HH:MM)",
    "subject": "string",
    "class_id": "string",
    "room": "string",
    "department": "string",
    "semester": "string",
    "section": "string",
    "createdAt": "timestamp"
  }
  ```

### `attendance`
* *Path:* `/attendance/{sessionId_studentUid}`
* *Purpose:* Logged attendance records.
* *Properties:*
  ```json
  {
    "name": "string",
    "student_uid": "string",
    "studentId": "string",
    "date": "string (YYYY-MM-DD)",
    "time": "string (HH:MM:SS)",
    "status": "Present" | "Absent" | "Late",
    "method": "string",
    "confidence": "number",
    "session_id": "string",
    "timestamp": "timestamp",
    "location": { "lat": "number", "lon": "number" }
  }
  ```

### `liveness_cooldown`
* *Path:* `/liveness_cooldown/{studentUid}`
* *Purpose:* Brute-force lockout monitoring.
* *Properties:*
  ```json
  {
    "failed_attempts": ["timestamp"],
    "cooldown_until": "timestamp | null"
  }
  ```

### `email_logs`
* *Path:* `/email_logs/{logId}`
* *Purpose:* Outgoing email logs.
* *Properties:*
  ```json
  {
    "type": "absence_notification" | "student_welcome",
    "recipient": "string",
    "status": "sent" | "failed",
    "ref": "string (msg_id)",
    "sentAt": "timestamp",
    "student_uid": "string",
    "session_id": "string",
    "subject": "string"
  }
  ```

---

## 17. API Endpoint Documentation

All endpoints reside under `/api/*`. Teacher-authorized routes require a `Bearer ID Token` in headers.

### Attendance & Liveness APIs
#### 1. POST `/api/request-challenge`
* *Authorized:* Student
* *Payload:*
  ```json
  { "session_id": "string", "device_fingerprint": "string" }
  ```
* *Response (200):*
  ```json
  {
    "success": true,
    "challenge_sequence": ["blink twice", "smile", "turn head left"],
    "challenge_token": "string (HMAC signed payload)"
  }
  ```

#### 2. POST `/api/verify-liveness`
* *Authorized:* Student
* *Payload:*
  ```json
  {
    "session_id": "string",
    "challenge_token": "string",
    "device_fingerprint": "string",
    "completed_challenges": ["blink twice", "smile", "turn head left"],
    "attempt_duration": 14.2,
    "scores": { "quality": 1.0, "texture": 0.85, "biometric": 0.9, "head": 1.0, "blink": 1.0 }
  }
  ```
* *Response (200):*
  ```json
  {
    "success": true,
    "liveness_token": "string (HMAC signed verification token)"
  }
  ```

#### 3. POST `/api/mark-attendance`
* *Authorized:* Student
* *Payload:*
  ```json
  {
    "session_id": "string",
    "liveness_token": "string",
    "lat": 12.9716,
    "lon": 77.5946,
    "matched_name": "John Doe",
    "confidence": 0.92
  }
  ```
* *Response (200):*
  ```json
  {
    "success": true,
    "name": "John Doe",
    "message": "John Doe — Marked Present ✓",
    "confidence": 0.92
  }
  ```

### Student Management APIs
#### 4. GET `/api/students`
* *Authorized:* Teacher
* *Response (200):* Array of students.

#### 5. DELETE `/api/students/<uid>`
* *Authorized:* Teacher
* *Response (200):* `{ "success": true }`

#### 6. POST `/api/students/import`
* *Authorized:* Teacher
* *Payload:* CSV parsed file data. Creates database entries and triggers Auth welcomes.

### Scheduled Class Session APIs
#### 7. GET `/api/scheduled-sessions/today`
* *Authorized:* Teacher
* *Response (200):* Array of today's scheduled classes (lazy deactivates past sessions).

#### 8. POST `/api/scheduled-sessions`
* *Authorized:* Teacher
* *Payload:* `{ "date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "subject": "string", "class_id": "string" }`
* *Response (201):* `{ "success": true, "id": "string" }`

#### 9. POST `/api/scheduled-sessions/generate`
* *Authorized:* Teacher
* *Payload:* `{ "entries": [...], "week_start": "YYYY-MM-DD", "week_end": "YYYY-MM-DD" }`
* *Response (200):* `{ "success": true, "created": 4, "skipped": 1, "errors": [] }`

#### 10. POST `/api/scheduled-sessions/<sched_id>/start`
* *Authorized:* Teacher
* *Payload:* `{ "location": { "lat": 12.9, "lon": 77.5 }, "location_radius": 15 }`
* *Response (200):* Starts attendance channel. Returns session data.

---

## 18. OCR Workflow

The timetable import workflow provides dynamic template generation:
1. **File Drop**: Teacher drags a timetable image into the uploader area in `timetable.html`.
2. **CDN Engine Pre-warming**: The client lazyloads **Tesseract.js** from CDN.
3. **OCR Processing**: Runs image binarization, scanning text columns for days (Monday–Sunday) and timestamps matching time patterns (e.g. `HH:MM`).
4. **Editable Preview**: Extracted rows (Day, Subject, Start Time, End Time, Section) are populated into an interactive table.
5. **Template Conversion**: The teacher reviews the OCR rows, corrects typos, and clicks "Confirm". The client submits the rows to `/api/timetable`, creating template records in Firestore.

---

## 19. Current Tech Stack

* **Frontend Layout**: HTML5, Vanilla CSS3 (Custom Design System CSS variables).
* **Programming Languages**: JavaScript (ES6+), Python 3.10.
* **Server Framework**: Flask (WSGI Web Server Gateway Interface).
* **Databases**: Google Cloud Firestore (NoSQL, serverless).
* **File Storage**: Google Cloud Storage (Bucket integration).
* **Deployment/Containerization**: Docker (Lightweight python-slim layers).
* **Reverse Proxy**: Firebase Hosting Rewrites.
* **Email Delivery**: Resend API via HTTP REST wrappers.

---

## 20. Libraries & Frameworks Used

### Frontend (Client-side)
* **`face-api.js` (vladmandic/face-api)**: Mobile-optimized JS neural net for FaceNet-128 descriptor extraction.
* **`MediaPipe FaceMesh`**: Used for 468 landmark tracking to detect eye blinks, facial gestures, and head pose.
* **`Tesseract.js`**: In-browser OCR library used to extract timetable records.
* **`Firebase Web Compat SDK (v10.12.0)`**: Authenticated synchronization client.
* **`Lucide Icons`**: Modern SVG asset manager.

### Backend (Server-side)
* **`Flask` & `Flask-CORS`**: Web engine & cross-origin authorization layers.
* **`firebase-admin`**: Google SDK for Cloud Auth triggers and transactional writes.
* **`opencv-python`**: Support for low-level image processing.
* **`numpy` & `scipy`**: Fast matrix computation routines.
* **`requests`**: Transactional email request handler.

---

## 21. Hosting & Deployment Flow

```mermaid
graph TD
  LocalCode[Local Workspace Code] -->|1. Firebase Deploy| WebHosting[Firebase Hosting web/]
  LocalCode -->|2. Docker Build & Push| ArtifactRegistry[Google Artifact Registry]
  ArtifactRegistry -->|3. Container Deploy| CloudRun[Google Cloud Run API]
  
  subgraph "Server Actions"
    CloudRun -->|Write Documents| CloudFirestore[Cloud Firestore Database]
    CloudRun -->|Write Files| StorageBucket[Firebase Storage Bucket]
  end
```

---

## 22. Current Limitations

1. **Browser Webcam Dependence**: If a student denies camera access, liveness checks are blocked.
2. **Client-side Neural processing**: Devices with low-end CPU/GPU specs might experience lag during FaceMesh tracking loops.
3. **Geofencing Precision**: Relies on browser-computed GPS coordinates. Heavy walls or indoor interference can degrade accuracy.
4. **Local Host Hosting**: The Flask server runs locally in the current developer workflow (Cloud Run integration exists but is not currently live as a production remote instance).

---

## 23. Current Production Readiness Status

* 🟢 **Static Web Application on Firebase Hosting**: `PRODUCTION-READY` (Highly performant, CSS tokens stabilized, layout optimized).
* 🟢 **Cloud NoSQL Database (Firestore Rules)**: `PRODUCTION-READY` (Data schemas validated, rules highly secure, atomic counts live).
* 🟢 **In-browser biometric facial identification & Liveness Engine**: `PRODUCTION-READY` (Model CDNs live, EAR blink and texture heuristics tested).
* 🟢 **Transactional Email Audit Trail (Resend)**: `PRODUCTION-READY` (Templates designed, non-blocking background runners deployed).
* 🟢 **Flask Backend Containerization (Dockerfile)**: `PRODUCTION-READY` (Lightweight base, system dependencies optimized).
* 🟡 **API Deployment**: `LOCALHOST-ONLY / CLOUD-RUN-READY` (Configured inside `firebase.json` rewrites, but runs locally on developer machines).

---

## 24. Suggested Future Scope (Strictly on Existing Architecture)

1. **Automated Cron Triggers**: Implement Google Cloud Scheduler cron triggers to call `/api/scheduled-sessions/today` at class boundaries, automating lazy-expiry deactivations and parental absence alerts without requiring teacher actions.
2. **Service Worker Offline Cache**: Set up progressive offline caching for model assets (`@vladmandic/face-api` & `MediaPipe`) inside student browsers to optimize check-in page load times.
3. **Automatic GPS Calibration**: Allow teachers to dynamically set classroom Geofence coordinate centers using their device's current location when starting live sessions.
