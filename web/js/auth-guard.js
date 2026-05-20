/**
 * auth-guard.js — Role-based auth + geo utilities
 * Include after firebase-config.js on every protected page.
 */

// ─── AUTH GUARD ────────────────────────────────────────────────────────────
function requireAuth(requiredRole, rootPath = '/') {
  auth.onAuthStateChanged(async user => {
    if (!user) { window.location.replace(rootPath + 'login.html'); return; }
    
    let snap = await db.collection('users').doc(user.uid).get();
    if (!snap.exists) { 
      // Do not auto-create admin access. Default to student or force logout if invalid.
      // We will create a default student profile if missing to prevent breaking.
      await db.collection('users').doc(user.uid).set({
        email: user.email,
        role: 'student',
        createdAt: firebase.firestore.FieldValue.serverTimestamp()
      });
      snap = await db.collection('users').doc(user.uid).get();
    }
    const data = snap.data();
    const userRole = (data.role === 'admin') ? 'teacher' : (data.role || 'student');

    // Strict role enforcement — redirect to correct portal and STOP execution
    if (requiredRole && userRole !== requiredRole) {
      if (userRole === 'teacher') {
        window.location.replace(rootPath + 'admin/dashboard.html');
      } else {
        window.location.replace(rootPath + 'student/mark.html');
      }
      return; // CRITICAL: stop here, do NOT fire authReady
    }

    // Role matches — grant access
    window.currentUser = { uid: user.uid, email: user.email, ...data, role: userRole };
    document.dispatchEvent(new CustomEvent('authReady', { detail: window.currentUser }));
  });
}

async function signOut() {
  await auth.signOut();
  const isSubdir = window.location.pathname.includes('/admin/') || window.location.pathname.includes('/student/');
  window.location.href = isSubdir ? '../login.html' : 'login.html';
}

// ─── LIVE LOCATION INTELLIGENCE ────────────────────────────────────────────
let liveTrackerId = null;
function startLiveGeofencing(uid, cfg) {
  if (liveTrackerId) return;
  if (!navigator.geolocation) return;
  if (!cfg.location) return;

  const allowedRadius = cfg.locationRadius || 10;
  
  liveTrackerId = navigator.geolocation.watchPosition(async pos => {
    const dist = haversineDistance(
      pos.coords.latitude, pos.coords.longitude,
      cfg.location.lat, cfg.location.lon
    );
    if (dist > allowedRadius) {
      console.warn(`Geo-fence breached! ${dist}m away.`);
      const dateStr = new Date().toISOString().split('T')[0];
      // Match the docId format used when marking attendance
      const docId = `${dateStr}_${uid}`;
      try {
        const doc = await db.collection('attendance').doc(docId).get();
        if (doc.exists && doc.data().status === 'Present') {
          await db.collection('attendance').doc(docId).update({
            status: 'Invalidated',
            reason: `Left classroom (${Math.round(dist)}m away)`,
            invalidatedAt: firebase.firestore.FieldValue.serverTimestamp()
          });
          showToast('Attendance invalidated: You left the classroom.', 'error', 8000);
        }
      } catch(e) { console.warn('Geofence invalidation error:', e.message); }
    }
  }, err => {
    console.warn("Live tracking error:", err);
  }, { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 });
}

// Auto-start geofencing for students on any page that loads auth-guard
auth.onAuthStateChanged(async user => {
  if (user) {
    try {
      const snap = await db.collection('users').doc(user.uid).get();
      if (snap.exists && snap.data().role === 'student') {
        const cfgSnap = await db.collection('settings').doc('classroom').get();
        if (cfgSnap.exists && cfgSnap.data().locationCheckEnabled && cfgSnap.data().location) {
          startLiveGeofencing(user.uid, cfgSnap.data());
        }
      }
    } catch(e) { /* Settings may not exist yet */ }
  }
});

// ─── GEOLOCATION ────────────────────────────────────────────────────────────
/**
 * Haversine formula — compute distance between two GPS points (metres).
 */
function haversineDistance(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
  const dp = (lat2 - lat1) * Math.PI / 180;
  const dl = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dp/2)**2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Verify student is within allowed radius of classroom.
 * Returns { passed, distance, allowed, classroomSet, reason, userLat, userLon, accuracy }
 */
