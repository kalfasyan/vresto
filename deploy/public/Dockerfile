# Docker image for the public vresto demo (Google Cloud Run, Hugging Face Spaces, any Docker host).
# The deploy workflows (.github/workflows/deploy-cloud-run.yml, deploy-hf-space.yml) pin
# VRESTO_REF to the commit being deployed.
FROM python:3.12-slim

ARG VRESTO_REF=main

RUN apt-get -qq update \
    && apt-get -qq install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces run the container as user 1000, so everything the app writes lives in its home.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /home/user/app

RUN pip install --no-cache-dir --user "vresto @ git+https://github.com/kalfasyan/vresto@${VRESTO_REF}"

# Public mode: no login, per-tab credentials, downloads sent to the browser,
# server-disk tabs hidden, map tiles served through the single public port.
ENV VRESTO_PUBLIC_MODE=1 \
    VRESTO_TILE_POOL_SIZE=32 \
    VRESTO_UI_SHOW_BROWSER=0 \
    NICEGUI_WEBSERVER_HOST=0.0.0.0 \
    NICEGUI_WEBSERVER_PORT=7860

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:7860 || exit 1

CMD ["vresto"]
