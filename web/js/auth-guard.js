/**
 * auth-guard.js — Role-based auth + geo utilities
 * Include after firebase-config.js on every protected page.
 */

// ─── AUTH GUARD ────────────────────────────────────────────────────────────
// ── Unified role-based redirect (mirrors login.html — keep in sync) ───────────
function _redirectByRole(role, rootPath) {
  if (role === 'admin' || role === 'teacher') {
    window.location.replace(rootPath + 'admin/dashboard.html');
  } else {
    window.location.replace(rootPath + 'student/mark.html');
  }
}

function requireAuth(requiredRole, rootPath = '/') {
  auth.onAuthStateChanged(async user => {
    console.log('[AUTH_DEBUG] Current User (onAuthStateChanged):', user);
    if (!user) { window.location.replace(rootPath + 'login.html'); return; }

    console.log('[AUTH_DEBUG] Current UID:', user.uid);
    try {
      console.log('[AUTH_DEBUG] Querying Firestore collection "users" for UID:', user.uid);
      let snap = await db.collection('users').doc(user.uid).get();
      if (!snap.exists) {
        // No Firestore profile found. This could mean:
        //   (a) The Firestore write from signup hasn't completed yet (race condition), OR
        //   (b) The account was created directly in Firebase Console.
        // IMPORTANT: Do NOT write role:'student' here — that would permanently overwrite
        // a teacher/admin role if this fires before doSignup()'s Firestore write completes.
        // Instead: log a warning and redirect to login so the user can try again cleanly.
        console.warn('[requireAuth] No users doc found for', user.uid,
          '— possible signup race condition or Console-created account.',
          'Redirecting to login. If this persists, set role manually in Firestore.');
        await auth.signOut();
        window.location.replace(rootPath + 'login.html');
        return;
      }

      const data = snap.data();
      console.log('[AUTH_DEBUG] Fetched user document data successfully:', data);
      
      // Normalise role: accept 'admin', 'teacher', 'student' explicitly.
      // Any unrecognised value is treated as 'student' (safe default).
      const rawRole = data.role || 'student';
      const userRole = ['admin', 'teacher', 'student'].includes(rawRole) ? rawRole : 'student';
      if (!['admin', 'teacher', 'student'].includes(rawRole)) {
        console.warn('[requireAuth] Unknown role in Firestore:', rawRole, '— defaulting to student.');
      }

      // Force password change for students if flagged
      if (userRole === 'student' && data.mustChangePassword === true) {
        // Under the Firebase reset email workflow, the student has already set their own secure
        // password via the email link before logging in. Thus, we clear the flag automatically.
        try {
          await db.collection('users').doc(user.uid).update({
            mustChangePassword: false,
            passwordLastChangedAt: firebase.firestore.FieldValue.serverTimestamp()
          });
          // Try to update students collection accountStatus (may fail if rules restrict student writes)
          try {
            const studentSnap = await db.collection('students').where('uid', '==', user.uid).limit(1).get();
            if (!studentSnap.empty) {
              await studentSnap.docs[0].ref.update({ accountStatus: 'Active' });
            }
          } catch(_) {}

          data.mustChangePassword = false;
        } catch(e) {
          console.error('[requireAuth] Failed to auto-clear mustChangePassword flag:', e);
          const isChangePasswordPage = window.location.pathname.endsWith('change-password.html');
          if (!isChangePasswordPage) {
            window.location.replace(rootPath + 'student/change-password.html');
            return;
          }
        }
      }

      // Strict role enforcement — redirect to correct portal and STOP execution.
      // A teacher/admin on a student page → admin dashboard.
      // A student on an admin page → student portal.
      if (requiredRole && userRole !== requiredRole) {
        console.warn('[requireAuth] Role mismatch: required=', requiredRole, 'actual=', userRole, '— redirecting.');
        _redirectByRole(userRole, rootPath);
        return; // CRITICAL: stop here, do NOT fire authReady
      }

      // Role matches — grant access
      window.currentUser = { uid: user.uid, email: user.email, ...data, role: userRole };
      console.log('[AUTH_DEBUG] Role matched. Dispatched authReady with detail:', window.currentUser);
      document.dispatchEvent(new CustomEvent('authReady', { detail: window.currentUser }));
    } catch (err) {
      console.error('[AUTH_DEBUG] Firestore query failed with error:', err);
    }
  });
}

