# Media Server Dashboard as a container (Docker, Synology Container Manager, Portainer, ...).
# Plain Python, no packages: the image is the Python base image plus this folder.
#
#   docker compose up -d     (see docker-compose.yml and docs/docker.md)
FROM python:3.13-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="Media Server Dashboard" \
      org.opencontainers.image.description="Dashboard and 3D office for a homelab media stack (Proxmox, *arr apps)" \
      org.opencontainers.image.source="https://github.com/lcsfls/media-server-dashboard-extended" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

ENV MSD_DATA=/data \
    MSD_MODE=docker \
    MSD_PORT=8900 \
    MSD_BIND=0.0.0.0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY *.py *.html VERSION LICENSE ./
COPY vendor ./vendor
# Settings, password hash, API keys and the game live in /data: mount a volume or a folder there.
RUN useradd --uid 1000 --user-group --home-dir /data --no-create-home msd \
    && mkdir -p /data && chown msd:msd /data
USER msd
VOLUME /data
EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('MSD_PORT', '8900'), timeout=4)"]

CMD ["python3", "/app/server.py"]
