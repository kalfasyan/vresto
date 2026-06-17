# Evaluation: VirtualiZarr & Icechunk for vresto

## Summary

This document evaluates whether **VirtualiZarr** and **Icechunk** could benefit vresto — specifically for visualizing more map layers on the home page and for faster processing of satellite data from S3.

---

## Current Architecture

vresto currently:

1. **Discovers** Sentinel products via OData or STAC APIs (Copernicus Data Space).
2. **Downloads** bands from S3 as JP2/GeoTIFF files to local disk.
3. **Visualizes** bands using `localtileserver` (serves local rasters as XYZ tiles).
4. **Overlays** WorldCover and LCM layers by streaming COG windows via GDAL `/vsis3/` virtual filesystem with HTTP range requests.

Key observations:
- Only one tile server client can be active at a time (`TileManager` has a single `_active_client`).
- WorldCover/LCM already use efficient COG streaming (no full downloads).
- Band visualization requires full file download before tile serving.

---

## VirtualiZarr

### What It Does

VirtualiZarr creates **virtual Zarr stores** — metadata layers that present many remote files (COGs, NetCDF, Zarr chunks in S3) as a single logical N-dimensional array. No data is physically copied or reformatted; reads are lazy and only fetch the needed chunks on demand.

### Relevance to vresto

| Use Case | Benefit | Feasibility |
|----------|---------|-------------|
| **Multi-band composite visualization** | Instead of downloading 3 separate JP2 band files to disk, create a virtual Zarr that references the remote S3 objects. `xarray` + `dask` can lazily compute RGB composites on the fly. | Medium — requires switching from `localtileserver` (file-based) to a Zarr/xarray-backed tile renderer. |
| **More layers on home page** | Virtual references could unify WorldCover, LCM, and Sentinel data into a single virtual dataset. Layers could be added without duplicating data or pre-downloading. | Medium — the home page currently uses Leaflet XYZ tiles; a virtual Zarr dataset would still need a tile-serving layer to bridge to Leaflet. |
| **Time-series / temporal stacks** | Virtual Zarr can present thousands of Sentinel acquisitions as a time-stacked cube without any data movement. Useful for temporal composites or change detection overlays. | High potential, but adds complexity beyond current vresto scope. |

### Limitations for vresto

- **Not a tile server replacement**: VirtualiZarr provides array-level access, not XYZ map tiles. vresto would still need something like `titiler`, `dynamic-tiling`, or a custom renderer to convert Zarr slices into PNG tiles for Leaflet.
- **Latency**: Lazy reads from S3 for individual tiles may be slower than pre-downloaded local rasters for single-user desktop use.
- **JP2 support**: Sentinel L2A bands are JPEG2000 on CDSE S3. VirtualiZarr works best with COG or Zarr; JP2 lacks the internal tiling structure for efficient random chunk access.

---

## Icechunk

### What It Does

Icechunk is a **cloud-native, transactional tensor storage engine** built on top of Zarr. It adds:
- Git-like versioning and atomic snapshots
- Concurrent write safety (transactional)
- Extremely fast random access to data cubes on S3
- Ability to create "virtual" datasets over legacy formats (NetCDF, HDF5) without data migration

### Relevance to vresto

| Use Case | Benefit | Feasibility |
|----------|---------|-------------|
| **Faster S3 reads** | Icechunk's optimized S3 access (demonstrated 100× speedup by NASA for time-series extraction) could dramatically accelerate band retrieval if Sentinel data were stored as Zarr/Icechunk stores. | Low for CDSE — data is served as COG/JP2, not Zarr. Would only apply if vresto managed its own data lake. |
| **Caching/versioning downloaded data** | Icechunk could serve as a local versioned cache of processed layers — useful for rollback, reproducibility, and sharing curated datasets. | Medium — adds infrastructure overhead for what is currently a visualization tool. |
| **Multi-layer serving** | An Icechunk store could hold pre-processed WorldCover + LCM + Sentinel composites as a unified cube with version control, enabling fast random access for tile generation. | Medium–High — good fit if vresto evolves toward a data-cube dashboard. |
| **Collaborative workflows** | Multiple users could concurrently add/modify analysis layers with transactional safety. | Low priority — vresto is currently single-user. |

### Limitations for vresto

- **Data source mismatch**: Copernicus Data Space serves data as COG/JP2 on S3, not as Zarr or Icechunk stores. The benefit materializes only if vresto pre-ingests data into Icechunk.
- **Operational complexity**: Running an Icechunk store requires managing metadata and chunk storage — heavier than the current download-and-visualize model.
- **Overkill for current scale**: vresto visualizes a handful of products at a time. Icechunk shines at petabyte scale with many concurrent users.

---

## Recommendations

### Short-term (Low Effort, High Impact)

These improvements don't require VirtualiZarr or Icechunk:

1. **Allow multiple simultaneous tile server instances** — refactor `TileManager` to support multiple `TileClient` objects so the home page can show several band layers at once.
2. **Stream bands directly from S3** — use GDAL `/vsicurl/` or `/vsis3/` to serve COG tiles without downloading entire files (similar to the existing WorldCover approach).

### Medium-term (If vresto adds data-cube features)

3. **Adopt VirtualiZarr for virtual mosaics** — when vresto needs temporal composites or multi-product mosaics, VirtualiZarr can create virtual datasets over remote COGs without data duplication. Pair with `titiler` or a custom xarray-to-tile bridge.
4. **Evaluate Icechunk as a processed-layer store** — if vresto introduces a persistent cache of derived products (NDVI, RGB composites, classified maps), Icechunk provides versioned, fast-access storage.

### Long-term (Data Platform Evolution)

5. **Full Zarr/Icechunk data lake** — if vresto evolves from a visualization tool into a collaborative analysis platform, Icechunk becomes highly relevant for managing versioned data cubes with concurrent access.

---

## Conclusion

| Technology | Immediate value for vresto? | Future value? |
|---|---|---|
| **VirtualiZarr** | Limited — vresto's data is JP2/COG, and the tile-serving path needs a bridge layer | High — enables scalable virtual mosaics and temporal stacks without data duplication |
| **Icechunk** | Limited — adds infrastructure complexity without matching current single-user, few-products workflow | High — ideal if vresto becomes a multi-user data-cube platform |

**Bottom line**: The immediate wins for "more layers" and "faster S3 processing" are better achieved by refactoring the tile manager to support concurrent layers and streaming COGs directly from S3 via GDAL virtual filesystems. VirtualiZarr and Icechunk become compelling when vresto needs temporal data cubes, virtual mosaics over thousands of files, or versioned collaborative data stores.

---

## References

- [VirtualiZarr Documentation](https://virtualizarr.readthedocs.io/)
- [Icechunk Documentation](https://icechunk.io/)
- [NASA Icechunk Case Study](https://www.earthmover.io/blog/nasa-icechunk)
- [Icechunk at NOAA/NWS](https://www.earthmover.io/blog/icechunk-at-nws-cirrus)
- [Zarr, Icechunk & Xarray for Cloud-Native Geospatial (CNG 2025)](https://github.com/earth-mover/workshop-cng-2025-zarr)
