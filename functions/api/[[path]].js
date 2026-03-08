const DEFAULT_BACKEND = "https://algobetsai.onrender.com";

function buildTargetUrl(requestUrl, envPath = "") {
  const incoming = new URL(requestUrl);
  const base = String(DEFAULT_BACKEND).replace(/\/+$/, "");
  const safePath = String(envPath || "").replace(/^\/+/, "");
  const target = new URL(`${base}/api/${safePath}`);
  target.search = incoming.search;
  return target.toString();
}

function copyHeaders(incomingHeaders) {
  const headers = new Headers(incomingHeaders);
  const blocked = [
    "host",
    "cf-connecting-ip",
    "cf-ipcountry",
    "cf-ray",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-real-ip",
  ];
  for (const key of blocked) headers.delete(key);
  return headers;
}

export async function onRequest(context) {
  const { request, params } = context;
  const method = request.method.toUpperCase();
  const path = String(params.path || "");
  const targetUrl = buildTargetUrl(request.url, path);
  const headers = copyHeaders(request.headers);
  const init = { method, headers, redirect: "follow" };

  if (!["GET", "HEAD"].includes(method)) {
    init.body = request.body;
  }

  const upstream = await fetch(targetUrl, init);
  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.set("x-proxy-backend", DEFAULT_BACKEND);
  responseHeaders.set("x-proxy-via", "cloudflare-pages-function");

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
