FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc-mingw-w64-x86-64 nasm pkg-config python3 ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*
COPY packaging /package
RUN python3 /package/fetch_sources.py /inputs
ARG MEDIA_PLATFORM=linux
RUN sh /package/media/build.sh "$MEDIA_PLATFORM"
FROM scratch AS output
COPY --from=build /output/ /
