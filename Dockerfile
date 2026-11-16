# Finanz-Dashboard als Container (NAS, Raspberry Pi, Docker Desktop, Server).
# Fertiges Image: ghcr.io/kevegl/finance-dashboard (amd64 + arm64), siehe docker-compose.yml.
# Selbst bauen:  docker build -t finance-dashboard . && \
#   docker run -d -p 8080:8080 -v fd-data:/data --name finance finance-dashboard
FROM python:3.12-slim
# pdftotext für den PDF-Import (VIAC)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/ /app/
ENV FD_DATA_DIR=/data FD_PORT=8080 FD_BASE_CCY=CHF FD_DOCKER=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /data && chown 1000:1000 /data
VOLUME /data
EXPOSE 8080
USER 1000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"
CMD ["python3", "server.py"]
