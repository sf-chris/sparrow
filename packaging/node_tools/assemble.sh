#!/bin/sh
# One archive per Linux architecture for Sparrow Node: bin/ffmpeg, bin/ffprobe
# and bin/transmission-daemon, each runnable without anything from the
# computer it lands on, plus a manifest and the licences.
#   assemble.sh <transmission dir> <native ffmpeg> <other ffmpeg> <ffmpeg sources> <output>
set -eu
transmission=$1
native_ffmpeg=$2
other_ffmpeg=$3
sources=$4
output=$5
native=$(uname -m)
mkdir -p "$output"
for arch in x86_64 aarch64; do
  stage=/tmp/assemble/linux-$arch
  rm -rf "$stage"
  mkdir -p "$stage/bin" "$stage/licences"
  cp -a "$transmission/linux-$arch/." "$stage/"
  if [ "$arch" = "$native" ]; then ffmpeg=$native_ffmpeg; else ffmpeg=$other_ffmpeg; fi
  cp "$ffmpeg/ffmpeg" "$ffmpeg/ffprobe" "$stage/bin/"
  chmod 755 "$stage/bin/"*
  cp "$sources/FFmpeg-COPYING.GPLv2" "$sources/x264-COPYING" "$sources/zlib-LICENSE" "$stage/licences/"
  cat > "$stage/licences/README.txt" <<NOTE
FFmpeg 8.0.1 with x264 and zlib, built by Sparrow from the pinned sources in
sources.json (the sources ship in the Sparrow server image under
/opt/sparrow/sources). Licensed under the GNU GPL version 2 or later.

Transmission and the libraries beside it come unchanged from the Alpine Linux
packages listed in transmission/packages.txt. Their sources and licences are at
https://gitlab.alpinelinux.org/alpine/aports and https://pkgs.alpinelinux.org.
NOTE
  # The bundle's identity: what it holds, so a computer can tell when it's out of date.
  identity=$( (cat "$stage/transmission/packages.txt"; sha256sum "$stage/bin/ffmpeg" "$stage/bin/ffprobe" | cut -d' ' -f1) | sha256sum | cut -c1-16)
  transmission_version=$(grep '^transmission-daemon-' "$stage/transmission/packages.txt" | sed 's/^transmission-daemon-//')
  printf '{"platform": "linux-%s", "version": "%s", "ffmpeg": "8.0.1", "transmission": "%s"}\n' \
    "$arch" "$identity" "$transmission_version" > "$stage/manifest.json"
  tar -C "$stage" -czf "$output/tools-linux-$arch.tar.gz" .
done
ls -la "$output"