async function verifyLocation() {
  const settingsDoc = await db.collection('settings').doc('classroom').get();
  if (!settingsDoc.exists) return { passed: true, classroomSet: false, reason: 'No classroom location set' };

  const cfg = settingsDoc.data();
  if (!cfg.location || !cfg.locationCheckEnabled) return { passed: true, classroomSet: true, reason: 'Location check disabled' };

  const allowedRadius = cfg.locationRadius || 10;

  return new Promise(resolve => {
    if (!navigator.geolocation) {
      resolve({ passed: false, reason: 'Geolocation not supported on this device' });
      return;
    }
    navigator.geolocation.getCurrentPosition(
      pos => {
        // GPS Spoofing heuristic: unnaturally perfect accuracy
        if (pos.coords.accuracy <= 2.0 && !window.location.hostname.includes('localhost')) {
          console.warn("Suspicious GPS Accuracy:", pos.coords.accuracy);
        }

        const dist = haversineDistance(
          pos.coords.latitude, pos.coords.longitude,
          cfg.location.lat, cfg.location.lon
        );

        const isInside = dist <= allowedRadius;

        resolve({
          passed:        isInside,
          distance:      Math.round(dist),
          allowed:       allowedRadius,
          classroomSet:  true,
          userLat:       pos.coords.latitude,
          userLon:       pos.coords.longitude,
          accuracy:      Math.round(pos.coords.accuracy),
          reason:        isInside
            ? `Within classroom (${Math.round(dist)}m)`
            : `Outside geofence (${Math.round(dist)}m away, max ${allowedRadius}m)`
        });
      },
      err => {
        let msg = err.message;
        if (err.code === 1) msg = "Location permission denied. Please enable GPS.";
        resolve({ passed: false, reason: `GPS error: ${msg}` });
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
    );
  });
}

// ─── SHARED TOAST ────────────────────────────────────────────────────────────
function showToast(msg, type = 'info', duration = 5000) {
  let tc = document.getElementById('toast-container');
  if (!tc) { tc = document.createElement('div'); tc.id = 'toast-container'; document.body.appendChild(tc); }
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = `<span>${msg}</span><span class="toast-close" onclick="this.parentElement.remove()">X</span>`;
  tc.appendChild(t);
  setTimeout(() => t.remove(), duration);
}

// ─── SHARED SIDEBAR RENDERER ──────────────────────────────────────────────
function renderAdminSidebar(activePage) {
  const items = [
    { href: 'dashboard.html', icon: 'bar-chart-2',  label: 'Dashboard' },
    { href: 'live.html',      icon: 'video',         label: 'Live Scan' },
    { href: 'students.html',  icon: 'users',         label: 'Students' },
    { href: 'manual.html',    icon: 'edit-3',        label: 'Manual' },
    { href: 'reports.html',   icon: 'folder',        label: 'Reports' },
    { href: 'settings.html',  icon: 'settings',      label: 'Settings' },
  ];
  return `
    <aside class="sidebar">
      <div class="sidebar-logo"><img src="../logo.png" alt="logo" style="height:36px;border-radius:6px;box-shadow:0 0 14px rgba(56,189,248,0.4);"></div>
      <nav class="sidebar-menu">
        ${items.map(i => `
          <a href="${i.href}" class="menu-item ${activePage===i.href?'active':''}">
            <span class="menu-icon"><i data-lucide="${i.icon}"></i></span>${i.label}
          </a>`).join('')}
      </nav>
      <div class="sidebar-footer">
        <div class="user-profile" id="sb-user">
          <div class="avatar" style="width:36px;height:36px;background:var(--gradient-primary);border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;" id="sb-avatar">?</div>
          <div class="user-info"><h4 id="sb-name">Loading...</h4><p id="sb-role">Teacher</p></div>
        </div>
        <button class="btn-danger" style="width:100%;margin-top:12px;font-size:12px;" onclick="signOut()">
          <i data-lucide="log-out"></i> Sign Out
        </button>
      </div>
    </aside>`;
}

document.addEventListener('authReady', e => {
  const u = e.detail;
  const av = document.getElementById('sb-avatar');
  if (av) av.textContent = (u.name || u.email || '?')[0].toUpperCase();
  const nm = document.getElementById('sb-name');
  if (nm) nm.textContent = u.name || u.email;
  const rl = document.getElementById('sb-role');
  if (rl) rl.textContent = u.role === 'teacher' ? 'Teacher / Admin' : 'Student';
});
