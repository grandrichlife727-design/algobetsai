import { evFinderRows, getScanPayload } from "../_lib/odds-engine.js";
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
  const { request, params, env } = context;
  const method = request.method.toUpperCase();
  const rawPath = (Array.isArray(params.path) ? params.path.join("/") : String(params.path || "")).replace(/,/g, "/");
  const cleanPath = rawPath.replace(/^\/+/, "");

  if (method === "GET" && cleanPath === "ev-finder") {
    try {
      const payload = await getScanPayload(env, { force: false });
      const rows = evFinderRows(payload);
      return new Response(JSON.stringify({ results: rows, count: rows.length, data_source: "odds_api" }), {
        status: 200,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store",
          "x-algobets-origin": "cloudflare-ev-finder",
        },
      });
    } catch (err) {
      return new Response(
        JSON.stringify({ detail: String(err?.message || err || "EV finder unavailable"), results: [], count: 0 }),
        {
          status: 503,
          headers: {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-algobets-origin": "cloudflare-ev-finder",
          },
        },
      );
    }
  }

  const url = toUpstreamUrl(request.url, rawPath);
  const hasApiKey = !!String(request.headers.get("x-api-key") || "").trim();
  const init = {
    method,
    headers: forwardHeaders(request),
    redirect: "follow",
  };
  const proxyApiKey = String(env?.BACKEND_API_KEY || "").trim();
  // This function only handles /api/* routes in Pages, so always inject when missing.
  if (!hasApiKey && proxyApiKey) {
    init.headers.set("x-api-key", proxyApiKey);
  }
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
