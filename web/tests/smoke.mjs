// Browser smoke test for Vresto Lite, run at phone size in headless Chromium.
//
//   cd web/tests && npm install && npx playwright install --with-deps chromium && node smoke.mjs
//
// The page is served from web/ by a tiny local server. Earth Search, the basemaps and the imagery
// are answered from fixtures (a 512 px UTM COG and two STAC items), so the test needs no network
// apart from the pinned libraries on jsDelivr. VENDOR_DIR=<dir> serves those from a local folder
// laid out like the CDN (<dir>/maplibre-gl@5.24.0/dist/maplibre-gl.js, ...) for offline runs.

import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { chromium, devices } from "playwright";

const here = fileURLToPath(new URL(".", import.meta.url));
const webRoot = join(here, "..");
const cog = await readFile(join(here, "fixtures/TCI.tif"));
const search = JSON.parse(await readFile(join(here, "fixtures/search.json"), "utf8"));
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z/D/PwAG/gL+DHWJ3gAAAABJRU5ErkJggg==",
  "base64",
);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };

const server = createServer(async (req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^\/+/, "") || "index.html";
  try {
    const body = await readFile(join(webRoot, path));
    res.writeHead(200, { "Content-Type": TYPES[extname(path)] || "application/octet-stream" }).end(body);
  } catch {
    res.writeHead(404).end();
  }
}).listen(0, "127.0.0.1");
await new Promise((ok) => server.once("listening", ok));
const base = `http://127.0.0.1:${server.address().port}/`;

const browser = await chromium.launch(
  process.env.HTTPS_PROXY ? { proxy: { server: process.env.HTTPS_PROXY, bypass: "127.0.0.1,localhost" } } : {},
);
const context = await browser.newContext({ ...devices["iPhone 13"] });
const errors = [];
const searchBodies = [];
const consoleLines = [];

async function openPage(query) {
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() => window.addEventListener("unhandledrejection", (e) => console.error("UNHANDLED", e.reason?.stack || e.reason)));
  page.on("console", (m) => consoleLines.push(`${m.type()}: ${m.text()}`));
  page.on("requestfailed", (r) => consoleLines.push(`request failed: ${r.url()} ${r.failure()?.errorText}`));
  if (process.env.VENDOR_DIR) {
    await page.route("https://cdn.jsdelivr.net/npm/**", (r) =>
      r.fulfill({ path: join(process.env.VENDOR_DIR, r.request().url().split("/npm/")[1]) }));
  }
  await page.route("https://earth-search.aws.element84.com/**", (r) => {
    const url = r.request().url();
    if (url.endsWith("/search")) {
      searchBodies.push(JSON.parse(r.request().postData()));
      return r.fulfill({ json: search });
    }
    const id = decodeURIComponent(url.split("/items/")[1] || "");
    const item = search.features.find((f) => f.id === id);
    return item ? r.fulfill({ json: item }) : r.fulfill({ status: 404, json: {} });
  });
  await page.route("https://fixtures.test/TCI.tif", (r) => {
    const m = /bytes=(\d+)-(\d*)/.exec(r.request().headers().range || "");
    if (!m) return r.fulfill({ body: cog, contentType: "image/tiff" });
    const start = +m[1];
    const end = Math.min(m[2] ? +m[2] : cog.length - 1, cog.length - 1);
    return r.fulfill({
      status: 206,
      body: cog.subarray(start, end + 1),
      headers: { "Content-Range": `bytes ${start}-${end}/${cog.length}`, "Content-Type": "image/tiff" },
    });
  });
  await page.route(/fixtures\.test\/thumb|tile\.openstreetmap|arcgisonline/, (r) => r.fulfill({ body: PNG_1PX, contentType: "image/png" }));
  await page.goto(base + query);
  return page;
}

const sceneLoaded = (page) =>
  page.waitForFunction(() => {
    const { map } = window.vrestoLite;
    return map.getSource("scene") && map.isSourceLoaded("scene");
  }, null, { timeout: 30000 });
const openSheet = async (page) => {
  if ((await page.getAttribute("#handle", "aria-expanded")) === "false") await page.click("#handle");
};
const compareShown = (page) =>
  page.waitForFunction(() => !document.querySelector(".divider").hidden, null, { timeout: 30000 });

