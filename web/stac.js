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

/**
 * Newest scenes between two YYYY-MM-DD dates, at most maxCloud % cloudy, either inside
 * bbox [w, s, e, n] or of one MGRS tile (e.g. "31UFS"), which is how dates are compared.
 */
export async function searchScenes({ bbox, tile, from, to, maxCloud }, signal) {
  const body = {
    collections: [COLLECTION],
    ...(tile ? {} : { bbox: bbox.map((v) => +v.toFixed(5)) }),
    datetime: `${from}T00:00:00Z/${to}T23:59:59Z`,
    query: { "eo:cloud_cover": { lte: maxCloud }, ...(tile ? { "grid:code": { eq: `MGRS-${tile}` } } : {}) },
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

/**
 * Scenes by product name, like the full app's name search. A full name matches exactly; a partial
 * one must include the sensing date and tile (S2B_MSIL2A_20250615T103629_N0511_R008_T31UFS), which
 * narrow the catalogue query, and the rest of the name is matched here. Earth Search ids
 * (S2B_T31UFS_20250615T103635_L2A) are accepted too.
 */
export async function searchByName(text, signal) {
  const name = text.trim().replace(/\.SAFE$/i, "").toUpperCase();
  if (/^S2[A-D]_T\d{2}[A-Z]{3}_\d{8}T\d{6}_L2A$/.test(name)) return [await getScene(name, signal)].filter(Boolean);
  if (/MSIL1C/.test(name)) throw new Error("Vresto Lite has Level-2A only. Level-1C products are in the full app");
  const day = /MSIL2A_(\d{4})(\d{2})(\d{2})/.exec(name);
  const tile = /_T(\d{2}[A-Z]{3})(?:_|$)/.exec(name);
  if (!day || !tile) throw new Error("Include at least the date and tile, for example S2B_MSIL2A_20250615T103629_N0511_R008_T31UFS");
  const date = `${day[1]}-${day[2]}-${day[3]}`;
  const page = await post({
    collections: [COLLECTION],
    datetime: `${date}T00:00:00Z/${date}T23:59:59Z`,
    query: { "grid:code": { eq: `MGRS-${tile[1]}` } },
    limit: LIMIT,
  }, signal);
  return (page.features || [])
    .map(toScene)
    .filter((s) => s && s.product.toUpperCase().replace(/\.SAFE$/, "").startsWith(name));
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
    product: p["s2:product_uri"] || item.id, // the CDSE product name, as the full app shows it
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
    files: Object.entries(a)
      .filter(([, v]) => (v.roles || []).some((r) => r === "data" || r === "visual") && https(v.href)?.endsWith(".tif"))
      .map(([k, v]) => ({ title: v.title || k, href: https(v.href) })),
  };
}
