// Full screen and kiosk mode for the dashboard, the office and the statistics, e.g. on a tablet on the wall.
//
// - The "⛶ Full screen" button (or the F key) fills the screen where the browser allows it.
// - Kiosk mode (full screen, a page opened with ?kiosk, or the dashboard added to the home screen):
//   the screen stays on (Wake Lock), the pointer hides when unused, links that leave the view are hidden,
//   and the page reloads itself once a night so a browser that runs for weeks stays fresh.
// - Added to the home screen ("Install app" / "Add to Home Screen") it opens full screen on the page that
//   was last shown in kiosk mode.
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch { /* storage blocked */ } },
  };
  const standalone = matchMedia('(display-mode: fullscreen), (display-mode: standalone)').matches || navigator.standalone === true;

  // Started from the home screen: go on to the page used last.
  if (params.has('app')) {
    const last = store.get('kioskPage');
    if (last && last !== location.pathname && /^\/(dashboard|stats)?$/.test(last)) { location.replace(last + '?kiosk'); return; }
  }
  if (params.has('kiosk')) store.set('kiosk', params.get('kiosk') === '0' ? null : '1');

  const el = document.documentElement;
  const canFull = !!(el.requestFullscreen || el.webkitRequestFullscreen);
  const isFull = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
  const kioskWanted = () => isFull() || standalone || store.get('kiosk') === '1';

  const btn = document.getElementById('fs');
  function enter() {
    store.set('kioskPage', location.pathname);
    const req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (req) Promise.resolve(req.call(el, { navigationUI: 'hide' })).catch(() => { store.set('kiosk', '1'); apply(); });
    else { store.set('kiosk', '1'); apply(); }  // iPhone Safari: no full screen API, kiosk mode at least
  }
  function leave() {
    store.set('kiosk', null);
    if (isFull()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    apply();
  }
  if (btn) {
    btn.onclick = () => (kioskWanted() ? leave() : enter());
    if (!canFull) btn.title = 'Kiosk mode (for full screen: add this page to the home screen)';
  }
  addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() !== 'f' || e.ctrlKey || e.metaKey || e.altKey || /input|textarea|select/i.test(e.target.tagName)) return;
    kioskWanted() ? leave() : enter();
  });

  // keep the screen on
  let lock = null;
  async function wake() {
    if (!('wakeLock' in navigator) || lock || document.hidden || !kioskWanted()) return;
    try { lock = await navigator.wakeLock.request('screen'); lock.addEventListener('release', () => { lock = null; }); } catch { lock = null; }
  }
  document.addEventListener('visibilitychange', wake);

  // hide the pointer after a few seconds without moving it
  let idleT;
  function poke() {
    document.body.classList.remove('idle');
    clearTimeout(idleT);
    if (kioskWanted()) idleT = setTimeout(() => document.body.classList.add('idle'), 3000);
  }
  addEventListener('pointermove', poke, { passive: true });
  addEventListener('pointerdown', poke, { passive: true });

  function apply() {
    const on = kioskWanted();
    document.body.classList.toggle('kiosk', on);
    if (btn) {
      btn.textContent = on ? '✕ Exit full screen' : '⛶ Full screen';
      btn.setAttribute('aria-pressed', String(on));
    }
    if (on) { store.set('kioskPage', location.pathname); wake(); } else if (lock) { lock.release().catch(() => {}); lock = null; }
    poke();
  }
  document.addEventListener('fullscreenchange', apply);
  document.addEventListener('webkitfullscreenchange', apply);

  // a fresh start every night at about four, but only in kiosk mode and only if the server answers
  const reloadAt = new Date();
  reloadAt.setHours(4, Math.floor(Math.random() * 30), 0, 0);
  if (reloadAt <= new Date()) reloadAt.setDate(reloadAt.getDate() + 1);
  setInterval(async () => {
    if (!kioskWanted() || Date.now() < reloadAt.getTime()) return;
    try { if ((await fetch('/healthz', { cache: 'no-store' })).ok) location.reload(); } catch { /* try again in a minute */ }
  }, 60000);

  if (document.body) apply(); else addEventListener('DOMContentLoaded', apply);
})();
