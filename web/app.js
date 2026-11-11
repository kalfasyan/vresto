// Vresto Lite: search Sentinel-2 scenes in the map view and stream them onto the map.
// Static page, no server: the catalogue search and the imagery both come straight from AWS Open Data.

import { cogTileUrl, registerCogProtocol } from "./cog.js";
import { getScene, searchScenes } from "./stac.js";

const $ = (id) => document.getElementById(id);
const els = {
  place: $("place"), placeForm: $("place-form"), basemap: $("basemap"),
  searchHere: $("search-here"), showing: $("showing"), showingLabel: $("showing-label"), clear: $("clear-scene"),
  sheet: $("sheet"), handle: $("handle"), summary: $("summary"),
  from: $("from"), to: $("to"), cloud: $("cloud"), cloudOut: $("cloud-out"),
  status: $("status"), results: $("results"),
};

const BASEMAPS = {
  map: { label: "Map", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], maxzoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' },
  satellite: { label: "Satellite", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], maxzoom: 19,
    attribution: "Basemap &copy; Esri, Maxar, Earthstar Geographics" },
};
const IMAGERY_ATTRIBUTION = "Contains modified Copernicus Sentinel data, via Element84 Earth Search";

// ---------- state, read from and written back to the URL ----------

const isoDay = (d) => d.toISOString().slice(0, 10);
const validDay = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || "") && !isNaN(Date.parse(s));

function readUrl() {
  const q = new URLSearchParams(location.search);
  const today = new Date();
  const monthAgo = new Date(today.getTime() - 30 * 864e5);
  const [lat, lon] = (q.get("ll") || "").split(",").map(Number);
  const z = Number(q.get("z"));
  let from = validDay(q.get("from")) ? q.get("from") : isoDay(monthAgo);
  let to = validDay(q.get("to")) ? q.get("to") : isoDay(today);
  if (from > to) [from, to] = [to, from];
  const cloud = Number(q.get("cloud"));
  return {
    center: Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lon, lat] : [4.35, 50.85],
    zoom: z >= 1 && z <= 19 ? z : 8,
    from, to,
    cloud: q.has("cloud") && cloud >= 0 && cloud <= 100 ? cloud : 30,
    scene: q.get("scene") || null,
    basemap: q.get("base") === "map" ? "map" : "satellite",
  };
}

const state = { ...readUrl(), results: [], selected: null, searchedView: null };

function writeUrl() {
  const c = map.getCenter();
  const q = new URLSearchParams();
  q.set("ll", `${c.lat.toFixed(4)},${c.lng.toFixed(4)}`);
  q.set("z", String(Math.round(map.getZoom())));
  q.set("from", state.from);
  q.set("to", state.to);
  q.set("cloud", String(state.cloud));
  if (state.selected) q.set("scene", state.selected.id);
  if (state.basemap === "map") q.set("base", "map");
  history.replaceState(null, "", `?${String(q).replace(/%2C/g, ",")}`);
}

// ---------- map ----------

registerCogProtocol(maplibregl);

