# Alternative zur LXC-Installation: docker build -t finance-dashboard . && \
#   docker run -d -p 8080:8080 -v fd-data:/data --name finance finance-dashboard
FROM python:3.12-slim
WORKDIR /app
COPY app/ /app/
ENV FD_DATA_DIR=/data FD_PORT=8080 FD_BASE_CCY=CHF PYTHONUNBUFFERED=1
RUN mkdir -p /data && chown 1000:1000 /data
VOLUME /data
EXPOSE 8080
USER 1000
CMD ["python3", "server.py"]
