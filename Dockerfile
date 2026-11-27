FROM debian:bookworm-slim AS media
# This machine's FFmpeg, and a cross-compiled one for the other common Linux
# architecture, so a Sparrow Node of either kind gets media tools from here.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential nasm pkg-config python3 ca-certificates xz-utils \
    "crossbuild-essential-$(case "$(uname -m)" in aarch64) echo amd64 ;; *) echo arm64 ;; esac)" \
    && rm -rf /var/lib/apt/lists/*
COPY packaging/sources.json packaging/fetch_sources.py /package/
COPY packaging/media /package/media
RUN python3 /package/fetch_sources.py /inputs
RUN sh /package/media/build.sh linux /output
RUN other="$(case "$(uname -m)" in aarch64) echo x86_64 ;; *) echo aarch64 ;; esac)" \
    && sh /package/media/build.sh "linux-$other" "/output-other"

# Sparrow Node's own download app, from Alpine's signed packages (see the script).
FROM alpine:3.23 AS node-transmission
COPY packaging/node_tools/transmission.sh /transmission.sh
RUN sh /transmission.sh /node-tools

# One archive per architecture: FFmpeg, ffprobe and Transmission, all
# self-contained. Paired computers download theirs while they set up.
FROM debian:bookworm-slim AS node-tools
COPY --from=node-transmission /node-tools /node-tools
COPY --from=media /output/bin /ffmpeg-native
COPY --from=media /output-other/bin /ffmpeg-other
COPY --from=media /output/sources /ffmpeg-sources
COPY packaging/node_tools/assemble.sh /assemble.sh
RUN sh /assemble.sh /node-tools /ffmpeg-native /ffmpeg-other /ffmpeg-sources /output

FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS dependencies
WORKDIR /build
COPY requirements.lock ./
RUN pip install --no-cache-dir --prefix=/install --require-hashes -r requirements.lock
COPY packaging /package
RUN PYTHONPATH=/install/lib/python3.11/site-packages python /package/fetch_model.py /models/whisper-base
RUN PYTHONPATH=/install/lib/python3.11/site-packages python /package/collect_licenses.py /licenses

FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates transmission-daemon && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home sparrow
COPY --from=dependencies /install /usr/local
COPY --from=dependencies /models /opt/sparrow/models
COPY --from=media /output/bin /opt/sparrow/bin
COPY --from=media /output/sources /opt/sparrow/sources
COPY --from=node-tools /output /opt/sparrow/node-tools
COPY --from=dependencies /licenses /opt/sparrow/licenses
COPY packaging/THIRD_PARTY.md /opt/sparrow/THIRD_PARTY.md
WORKDIR /app
COPY backend /app/backend
COPY --from=frontend /build/dist /app/frontend/dist
COPY packaging/entrypoint.py /app/entrypoint.py
ENV SPARROW_DATA_DIR=/data SPARROW_NODE_TOOLS=/opt/sparrow/node-tools SPARROW_FFMPEG=/opt/sparrow/bin/ffmpeg SPARROW_FFPROBE=/opt/sparrow/bin/ffprobe SPARROW_TRANSCRIPTION_MODEL=/opt/sparrow/models/whisper-base PYTHONUNBUFFERED=1
RUN mkdir /data && chown 10001:10001 /data && mkdir -p /media/Library /media/Incoming && chown -R 10001:10001 /media
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/v1/auth/status',timeout=3)"
# Which commit this image is, for the version each computer shows. Pass them
# when building: SPARROW_COMMIT=$(git rev-parse --short HEAD) and
# SPARROW_COMMIT_DATE=$(git log -1 --format=%cI). Last, so the cache holds.
ARG SPARROW_COMMIT=""
ARG SPARROW_COMMIT_DATE=""
ENV SPARROW_COMMIT=$SPARROW_COMMIT SPARROW_COMMIT_DATE=$SPARROW_COMMIT_DATE
ENTRYPOINT ["python","/app/entrypoint.py"]