try {
  // 1. The COG renderer: reprojected pixels land where the fixture puts them, the collar is transparent.
  let page = await openPage("?ll=51.419,4.475&z=13&from=2025-06-01&to=2025-06-30&cloud=30");
  const pixels = await page.evaluate(async () => {
    const { renderTile } = await import("./cog.js");
    const toLonLat = proj4("+proj=utm +zone=31 +datum=WGS84 +units=m +no_defs", "EPSG:4326").forward;
    const z = 15;
    const sample = async (ux, uy) => {
      const [lon, lat] = toLonLat([ux, uy]);
      const n = 2 ** z;
      const fx = ((lon + 180) / 360) * n;
      const fy = ((1 - Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) / Math.PI) / 2) * n;
      const png = await renderTile("https://fixtures.test/TCI.tif", 32631, z, Math.floor(fx), Math.floor(fy));
      const bitmap = await createImageBitmap(new Blob([png]));
      const ctx = new OffscreenCanvas(256, 256).getContext("2d");
      ctx.drawImage(bitmap, 0, 0);
      return [...ctx.getImageData(Math.floor((fx % 1) * 256), Math.floor((fy % 1) * 256), 1, 1).data];
    };
    return {
      green: await sample(601500, 5697000),
      tan: await sample(604000, 5697000),
      collar: await sample(600100, 5697000),
      outside: await sample(599000, 5697000),
    };
  });
  assert.deepEqual(pixels.green, [40, 120, 40, 255], "left half colour");
  assert.deepEqual(pixels.tan, [200, 180, 120, 255], "right half colour");
  assert.equal(pixels.collar[3], 0, "black collar is transparent");
  assert.equal(pixels.outside[3], 0, "outside the scene is transparent");

  // 2. Search fills the list.
  await page.waitForSelector(".scene").catch(async (e) => {
    console.error("state:", await page.evaluate(() => {
      const { map } = window.vrestoLite;
      return { loaded: map.loaded(), style: map.isStyleLoaded(), summary: document.getElementById("summary").textContent,
        status: document.getElementById("status").textContent, basemapBtn: document.getElementById("basemap").textContent };
    }));
    throw e;
  });
  assert.equal(await page.locator(".scene").count(), 2);
  assert.match(await page.textContent("#summary"), /^2 scenes/);

  // 3. Tapping a scene streams it and offers its band files.
  await page.locator(".scene").first().click();
  await sceneLoaded(page);
  assert.match(page.url(), /scene=S2A_T31UFS_20250630T104040_L2A/);
  assert.equal(await page.locator(".files a").count(), 3);
  assert.equal(await page.textContent(".details .product code"), "S2A_MSIL2A_20250630T104041_N0511_R008_T31UFS_20250630T122714.SAFE");

  // 4. Compare: pick a second date, the divider appears, and the link carries both scenes.
  await page.click("#compare");
  await page.waitForFunction(() => document.getElementById("status").textContent.startsWith("Tap another date"));
  assert.match(await page.textContent("#summary"), /^1 other date of 31UFS/);
  assert.deepEqual(searchBodies.at(-1).query["grid:code"], { eq: "MGRS-31UFS" }, "compare lists the same tile only");
  await page.locator(".scene").nth(1).click();
  await compareShown(page);
  assert.match(page.url(), /compare=S2A_T31UFS_20250613/);
  const shared = page.url().slice(base.length);
  await page.close();

  // 5. The shared link reopens the same comparison.
  page = await openPage(shared);
  await sceneLoaded(page);
  await compareShown(page);
  assert.match(await page.textContent("#showing-label"), /⇆/);

  // 6. Name search: the start of a name up to the tile finds it and shows it.
  await openSheet(page);
  await page.click('[data-tab="name"]');
  await page.fill("#name", "S2A_MSIL2A_20250613T104041_N0511_R008_T31UFS");
  await page.press("#name", "Enter");
  await page.waitForFunction(() => document.getElementById("summary").textContent.startsWith("1 product"));
  assert.match(page.url(), /scene=S2A_T31UFS_20250613T104040_L2A/);
  await openSheet(page);
  await page.fill("#name", "S2A_MSIL1C_20250613T104041_N0511_R008_T31UFS");
  await page.press("#name", "Enter");
  await page.waitForFunction(() => document.getElementById("status").textContent.includes("Level-2A only"));

  // 7. The layer picker previews the full app's overlays and links to the same view there.
  await page.click("#layers");
  await page.click("#overlay-list button >> text=NDVI climatology");
  assert.equal(await page.isVisible('[data-panel="full"]'), true);
  const full = await page.getAttribute("#open-full", "href");
  assert.match(full, /^http:\/\/localhost:8610\/\?ll=.*&tile=31UFS&date=20250613&overlay=ndvi$/);

  assert.deepEqual(errors, [], "no uncaught page errors");
  console.log("Vresto Lite smoke test passed");
} catch (err) {
  console.error("Page errors:", errors);
  console.error(consoleLines.filter((l) => !l.includes("GL Driver")).slice(-40).join("\n"));
  throw err;
} finally {
  await browser.close();
  server.close();
}
