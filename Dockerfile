FROM debian:bookworm-slim AS media
RUN apt-get update && apt-get install -y --no-install-recommends build-essential nasm pkg-config python3 ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
COPY packaging /package
RUN python3 /package/fetch_sources.py /inputs
RUN sh /package/media/build.sh linux

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
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home sparrow
COPY --from=dependencies /install /usr/local
COPY --from=dependencies /models /opt/sparrow/models
COPY --from=media /output/bin /opt/sparrow/bin
COPY --from=media /output/sources /opt/sparrow/sources
COPY --from=dependencies /licenses /opt/sparrow/licenses
COPY packaging/THIRD_PARTY.md /opt/sparrow/THIRD_PARTY.md
WORKDIR /app
COPY backend /app/backend
COPY --from=frontend /build/dist /app/frontend/dist
COPY packaging/entrypoint.py /app/entrypoint.py
ENV SPARROW_DATA_DIR=/data SPARROW_FFMPEG=/opt/sparrow/bin/ffmpeg SPARROW_FFPROBE=/opt/sparrow/bin/ffprobe SPARROW_TRANSCRIPTION_MODEL=/opt/sparrow/models/whisper-base PYTHONUNBUFFERED=1
RUN mkdir /data && chown 10001:10001 /data
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/v1/auth/status',timeout=3)"
ENTRYPOINT ["python","/app/entrypoint.py"]