const map = new maplibregl.Map({
  container: "map",
  center: state.center,
  zoom: state.zoom,
  attributionControl: { compact: true },
  style: {
    version: 8,
    sources: Object.fromEntries(Object.entries(BASEMAPS).map(([k, { label, ...b }]) => [k, { type: "raster", tileSize: 256, ...b }])),
    layers: Object.keys(BASEMAPS).map((k) => ({
      id: k, type: "raster", source: k, layout: { visibility: k === state.basemap ? "visible" : "none" },
    })),
  },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
map.addControl(new maplibregl.GeolocateControl({ fitBoundsOptions: { maxZoom: 11 } }), "bottom-right");
map.touchZoomRotate.disableRotation();
map.dragRotate.disable();

function setBasemap(name) {
  state.basemap = name;
  for (const k of Object.keys(BASEMAPS)) map.setLayoutProperty(k, "visibility", k === name ? "visible" : "none");
  els.basemap.textContent = BASEMAPS[name === "map" ? "satellite" : "map"].label;
  writeUrl();
}

const EMPTY = { type: "FeatureCollection", features: [] };

map.on("load", async () => {
  map.addSource("footprints", { type: "geojson", data: EMPTY, promoteId: "id" });
  map.addLayer({
    id: "fp-fill", type: "fill", source: "footprints",
    paint: { "fill-color": "#2dd4bf", "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.25, 0] },
  });
  map.addLayer({
    id: "fp-line", type: "line", source: "footprints",
    paint: {
      "line-color": ["case", ["boolean", ["feature-state", "selected"], false], "#f59e0b", "#2dd4bf"],
      "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1.2],
    },
  });
  setBasemap(state.basemap);
  if (state.scene) {
    try {
      select(await getScene(state.scene));
    } catch (err) {
      setStatus(`Could not open the shared scene: ${err.message}`, true);
    }
  }
  search();
});

map.on("moveend", () => {
  writeUrl();
  const v = state.searchedView;
  if (!v) return;
  const moved = Math.abs(map.getZoom() - v.zoom) > 0.5 || map.project(v.center).dist(map.project(map.getCenter())) > 80;
  els.searchHere.hidden = !moved;
});

map.on("click", "fp-fill", (e) => {
  const ids = new Set(e.features.map((f) => f.properties.id));
  const first = state.results.find((s) => ids.has(s.id));
  if (!first) return;
  setSheet(true);
  const card = els.results.querySelector(`[data-id="${CSS.escape(first.id)}"]`);
  card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  flashHover(first.id);
});

let lastError = 0;
map.on("error", (e) => {
  if (e.sourceId === "scene" && Date.now() - lastError > 5000) {
    lastError = Date.now();
    setStatus("Some imagery tiles could not be loaded. Pan or zoom to retry.", true);
  }
});

// ---------- search ----------

let searchAbort = null;

function viewBbox() {
  const b = map.getBounds();
  const clamp = (v, lim) => Math.max(-lim, Math.min(lim, v));
  return [clamp(b.getWest(), 180), clamp(b.getSouth(), 90), clamp(b.getEast(), 180), clamp(b.getNorth(), 90)];
}

async function search() {
  searchAbort?.abort();
  searchAbort = new AbortController();
  const signal = searchAbort.signal;
  state.searchedView = { center: map.getCenter(), zoom: map.getZoom() };
  els.searchHere.hidden = true;
  els.summary.textContent = "Searching…";
  setStatus("");
  try {
    const { scenes, more } = await searchScenes({ bbox: viewBbox(), from: state.from, to: state.to, maxCloud: state.cloud }, signal);
    state.results = scenes;
    renderResults();
    const n = scenes.length;
    els.summary.textContent = n ? `${n}${more ? "+" : ""} scene${n === 1 ? "" : "s"} · ${fmtRange()}` : `No scenes · ${fmtRange()}`;
    if (!n) setStatus("No scenes here for these dates and cloud limit. Try a wider date range or a higher cloud limit.");
    else if (more) setStatus(`Showing the newest ${n}. Zoom in or narrow the dates to see older ones.`);
    else setStatus("Tap a scene to show it on the map.");
  } catch (err) {
    if (signal.aborted) return;
    els.summary.textContent = "Search failed";
    setStatus(`Search failed: ${err.message}. Check your connection and try again.`, true);
  }
}

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.classList.toggle("error", isError);
}

const fmtDay = (iso, opts = { day: "numeric", month: "short" }) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { ...opts, timeZone: "UTC" });
const fmtRange = () => `${fmtDay(state.from)} – ${fmtDay(state.to)}`;

function renderResults() {
  const features = state.results.map((s) => ({ type: "Feature", geometry: s.geometry, properties: { id: s.id } }));
  map.getSource("footprints")?.setData({ type: "FeatureCollection", features });
  if (state.selected) map.setFeatureState({ source: "footprints", id: state.selected.id }, { selected: true });

  els.results.replaceChildren(...state.results.map((s) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "scene" + (state.selected?.id === s.id ? " selected" : "");
    btn.dataset.id = s.id;
    btn.setAttribute("aria-pressed", String(state.selected?.id === s.id));
    const thumb = s.thumbnail
      ? Object.assign(document.createElement("img"), { src: s.thumbnail, alt: "", loading: "lazy" })
      : Object.assign(document.createElement("span"), { className: "noimg" });
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.innerHTML = `<span class="date"></span><span class="sub"></span>`;
    meta.firstChild.textContent = fmtDay(s.date, { day: "numeric", month: "short", year: "numeric" });
    meta.lastChild.textContent = [s.cloud != null ? `${Math.round(s.cloud)}% cloud` : null, s.tile, s.platform].filter(Boolean).join(" · ");
    btn.append(thumb, meta);
    btn.addEventListener("click", () => select(state.selected?.id === s.id ? null : s));
    btn.addEventListener("mouseenter", () => setHover(s.id, true));
    btn.addEventListener("mouseleave", () => setHover(s.id, false));
    li.append(btn);
    return li;
  }));
}

