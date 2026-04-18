/* ribbon-data.js — llena el ribbon reusando los datos que ya carga la home.
   Estrategia:
   1. Si en la página existen los elementos #hs-activas/#hs-instituciones/
      #data-last-update (home), copia sus valores al ribbon cuando cambian.
   2. Si NO existen (páginas internas), hace su propio fetch al API.
   Así evitamos una doble request y nos aseguramos que el ribbon siempre
   refleje la misma fuente de verdad que el resto del sitio.
*/
(function () {
  'use strict';

  var RAILWAY_BACKEND = 'https://contrataoplanta-production.up.railway.app';

  function apiBase() {
    if (window.__API_BASE) return window.__API_BASE;
    if (window.API_BASE) return window.API_BASE;
    return RAILWAY_BACKEND;
  }

  function fmt(n) {
    try { return Number(n || 0).toLocaleString('es-CL'); } catch (e) { return String(n); }
  }

  function timeAgoFromIso(isoDate) {
    if (!isoDate) return null;
    var fecha = new Date(isoDate);
    if (isNaN(fecha.getTime())) return null;
    var minutos = Math.max(0, Math.floor((Date.now() - fecha.getTime()) / 60000));
    if (minutos < 1) return 'ahora';
    if (minutos < 60) return minutos + ' min';
    if (minutos < 1440) return Math.floor(minutos / 60) + ' h';
    return Math.floor(minutos / 1440) + ' d';
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el && value != null && value !== '') {
      el.textContent = value;
    }
  }

  // 1) Intentar copiar desde elementos ya llenos en la home
  function copyFromDom() {
    var hsA = document.getElementById('hs-activas');
    var hsI = document.getElementById('hs-instituciones');
    var lu = document.getElementById('data-last-update');
    var countSub = document.getElementById('count-sub');

    if (hsA && hsA.textContent.trim() && hsA.textContent.trim() !== '—') {
      setText('ribbon-vigentes', hsA.textContent.trim());
    }
    if (hsI && hsI.textContent.trim() && hsI.textContent.trim() !== '—') {
      setText('ribbon-instituciones', hsI.textContent.trim());
    }

    // Tiempo: preferimos #count-sub porque ya viene formateado "hace X min"
    if (countSub && countSub.textContent) {
      var m = countSub.textContent.match(/hace\s+([^·]+)/i);
      if (m && m[1]) {
        setText('ribbon-actualizado', m[1].trim());
      }
    } else if (lu && lu.textContent && !/cargando|disponible/i.test(lu.textContent)) {
      // Fallback: intentar parsear si data-last-update tiene una fecha
      var parsed = new Date(lu.textContent);
      if (!isNaN(parsed.getTime())) {
        var ago = timeAgoFromIso(parsed.toISOString());
        if (ago) setText('ribbon-actualizado', ago);
      }
    }
  }

  // 2) Fallback: fetch directo al API (para páginas que no llenan #hs-*)
  async function fetchAndFill() {
    try {
      var resp = await fetch(apiBase() + '/api/estadisticas', {
        signal: (window.AbortSignal && AbortSignal.timeout) ? AbortSignal.timeout(8000) : undefined
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      var data = await resp.json();

      var ago = timeAgoFromIso(data.ultima_actualizacion);
      if (ago) setText('ribbon-actualizado', ago);
      if (data.instituciones_activas != null) setText('ribbon-instituciones', fmt(data.instituciones_activas));
      if (data.activas_hoy != null) setText('ribbon-vigentes', fmt(data.activas_hoy));
      if (data.cierran_hoy != null) setText('ribbon-cierran', fmt(data.cierran_hoy));
    } catch (err) {
      if (window.console && console.warn) console.warn('ribbon-data: fetch falló', err);
    }
  }

  function init() {
    if (!document.querySelector('.ribbon')) return;

    // Intento inmediato por si el DOM ya está poblado
    copyFromDom();

    // Observar cambios en los slots que la home llena dinámicamente
    var targets = ['hs-activas', 'hs-instituciones', 'data-last-update', 'count-sub']
      .map(function (id) { return document.getElementById(id); })
      .filter(Boolean);

    if (targets.length) {
      var obs = new MutationObserver(copyFromDom);
      targets.forEach(function (el) {
        obs.observe(el, { childList: true, characterData: true, subtree: true });
      });
      // Dejar de escuchar después de 15 seg (ya debería haber cargado)
      setTimeout(function () { obs.disconnect(); }, 15000);
    } else {
      // Página sin los elementos típicos de stats — hacemos fetch directo.
      fetchAndFill();
    }

    // Siempre hacer fetch para "cierran hoy" que no está en el DOM de la home
    // (lo dejamos en segundo plano; si falla, el "—" queda)
    fetchCierranHoy();
  }

  async function fetchCierranHoy() {
    var el = document.getElementById('ribbon-cierran');
    if (!el || (el.textContent.trim() && el.textContent.trim() !== '—')) return;
    try {
      var resp = await fetch(apiBase() + '/api/estadisticas', {
        signal: (window.AbortSignal && AbortSignal.timeout) ? AbortSignal.timeout(8000) : undefined
      });
      if (!resp.ok) return;
      var data = await resp.json();
      if (data.cierran_hoy != null) setText('ribbon-cierran', fmt(data.cierran_hoy));

      // Bonus: también llenar #data-last-update si dice "no disponible"
      var lu = document.getElementById('data-last-update');
      if (lu && /no disponible|cargando/i.test(lu.textContent) && data.ultima_actualizacion) {
        var fecha = new Date(data.ultima_actualizacion);
        if (!isNaN(fecha.getTime())) {
          lu.textContent = fecha.toLocaleString('es-CL', { dateStyle: 'medium', timeStyle: 'short' });
        }
      }
    } catch (e) {}
  }

  document.addEventListener('shell:ready', function () {
    // shell:ready se dispara después de que los partials se montan
    setTimeout(init, 50);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(init, 50); });
  } else {
    setTimeout(init, 100);
  }
})();
