const UPSTREAM = "https://algobetsai.onrender.com";

function toUpstreamUrl(requestUrl, pathParam) {
  const source = new URL(requestUrl);
  const raw = (Array.isArray(pathParam) ? pathParam.join("/") : String(pathParam || "")).replace(/,/g, "/");
  const path = raw.replace(/^\/+/, "");
  const normalized = path.startsWith("api/") ? path : `api/${path}`;
  const target = new URL(`${UPSTREAM}/${normalized}`);
  target.search = source.search;
  return target.toString();
}

function forwardHeaders(request) {
  const headers = new Headers(request.headers);
  const drop = [
    "host",
    "origin",
    "cf-connecting-ip",
    "cf-ipcountry",
    "cf-ray",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
  ];
  for (const h of drop) headers.delete(h);
  return headers;
}

export async function onRequest(context) {
  const { request, params } = context;
  const method = request.method.toUpperCase();
  const rawPath = (Array.isArray(params.path) ? params.path.join("/") : String(params.path || "")).replace(/,/g, "/");
  const url = toUpstreamUrl(request.url, rawPath);
  const init = {
    method,
    headers: forwardHeaders(request),
    redirect: "follow",
  };
  if (!["GET", "HEAD"].includes(method)) init.body = request.body;

  const upstream = await fetch(url, init);
  const outHeaders = new Headers(upstream.headers);
  outHeaders.set("x-algobets-proxy", "cloudflare-pages");
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: outHeaders,
  });
}
