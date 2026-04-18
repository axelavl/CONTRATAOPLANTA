/* logo-fallback.js — override de imgFavFallback con búsqueda exhaustiva.
   Cuando Clearbit o cualquier source devuelve 404, prueba en orden:
   1. Clearbit (size 128)
   2. DuckDuckGo icons (ip3)
   3. Google favicons (sz=128)
   4. /apple-touch-icon.png del propio dominio de la institución
   5. /favicon.ico del propio dominio
   Solo cae al emoji/texto placeholder si todas las 5 fallan.
*/
(function () {
  'use strict';

  function domainFromImg(img) {
    // Guardamos el dominio en data-domain la primera vez que vemos el img
    if (img.dataset.domain) return img.dataset.domain;
    var src = img.src || '';
    var m =
      src.match(/logo\.clearbit\.com\/([^?#/]+)/) ||
      src.match(/duckduckgo\.com\/ip3\/([^.]+\.[^/]+)\.ico/) ||
      src.match(/google\.com\/s2\/favicons.*domain=([^&]+)/) ||
      src.match(/^https?:\/\/([^/]+)\/(?:favicon\.ico|apple-touch-icon\.png)/);
    var domain = m ? m[1] : '';
    if (domain) img.dataset.domain = domain;
    return domain;
  }

  function sourcesFor(domain) {
    return [
      'https://logo.clearbit.com/' + encodeURIComponent(domain) + '?size=128',
      'https://icons.duckduckgo.com/ip3/' + encodeURIComponent(domain) + '.ico',
      'https://www.google.com/s2/favicons?domain=' + encodeURIComponent(domain) + '&sz=128',
      'https://' + domain + '/apple-touch-icon.png',
      'https://' + domain + '/favicon.ico',
    ];
  }

  function replaceWithEmoji(img) {
    var fallback = img.dataset.fallback || '🏢';
    var span = document.createElement('span');
    span.style.fontSize = '20px';
    span.textContent = fallback;
    img.replaceWith(span);
  }

  // Sobrescribe la función global definida en index.html
  window.imgFavFallback = function (img) {
    var domain = domainFromImg(img);
    if (!domain) {
      replaceWithEmoji(img);
      return;
    }

    var sources = sourcesFor(domain);
    var tried = parseInt(img.dataset.attempt || '0', 10);
    // Saltarse el primero (ya se intentó con el src inicial)
    var nextIdx = tried + 1;
    if (nextIdx >= sources.length) {
      replaceWithEmoji(img);
      return;
    }
    img.dataset.attempt = String(nextIdx);
    img.src = sources[nextIdx];
  };
})();