async function signOut() {
  await auth.signOut();
  const isSubdir = window.location.pathname.includes('/admin/') || window.location.pathname.includes('/student/');
  window.location.href = isSubdir ? '../login.html' : 'login.html';
}

// ─── LIVE LOCATION INTELLIGENCE ────────────────────────────────────────────
let liveTrackerId = null;
let geofenceInvalidated = false;
async function startLiveGeofencing(uid, session) {
  if (liveTrackerId) return;
  if (!navigator.geolocation) return;

  let location = null;
  let allowedRadius = 10;
  let geofenceDisabled = false;

  if (session && session.location) {
    location = session.location;
    allowedRadius = (session.location_radius !== undefined && session.location_radius !== null) ? Number(session.location_radius) : 10;
    if (allowedRadius === 0) {
      geofenceDisabled = true;
    }
  } else {
    // Legacy session or no session -> fall back to classroom settings!
    try {
      const settingsDoc = await db.collection('settings').doc('classroom').get();
      if (settingsDoc.exists) {
        const cfg = settingsDoc.data();
        if (cfg.locationCheckEnabled === false) {
          geofenceDisabled = true;
        }
        location = cfg.location;
        allowedRadius = (cfg.locationRadius !== undefined && cfg.locationRadius !== null) ? Number(cfg.locationRadius) : 10;
        if (allowedRadius === 0) {
          geofenceDisabled = true;
        }
      }
    } catch(e) {
      console.warn('[GPS_TRACE] Failed to load geofence fallback settings in startLiveGeofencing:', e);
    }
  }

  if (geofenceDisabled || !location) {
    console.log('[GPS_TRACE] Live geofencing: Geofence disabled or location missing. Skipping live tracking.');
    return;
  }
  
  liveTrackerId = navigator.geolocation.watchPosition(async pos => {
    if (geofenceInvalidated) return;
    const dist = haversineDistance(
      pos.coords.latitude, pos.coords.longitude,
      location.lat, location.lon
    );
    if (dist > allowedRadius) {
      console.warn(`Geo-fence breached! ${dist}m away.`);
      try {
        const idToken = await auth.currentUser.getIdToken();
        const res = await fetch(getApiUrl('/api/invalidate-geofence'), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${idToken}`
          },
          body: JSON.stringify({
            lat: pos.coords.latitude,
            lon: pos.coords.longitude,
            distance: dist
          })
        });
        const data = await res.json();
        if (res.ok && data.success) {
          geofenceInvalidated = true;
          showToast('Attendance invalidated: You left the classroom.', 'error', 8000);
        }
      } catch(e) { console.warn('Geofence invalidation error:', e.message); }
    }
  }, err => {
    console.warn("Live tracking error:", err);
  }, { enableHighAccuracy: true, maximumAge: 10000, timeout: 5000 });
}

// Auto-start geofencing for students on any page that loads auth-guard (except mark.html on load)
auth.onAuthStateChanged(async user => {
  if (user) {
    try {
      const snap = await db.collection('users').doc(user.uid).get();
      if (snap.exists && snap.data().role === 'student') {
        // If we are on mark.html, do NOT auto-start geofencing on load.
        // It will be started manually after a successful attendance mark.
        const isMarkPage = window.location.pathname.endsWith('mark.html');
        if (isMarkPage) {
          console.log('[GPS_TRACE] On mark.html page. Delaying live geofencing until attendance is marked.');
          return;
        }

        const sessSnap = await db.collection('sessions')
          .where('is_active', '==', true)
          .orderBy('created_at', 'desc')
          .get();

        if (!sessSnap.empty) {
          let matchedSession = null;
          // Find a session matching student's class
          const studentSnap = await db.collection('students').where('uid', '==', user.uid).limit(1).get();
          if (!studentSnap.empty) {
            const studentData = studentSnap.docs[0].data();
            const sDept = (studentData.department || studentData.dept || '').trim().toLowerCase();
            const sSem = String(studentData.semester || '').trim().toLowerCase();
            const sSec = (studentData.section || '').trim().toLowerCase();

            for (const sdoc of sessSnap.docs) {
              const sessData = sdoc.data();
              const sessClass = (sessData.class_id || '').trim().toLowerCase();
              const sessDept = (sessData.department || '').trim().toLowerCase();
              const sessSem = String(sessData.semester || '').trim().toLowerCase();
              const sessSec = (sessData.section || '').trim().toLowerCase();

              let match = false;
              if (sessDept && sessSem && sessSec) {
                const cleanSem = val => val.replace(/\D/g, '') || val;
                match = (sDept.includes(sessDept) || sessDept.includes(sDept)) && 
                        (cleanSem(sSem) === cleanSem(sessSem)) && 
                        (sSec === sessSec);
              } else if (sessClass) {
                const cleanSem = val => val.replace(/\D/g, '') || val;
                const sSemDigit = cleanSem(sSem);
                match = (sDept ? sessClass.includes(sDept) : true) && 
                        (sSemDigit ? sessClass.includes(sSemDigit) : true) && 
                        (sSec ? sessClass.includes(sSec) : true);
              }
              if (match) {
                matchedSession = sessData;
                break;
              }
            }
          }
          
          if (matchedSession) {
            startLiveGeofencing(user.uid, matchedSession);
          } else {
            // Fallback for single teacher legacy or generic deployments
            startLiveGeofencing(user.uid, sessSnap.docs[0].data());
          }
        }
      }
    } catch(e) { console.warn('[GPS_TRACE] Error resolving live geofencing:', e); }
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
async function verifyLocation(session) {
  console.log(`[GPS_TRACE] verifyLocation triggered.`);

  // 1. If session exists but session.location is null/undefined, skip GPS entirely (do NOT fall back to classroom settings)
  if (session && (session.location === null || session.location === undefined)) {
    console.log(`[GPS_TRACE] Geofence disabled: session exists but session.location is null/undefined.`);
    return { passed: true, classroomSet: true, reason: 'Geofence disabled for this session' };
  }

  // 2. Fetch classroom settings to verify if locationCheckEnabled is false globally
  let settingsCheckEnabled = true;
  let globalClassroomLocation = null;
  let globalClassroomRadius = 10;
  
  try {
    const settingsDoc = await db.collection('settings').doc('classroom').get();
    if (settingsDoc.exists) {
      const cfg = settingsDoc.data();
      if (cfg.locationCheckEnabled === false) {
        settingsCheckEnabled = false;
      }
      globalClassroomLocation = cfg.location;
      globalClassroomRadius = (cfg.locationRadius !== undefined && cfg.locationRadius !== null) ? Number(cfg.locationRadius) : 10;
    }
  } catch (e) {
    console.warn('[GPS_TRACE] Failed to load global classroom settings:', e);
  }

  // 3. If global location check is disabled, skip GPS completely
  if (settingsCheckEnabled === false) {
    console.log(`[GPS_TRACE] Geofence disabled: locationCheckEnabled is false in settings/classroom.`);
    return { passed: true, classroomSet: true, reason: 'Geofence disabled (locationCheckEnabled is false)' };
  }

  let location = null;
  let allowedRadius = 10;
  let geofenceDisabled = false;

  // 4. Determine coordinates/radius from session or global settings fallback
  if (session && session.location) {
    location = session.location;
    allowedRadius = (session.location_radius !== undefined && session.location_radius !== null) ? Number(session.location_radius) : 10;
    if (allowedRadius === 0) {
      geofenceDisabled = true;
    }
  } else {
    // No session location -> fallback to global classroom coordinates
    location = globalClassroomLocation;
    allowedRadius = globalClassroomRadius;
    if (allowedRadius === 0) {
      geofenceDisabled = true;
    }
  }

  // 5. Check if geofence is disabled or coordinates are completely missing
  if (geofenceDisabled || !location) {
    console.log(`[GPS_TRACE] Geofence disabled: geofenceDisabled=${geofenceDisabled}, location=${JSON.stringify(location)}`);
    return { passed: true, classroomSet: true, reason: 'Geofence disabled' };
  }

  console.log(`[GPS_TRACE] Geofence status: ENABLED`);
  console.log(`[GPS_TRACE] Session location coordinates: ${JSON.stringify(location)}`);
  console.log(`[GPS_TRACE] Allowed radius: ${allowedRadius}m`);

  // Log permission state before starting
  if (navigator.permissions && navigator.permissions.query) {
    try {
      const perm = await navigator.permissions.query({ name: 'geolocation' });
      console.log(`[GPS_TRACE] Permission state query: state=${perm.state}`);
    } catch (e) {
      console.warn(`[GPS_TRACE] Permission query error: ${e.message}`);
    }
  }

  return new Promise(resolve => {
    if (!navigator.geolocation) {
      console.log("[GPS_TRACE] navigator.geolocation is not supported/available");
      resolve({ passed: false, reason: 'Geolocation not supported on this device' });
      return;
    }

    let hasResolved = false;

    // Hard safety timeout: force resolve after 10 seconds
    const safetyTimer = setTimeout(() => {
      if (!hasResolved) {
        hasResolved = true;
        console.warn("[GPS_TRACE] SAFETY TIMEOUT TRIGGERED: Geolocation request hung for 10s. Forcing fallback success.");
        resolve({ passed: true, classroomSet: true, reason: 'Location check timed out (fallback)' });
      }
    }, 10000);

    let hasRetried = false;

    const attemptGPS = (highAccuracy) => {
      console.log(`[GPS_TRACE] GPS start: Before Geolocation request: highAccuracy=${highAccuracy}, timeout=8000ms`);
      navigator.geolocation.getCurrentPosition(
        pos => {
          if (hasResolved) return;
          hasResolved = true;
          clearTimeout(safetyTimer);
          
          console.log(`[GPS_TRACE] GPS success: Success Callback executed.`);
          console.log(`[GPS_TRACE] Student Latitude: ${pos.coords.latitude}`);
          console.log(`[GPS_TRACE] Student Longitude: ${pos.coords.longitude}`);
          console.log(`[GPS_TRACE] Student GPS Accuracy: ${pos.coords.accuracy}m`);

          // GPS Spoofing heuristic: unnaturally perfect accuracy
          if (pos.coords.accuracy <= 2.0 && !window.location.hostname.includes('localhost')) {
            console.warn("Suspicious GPS Accuracy:", pos.coords.accuracy);
          }

          const dist = haversineDistance(
            pos.coords.latitude, pos.coords.longitude,
            location.lat, location.lon
          );
          console.log(`[GPS_TRACE] Calculated Distance: ${dist}m`);

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
          if (hasResolved) return;

          console.error(`[GPS_TRACE] GPS error: Error Callback executed. Code: ${err.code}, Msg: ${err.message}`);
          
          // If it timed out and we haven't retried yet:
          if ((err.code === err.TIMEOUT || err.code === 3 || err.message.toLowerCase().includes('timeout')) && !hasRetried) {
            hasRetried = true;
            console.log(`[GPS_TRACE] Timeout occurred. Retrying once with highAccuracy=false fallback...`);
            attemptGPS(false);
          } else {
            hasResolved = true;
            clearTimeout(safetyTimer);
            let msg = err.message;
            if (err.code === 1) msg = "Location permission denied. Please enable GPS.";
            resolve({ passed: false, reason: `GPS error: ${msg}` });
          }
        },
        { enableHighAccuracy: highAccuracy, timeout: 8000, maximumAge: 0 }
      );
    };

    attemptGPS(true); // Initial attempt with high accuracy
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
    { href: 'dashboard.html',           icon: 'bar-chart-2',  label: 'Dashboard'  },
    { href: 'session.html',             icon: 'clock',         label: 'Session'    },
    { href: 'timetable.html',           icon: 'calendar',      label: 'Timetable'  },
    { href: 'live.html',                icon: 'video',         label: 'Attendance Monitor' },
    { href: 'students.html',            icon: 'users',         label: 'Students'   },
    { href: 'manual.html',              icon: 'edit-3',        label: 'Manual'     },
    { href: 'reports.html',             icon: 'folder',        label: 'Reports'    },
    { href: 'settings.html',            icon: 'settings',      label: 'Settings'   },
  ];

  const u = window.currentUser || {};
  const name = u.name || u.email || 'Teacher';
  const role = u.role === 'teacher' || u.role === 'admin' ? 'Teacher / Admin' : 'Student';
  const avatar = name[0].toUpperCase();

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
          <div class="avatar" style="width:36px;height:36px;background:var(--gradient-primary);border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;" id="sb-avatar">${avatar}</div>
          <div class="user-info"><h4 id="sb-name">${name}</h4><p id="sb-role">${role}</p></div>
        </div>
        <button class="btn-danger" style="width:100%;margin-top:12px;font-size:12px;" onclick="signOut()">
          <i data-lucide="log-out"></i> Sign Out
        </button>
      </div>
    </aside>`;
}

document.addEventListener('authReady', e => {
  const u = e.detail;
  console.log('[AUTH_DEBUG] Event listener for authReady caught user:', u);
  const av = document.getElementById('sb-avatar');
  const nm = document.getElementById('sb-name');
  const rl = document.getElementById('sb-role');
  console.log('[AUTH_DEBUG] Sidebar DOM element check - sb-avatar:', !!av, ', sb-name:', !!nm, ', sb-role:', !!rl);
  if (av) av.textContent = (u.name || u.email || '?')[0].toUpperCase();
  if (nm) nm.textContent = u.name || u.email;
  if (rl) rl.textContent = u.role === 'teacher' || u.role === 'admin' ? 'Teacher / Admin' : 'Student';

  // Cross-Platform Mobile Menu Toggle Injection
  if (u.role === 'teacher') {
    const topbarFirstDiv = document.querySelector('.topbar > div:first-child');
    if (topbarFirstDiv && !document.getElementById('mob-menu')) {
      // Auto-create menu toggle button
      const btn = document.createElement('button');
      btn.id = 'mob-menu';
      btn.className = 'mob-menu-btn';
      btn.setAttribute('type', 'button');
      btn.innerHTML = '<i data-lucide="menu" style="width:20px;height:20px;"></i>';
      
      // Auto-create backdrop overlay
      let overlay = document.getElementById('sidebar-overlay');
      if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'sidebar-overlay';
        overlay.className = 'sidebar-overlay';
        document.body.appendChild(overlay);
      }

      // Handle toggle logic
      btn.addEventListener('click', () => {
        const sidebar = document.querySelector('.sidebar');
        if (sidebar) {
          sidebar.classList.toggle('open');
          if (sidebar.classList.contains('open')) {
            overlay.classList.add('active');
          } else {
            overlay.classList.remove('active');
          }
        }
      });

      overlay.addEventListener('click', () => {
        const sidebar = document.querySelector('.sidebar');
        if (sidebar) sidebar.classList.remove('open');
        overlay.classList.remove('active');
      });

      topbarFirstDiv.insertBefore(btn, topbarFirstDiv.firstChild);
      
      // Re-run Lucide to render the menu icon
      if (window.lucide) {
        window.lucide.createIcons();
      }
      console.log('[Mobile Navigation] Injected successfully');
    }
  }
});
