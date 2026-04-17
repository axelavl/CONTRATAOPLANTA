// Cloudflare Pages Function: proxy same-origin de /api/* al backend FastAPI.
//
// Motivo:
// El sitio se publica también en hosts estáticos (estadoemplea.pages.dev,
// contrataoplanta.pages.dev). Allí no hay nginx que haga reverse-proxy, así
// que cualquier fetch a `/api/...` terminaría sirviendo HTML 404 y la cadena
// de fallbacks absolutos dispara "Failed to fetch" por CORS o DNS.
// Esta función convierte el llamado en same-origin y reenvía al backend.
//
// UPSTREAM apunta al subdominio `api.contrataoplanta.cl` — que en producción
// entra directo al nginx del VPS (→ FastAPI). Antes apuntaba al apex
// `contrataoplanta.cl`: si ese dominio también estuviera detrás de Cloudflare
// Pages como dominio custom, el proxy se llamaría a sí mismo y quedaba en
// loop hasta timeout. La variable `API_UPSTREAM` permite sobreescribir desde
// el dashboard de Pages sin tocar el código.

const DEFAULT_UPSTREAM = 'https://api.contrataoplanta.cl';
// Timeout del fetch al upstream. Si el backend está caído preferimos cortar
// rápido y que el frontend continúe con su cadena de fallbacks en vez de
// hacer esperar al usuario 30+ segundos antes de ver el error.
const UPSTREAM_TIMEOUT_MS = 15000;

// Headers inyectados por Cloudflare o por el host estático que no deben viajar
// al upstream: al reenviarlos rompen el routing o la terminación TLS.
const STRIP_REQUEST_HEADERS = [
  'host',
  'cf-connecting-ip',
  'cf-ray',
  'cf-visitor',
  'cf-ipcountry',
  'cf-ew-via',
  'x-forwarded-for',
  'x-forwarded-host',
  'x-forwarded-proto',
  'x-real-ip',
];

export async function onRequest({ request, env }) {
  const upstreamBase = ((env && env.API_UPSTREAM) || DEFAULT_UPSTREAM).replace(/\/+$/, '');
  const incoming = new URL(request.url);
  const target = upstreamBase + incoming.pathname + incoming.search;

  const headers = new Headers(request.headers);
  for (const name of STRIP_REQUEST_HEADERS) headers.delete(name);

  const init = {
    method: request.method,
    headers,
    redirect: 'follow',
  };
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    init.body = request.body;
    // El runtime de Workers exige `duplex: 'half'` cuando el body es un
    // ReadableStream, sino el fetch falla silenciosamente en POST/PUT.
    init.duplex = 'half';
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  init.signal = controller.signal;

  try {
    const upstream = await fetch(target, init);
    clearTimeout(timeoutId);
    // El runtime de Cloudflare puede descomprimir el cuerpo aguas arriba; en
    // ese caso reenviar `content-encoding`/`content-length` hace que el browser
    // intente descomprimir bytes ya expandidos y aborte la respuesta.
    const respHeaders = new Headers(upstream.headers);
    respHeaders.delete('content-encoding');
    respHeaders.delete('content-length');
    // Marca la respuesta como proveniente del proxy, útil para que el cliente
    // distinga un 5xx del backend real vs. un 5xx sintetizado por el proxy.
    respHeaders.set('x-proxied-by', 'contrataoplanta-pages-fn');
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: respHeaders,
    });
  } catch (err) {
    clearTimeout(timeoutId);
    const aborted = err && (err.name === 'AbortError' || err.name === 'TimeoutError');
    const detail = aborted
      ? `Timeout tras ${UPSTREAM_TIMEOUT_MS}ms`
      : (err && err.message ? err.message : String(err));
    return new Response(
      JSON.stringify({
        error: 'backend_unreachable',
        upstream: target,
        detail,
      }),
      {
        status: 502,
        headers: {
          'content-type': 'application/json; charset=utf-8',
          'x-proxied-by': 'contrataoplanta-pages-fn',
          'x-proxy-error': 'backend_unreachable',
        },
      },
    );
  }
}