function setHover(id, on) {
  if (map.getSource("footprints")) map.setFeatureState({ source: "footprints", id }, { hover: on });
  els.results.querySelector(`[data-id="${CSS.escape(id)}"]`)?.classList.toggle("hover", on);
}

function flashHover(id) {
  setHover(id, true);
  setTimeout(() => setHover(id, false), 1200);
}

// ---------- streaming a scene ----------

function select(scene) {
  if (state.selected && map.getSource("footprints")) {
    map.setFeatureState({ source: "footprints", id: state.selected.id }, { selected: false });
  }
  if (map.getLayer("scene")) map.removeLayer("scene");
  if (map.getSource("scene")) map.removeSource("scene");
  state.selected = scene;

  if (scene) {
    map.addSource("scene", {
      type: "raster",
      tiles: [cogTileUrl(scene.visual, scene.epsg)],
      tileSize: 256,
      bounds: scene.bbox,
      maxzoom: 15,
      attribution: IMAGERY_ATTRIBUTION,
    });
    map.addLayer({ id: "scene", type: "raster", source: "scene", paint: { "raster-fade-duration": 150 } }, "fp-fill");
    map.setFeatureState({ source: "footprints", id: scene.id }, { selected: true });
    els.showingLabel.textContent = `${fmtDay(scene.date, { day: "numeric", month: "short", year: "numeric" })} · ${scene.tile}`;
    if (!boundsOverlap(viewBbox(), scene.bbox)) {
      map.fitBounds(scene.bbox, { padding: 40 });
    }
    setStatus("Loading imagery…");
    const done = () => {
      if (map.getSource("scene") && !map.isSourceLoaded("scene")) return;
      map.off("sourcedata", done);
      if (els.status.textContent === "Loading imagery…") setStatus("");
    };
    map.on("sourcedata", done);
    if (matchMedia("(max-width: 767px)").matches) setSheet(false);
  } else {
    setStatus("");
  }
  els.showing.hidden = !scene;
  for (const card of els.results.querySelectorAll(".scene")) {
    const on = card.dataset.id === scene?.id;
    card.classList.toggle("selected", on);
    card.setAttribute("aria-pressed", String(on));
  }
  writeUrl();
}

function boundsOverlap(a, b) {
  return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}

// ---------- place search ----------

async function goToPlace(text) {
  const m = text.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (m && Math.abs(+m[1]) <= 90 && Math.abs(+m[2]) <= 180) {
    map.flyTo({ center: [+m[2], +m[1]], zoom: Math.max(map.getZoom(), 10) });
    return;
  }
  setStatus(`Looking up “${text}”…`);
  try {
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(text)}`;
    const [hit] = await (await fetch(url, { headers: { Accept: "application/json" } })).json();
    if (!hit) return setStatus(`No place called “${text}” found.`, true);
    const [s, n, w, e] = hit.boundingbox.map(Number);
    map.fitBounds([[w, s], [e, n]], { maxZoom: 12, padding: 20 });
    setStatus("");
    map.once("moveend", search);
  } catch {
    setStatus("Place search is unavailable right now.", true);
  }
}

// ---------- controls ----------

function setSheet(open) {
  els.sheet.classList.toggle("collapsed", !open);
  els.handle.setAttribute("aria-expanded", String(open));
}

els.handle.addEventListener("click", () => setSheet(els.sheet.classList.contains("collapsed")));
els.searchHere.addEventListener("click", search);
els.clear.addEventListener("click", () => select(null));
els.basemap.addEventListener("click", () => setBasemap(state.basemap === "map" ? "satellite" : "map"));
els.placeForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.place.value.trim();
  if (text) goToPlace(text);
  els.place.blur();
});

els.from.value = state.from;
els.to.value = state.to;
els.cloud.value = String(state.cloud);
els.cloudOut.value = `${state.cloud}%`;

function onDates() {
  if (!validDay(els.from.value) || !validDay(els.to.value)) return;
  [state.from, state.to] = [els.from.value, els.to.value].sort();
  els.from.value = state.from;
  els.to.value = state.to;
  writeUrl();
  search();
}
els.from.addEventListener("change", onDates);
els.to.addEventListener("change", onDates);
els.cloud.addEventListener("input", () => (els.cloudOut.value = `${els.cloud.value}%`));
els.cloud.addEventListener("change", () => {
  state.cloud = Number(els.cloud.value);
  writeUrl();
  search();
});
