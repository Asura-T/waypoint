# Waypoint: one container serving the web app and its small save API.
FROM python:3.12-alpine

LABEL org.opencontainers.image.title="Waypoint" \
      org.opencontainers.image.description="Interactive 3D office map for planning office improvements and documenting network equipment"

ARG WAYPOINT_VERSION=dev
ENV PYTHONUNBUFFERED=1 \
    WAYPOINT_VERSION=${WAYPOINT_VERSION} \
    WAYPOINT_DATA_DIR=/data \
    WAYPOINT_HOST=0.0.0.0 \
    WAYPOINT_PORT=8080

WORKDIR /opt/waypoint
COPY app ./app
COPY demo ./demo

# /data holds every saved map; mount a persistent dataset here.
# 568 is the "apps" user on TrueNAS; override with `user:` in compose if your dataset uses another owner.
RUN mkdir -p /data && chown 568:568 /data
USER 568:568
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('WAYPOINT_PORT','8080'), timeout=4)"

CMD ["python", "app/server.py"]
