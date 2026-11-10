# Hosting a Public Demo

vresto can run as a website that anyone opens with one click, without signing in. Searching,
tile streaming, the coverage grid and pixel history already work without an account, so a
shared server only needs to keep visitors apart. That is what **public mode** does.

## Public mode

Start vresto with `VRESTO_PUBLIC_MODE=1`:

```bash
VRESTO_PUBLIC_MODE=1 NICEGUI_WEBSERVER_PORT=7860 vresto
```

In public mode:

- **Credentials stay with the visitor.** What a visitor types in ☰ → Settings is kept in
  memory for that browser tab only. It is never written to `.env` or the server's environment,
  never shown to other visitors, and forgotten when the tab closes (any temporary S3 keys it
  created in the visitor's account are deleted). The server's own credentials are never used
  for a visitor's downloads, quicklooks or metadata.
- **Downloads go to the browser.** Bands are fetched into a temporary folder, sent to the
  visitor (several bands as one zip) and cleaned up within an hour.
- **Server-disk tabs are hidden.** *Product Analysis* and *Hi-Res Tiler* read folders on the
  machine running vresto, so they are left out.
- **Tiles use one port.** Streamed tiles and overlays are served through the main web port
  (`/_vresto/tiles/...`) instead of extra ports, which is what hosts such as Hugging Face
  Spaces allow. Each URL only reaches the file vresto streamed for it.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VRESTO_PUBLIC_MODE` | off | Turn public mode on. |
| `VRESTO_TILE_PROXY` | off (on in public mode) | Serve tiles through the main port, without public mode. Useful behind a reverse proxy or in Docker. |
| `VRESTO_TILE_POOL_SIZE` | `12` | Streamed layers kept live across all visitors (~50 MB each). |
| `VRESTO_PUBLIC_MAX_DOWNLOAD_FILES` | `12` | Most band files per download (`0` = no limit). |
| `VRESTO_PUBLIC_DOWNLOAD_DIR` | system temp | Where downloads wait before reaching the browser. |

Optionally, give the server CDSE S3 keys (`COPERNICUS_S3_ACCESS_KEY`,
`COPERNICUS_S3_SECRET_KEY`) to stream from CDSE and enable the CDSE-only overlays for everyone.
Streaming then counts against that account's quota. Without them, tiles stream from AWS Open
Data.

## Hugging Face Spaces

The `deploy/huggingface` folder holds a ready Space (a Dockerfile plus the Space's README),
and the `deploy-hf-space` workflow publishes it on every push to `main`:

1. On huggingface.co, create a new Space with the **Docker** SDK (blank template).
2. Create a Hugging Face access token with write access to that Space.
3. In the GitHub repository, open *Settings → Secrets and variables → Actions* and add the
   secret `HF_TOKEN` (the token) and the variable `HF_SPACE` (for example `kalfasyan/vresto`).
4. Run the *deploy-hf-space* workflow, or push to `main`.

The Space builds the image from the pushed commit and serves vresto at
`https://<user>-<space>.hf.space`. Free Spaces have 2 vCPUs and 16 GB of RAM, are shared by
all visitors, and go to sleep after a while without visits; the first visit then takes a
minute to wake up.

## Any other host

Any host that runs a Docker container and exposes one port works the same way (a VPS, Fly.io,
Render, Google Cloud Run with session affinity). Build `deploy/huggingface/Dockerfile`, or run
the main image with `VRESTO_PUBLIC_MODE=1`. The host must support WebSockets, which the web
interface uses.
