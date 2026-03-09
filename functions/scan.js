import { getScanPayload } from "./_lib/odds-engine.js";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-algobets-origin": "cloudflare-scan",
    },
  });
}

export async function onRequestGet(context) {
  try {
    const url = new URL(context.request.url);
    const force = String(url.searchParams.get("refresh") || "").toLowerCase() === "true";
    const payload = await getScanPayload(context.env, { force });
    return json(payload, 200);
  } catch (err) {
    return json(
      {
        error: "scan_unavailable",
        detail: String(err?.message || err || "Unknown error"),
        picks: [],
        picks_total: 0,
        games: [],
        games_total: 0,
        debug_has_api_key: false,
      },
      503,
    );
  }
}
