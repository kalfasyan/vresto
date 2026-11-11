// Renders a Sentinel-2 true-colour COG as web-map tiles, entirely in the browser.
//
// Sentinel-2 files are in UTM, and MapLibre draws Web Mercator tiles, so each tile is
// reprojected here: the tile's corners are mapped into UTM, the matching window is read from
// the COG overview closest to the tile's resolution (only those bytes are fetched), and every
// output pixel is sampled from it. Black pixels are the scene's no-data collar and are left
// transparent.
//
// Usage: registerCogProtocol(maplibregl), then a raster source with
// tiles: [cogTileUrl(href, epsg)] and tileSize: 256.

const TILE = 256;
const GRID = 16; // UTM coordinates are computed exactly on a 17x17 grid and interpolated between.
const R = 6378137;
const HALF = Math.PI * R;

const cogs = new Map(); // href -> Promise<{ images, origin, res }>
let emptyTile = null;
let pool = null; // decoder workers, so tile decoding never blocks scrolling and taps

function openCog(href) {
  if (!cogs.has(href)) {
    const opened = (async () => {
      const tiff = await GeoTIFF.fromUrl(href, { allowFullFile: false });
      const count = await tiff.getImageCount();
      const images = await Promise.all([...Array(count).keys()].map((i) => tiff.getImage(i)));
      const base = images[0];
      const [ox, oy] = base.getOrigin();
      const baseRes = base.getResolution()[0];
      // Overviews carry no georeferencing of their own; derive it from the full-resolution image.
      const levels = images
        .map((image) => ({ image, res: (baseRes * base.getWidth()) / image.getWidth() }))
        .sort((a, b) => a.res - b.res);
      return { levels, ox, oy, width: base.getWidth() * baseRes, height: base.getHeight() * baseRes };
    })();
    opened.catch(() => cogs.delete(href)); // let a later tile retry a failed open
    cogs.set(href, opened);
  }
  return cogs.get(href);
}

function utmDef(epsg) {
  const zone = epsg % 100;
  const south = Math.floor(epsg / 100) === 327 ? " +south" : "";
  return `+proj=utm +zone=${zone}${south} +datum=WGS84 +units=m +no_defs`;
}

async function encode(rgba) {
  const canvas = new OffscreenCanvas(TILE, TILE);
  if (rgba) canvas.getContext("2d").putImageData(new ImageData(rgba, TILE, TILE), 0, 0);
  const blob = await canvas.convertToBlob({ type: "image/png" });
  return blob.arrayBuffer();
}

async function blank() {
  emptyTile ??= encode(null);
  return (await emptyTile).slice(0);
}

async function renderTile(href, epsg, z, x, y, signal) {
  const cog = await openCog(href);
  const toUtm = proj4("EPSG:4326", utmDef(epsg)).forward;

  // UTM coordinates of a (GRID+1)^2 lattice over the tile.
  const size = (2 * HALF) / 2 ** z;
  const left = -HALF + x * size;
  const top = HALF - y * size;
  const n = GRID + 1;
  const gx = new Float64Array(n * n);
  const gy = new Float64Array(n * n);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      const mx = left + (i / GRID) * size;
      const my = top - (j / GRID) * size;
      const lon = (mx / R) * (180 / Math.PI);
      const lat = (2 * Math.atan(Math.exp(my / R)) - Math.PI / 2) * (180 / Math.PI);
      const [ux, uy] = toUtm([lon, lat]);
      const k = j * n + i;
      gx[k] = ux;
      gy[k] = uy;
      if (ux < minX) minX = ux;
      if (ux > maxX) maxX = ux;
      if (uy < minY) minY = uy;
      if (uy > maxY) maxY = uy;
    }
  }

  const x0 = cog.ox, y1 = cog.oy, x1 = cog.ox + cog.width, y0 = cog.oy - cog.height;
  if (maxX <= x0 || minX >= x1 || maxY <= y0 || minY >= y1) return blank();

  // Coarsest overview that is still at least as sharp as the tile.
  const wanted = (maxX - minX) / TILE;
  let level = cog.levels[0];
  for (const candidate of cog.levels) if (candidate.res <= wanted) level = candidate;
  const { image, res } = level;

  const px0 = Math.max(0, Math.floor((minX - x0) / res));
  const py0 = Math.max(0, Math.floor((y1 - maxY) / res));
  const px1 = Math.min(image.getWidth(), Math.ceil((maxX - x0) / res));
  const py1 = Math.min(image.getHeight(), Math.ceil((y1 - minY) / res));
  if (px1 <= px0 || py1 <= py0) return blank();

  const data = await image.readRasters({
    window: [px0, py0, px1, py1],
    samples: [0, 1, 2],
    interleave: true,
    pool: (pool ??= new GeoTIFF.Pool()),
    signal,
  });
  const ww = px1 - px0, wh = py1 - py0;

  const out = new Uint8ClampedArray(TILE * TILE * 4);
  const step = TILE / GRID;
  for (let py = 0; py < TILE; py++) {
    const fj = (py + 0.5) / step;
    const j = Math.min(GRID - 1, Math.floor(fj));
    const tj = fj - j;
    for (let px = 0; px < TILE; px++) {
      const fi = (px + 0.5) / step;
      const i = Math.min(GRID - 1, Math.floor(fi));
      const ti = fi - i;
      const a = j * n + i, b = a + 1, c = a + n, d = c + 1;
      const ux = (gx[a] * (1 - ti) + gx[b] * ti) * (1 - tj) + (gx[c] * (1 - ti) + gx[d] * ti) * tj;
      const uy = (gy[a] * (1 - ti) + gy[b] * ti) * (1 - tj) + (gy[c] * (1 - ti) + gy[d] * ti) * tj;
      const sx = Math.floor((ux - x0) / res) - px0;
      const sy = Math.floor((y1 - uy) / res) - py0;
      if (sx < 0 || sy < 0 || sx >= ww || sy >= wh) continue;
      const s = (sy * ww + sx) * 3;
      const r = data[s], g = data[s + 1], bl = data[s + 2];
      if (r === 0 && g === 0 && bl === 0) continue;
      const o = (py * TILE + px) * 4;
      out[o] = r;
      out[o + 1] = g;
      out[o + 2] = bl;
      out[o + 3] = 255;
    }
  }
  return encode(out);
}

export function cogTileUrl(href, epsg) {
  return `s2cog://${epsg}/{z}/{x}/{y}/${encodeURIComponent(href)}`;
}

export function registerCogProtocol(maplibregl) {
  maplibregl.addProtocol("s2cog", async (params, abortController) => {
    const [epsg, z, x, y, href] = params.url.slice("s2cog://".length).split("/");
    const data = await renderTile(decodeURIComponent(href), +epsg, +z, +x, +y, abortController.signal);
    return { data };
  });
}
