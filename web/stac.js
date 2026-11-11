// Sentinel-2 L2A search on Element84 Earth Search, the public STAC catalogue the Python app's
// no-account mode also uses. No key or account is needed.

export const STAC_URL = "https://earth-search.aws.element84.com/v1";
export const COLLECTION = "sentinel-2-c1-l2a";
const LIMIT = 50;

async function post(body, signal) {
  const res = await fetch(`${STAC_URL}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const err = new Error(`Search failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/** Newest scenes inside bbox [w, s, e, n] between two YYYY-MM-DD dates, at most maxCloud % cloudy. */
export async function searchScenes({ bbox, from, to, maxCloud }, signal) {
  const body = {
    collections: [COLLECTION],
    bbox: bbox.map((v) => +v.toFixed(5)),
    datetime: `${from}T00:00:00Z/${to}T23:59:59Z`,
    query: { "eo:cloud_cover": { lte: maxCloud } },
    limit: LIMIT,
    sortby: [{ field: "properties.datetime", direction: "desc" }],
  };
  let page;
  try {
    page = await post(body, signal);
  } catch (err) {
    if (!(err.status >= 400 && err.status < 500)) throw err;
    delete body.sortby; // a server that refuses sortby still answers; sort here instead
    page = await post(body, signal);
  }
  const scenes = (page.features || []).map(toScene).filter(Boolean);
  scenes.sort((a, b) => b.datetime.localeCompare(a.datetime));
  return { scenes, more: Boolean((page.links || []).find((l) => l.rel === "next")) };
}

/** One scene by its STAC id, for links that name a scene. */
export async function getScene(id, signal) {
  const res = await fetch(`${STAC_URL}/collections/${COLLECTION}/items/${encodeURIComponent(id)}`, { signal });
  if (!res.ok) throw new Error(`Scene ${id} not found (${res.status})`);
  return toScene(await res.json());
}

function https(href) {
  if (typeof href !== "string") return null;
  if (href.startsWith("s3://")) {
    const [bucket, ...key] = href.slice(5).split("/");
    return `https://${bucket}.s3.amazonaws.com/${key.join("/")}`;
  }
  return href.startsWith("https://") ? href : null;
}

function toScene(item) {
  const p = item.properties || {};
  const a = item.assets || {};
  const visual = https(a.visual?.href);
  const epsg = p["proj:epsg"] ?? Number(String(p["proj:code"] || "").split(":")[1]);
  if (!visual || !epsg) return null;
  const tile = (p["grid:code"] || "").replace("MGRS-", "");
  return {
    id: item.id,
    datetime: p.datetime || "",
    date: (p.datetime || "").slice(0, 10),
    cloud: typeof p["eo:cloud_cover"] === "number" ? p["eo:cloud_cover"] : null,
    tile,
    platform: (p.platform || "").replace("sentinel-", "S").toUpperCase(),
    epsg,
    bbox: item.bbox,
    geometry: item.geometry,
    visual,
    thumbnail: https(a.thumbnail?.href),
  };
}
