# syntax=docker/dockerfile:1

FROM ghcr.io/osgeo/gdal:ubuntu-small-3.11.3

SHELL ["sh", "-exc"]

# Copy uv from the official uv image
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

# Install additional system dependencies and build tools
RUN apt-get -qq update && \
    apt-get -qq install -y \
    curl \
    tzdata \
    build-essential \
    && apt clean && rm -rf /var/lib/apt/lists/*


# Set timezone
ENV TZ=Europe/Brussels
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Set UV environment variables
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.11 \
    UV_HTTP_TIMEOUT=1000 \
    UV_PYTHON_PREFERENCE=only-managed

# Set working directory
WORKDIR /app

# Install dependencies using uv with cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --frozen --no-install-project --no-dev

# Copy source code and scripts
COPY src ./src
COPY scripts ./scripts

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=README.md,target=README.md \
    uv sync --frozen --no-dev

# Set environment variables
ENV NICEGUI_WEBSERVER_PORT=8610
ENV NICEGUI_WEBSERVER_HOST=0.0.0.0
ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/src:$PYTHONPATH

# Expose web dashboard port
EXPOSE 8610

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8610 || exit 1

# Start the vresto web interface
CMD ["uv", "run", "vresto"]
