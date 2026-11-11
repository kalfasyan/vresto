# Vresto Lite

A static, browser-only version of Vresto for phones and quick looks. It searches Sentinel-2 L2A
scenes on [Earth Search](https://element84.com/earth-search/) and streams their true-colour COGs
from AWS Open Data straight into the map. There is no server: each map tile is read and
reprojected from UTM in the browser (`cog.js`).

It is published with the docs by `.github/workflows/build-docs.yml`, at
<https://kalfasyan.github.io/vresto/app/>.

Run it locally with any static server, for example:

```bash
python -m http.server -d web 8000   # then open http://localhost:8000
```

| File | What it does |
| --- | --- |
| `index.html` | Page layout; loads MapLibre GL, geotiff.js and proj4 from jsDelivr (pinned versions) |
| `app.js` | Map, search sheet, results, place search, shareable URL |
| `stac.js` | Earth Search queries |
| `cog.js` | `s2cog://` MapLibre protocol that turns a Sentinel-2 COG into Web Mercator tiles |
| `app.css` | Phone-first layout: bottom sheet on phones, side panel from 768 px |
| `tests/smoke.mjs` | Phone-size browser test against fixtures, run in CI by `.github/workflows/web.yml` |

Run the test locally with `cd web/tests && npm install && npx playwright install chromium && node smoke.mjs`.
