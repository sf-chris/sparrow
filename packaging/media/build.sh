#!/bin/sh
# FFmpeg with x264 and zlib, from pinned sources.
#   build.sh linux            this machine's Linux, static (the server and nodes like it)
#   build.sh linux-aarch64    static, cross-compiled unless this machine is aarch64
#   build.sh linux-x86_64     static, cross-compiled unless this machine is x86_64
#   build.sh windows          Windows x64, for the Windows node installer
# Static Linux builds need nothing from the computer they run on, so the same
# binary serves this server and every Sparrow Node of that architecture.
set -eu
platform=${1:-linux}
output=${2:-/output}
native=$(uname -m)
case "$platform" in
  linux) platform="linux-$native" ;;
esac
build=/build/$platform
prefix=$build/prefix
jobs=$(nproc 2>/dev/null || echo 4)
mkdir -p "$build" "$output/bin" "$output/sources"
cp /inputs/ffmpeg-8.0.1.tar.xz /inputs/x264-c24e06c.tar.gz /inputs/zlib-1.3.1.tar.gz "$output/sources/"
cp /package/sources.json /package/media/build.sh "$output/sources/"
cd "$build"
tar xf /inputs/x264-c24e06c.tar.gz
tar xf /inputs/ffmpeg-8.0.1.tar.xz
tar xf /inputs/zlib-1.3.1.tar.gz

cross=''
x264_cross=''
ffmpeg_cross=''
static=' -static'
case "$platform" in
  windows)
    cross=x86_64-w64-mingw32-
    x264_cross="--host=x86_64-w64-mingw32 --cross-prefix=$cross"
    ffmpeg_cross="--enable-cross-compile --target-os=mingw32 --arch=x86_64 --cross-prefix=$cross"
    ;;
  linux-"$native") ;;
  linux-aarch64|linux-x86_64)
    arch=${platform#linux-}
    cross=$arch-linux-gnu-
    x264_cross="--host=$arch-linux-gnu --cross-prefix=$cross"
    ffmpeg_cross="--enable-cross-compile --target-os=linux --arch=$arch --cross-prefix=$cross"
    ;;
  *) echo "Unknown platform: $platform" >&2; exit 1 ;;
esac

# zlib: Matroska tracks are often zlib-compressed (mkvmerge does it to every
# picture subtitle track by default); without it they cannot be decoded.
cd "$build/zlib-1.3.1"
if [ "$platform" = windows ]; then
  make -f win32/Makefile.gcc PREFIX=$cross BINARY_PATH=$prefix/bin INCLUDE_PATH=$prefix/include LIBRARY_PATH=$prefix/lib install
else
  CC=${cross}gcc AR=${cross}ar RANLIB=${cross}ranlib CFLAGS=-fPIC ./configure --static --prefix="$prefix"
  make -j"$jobs"
  make install
fi
cd "$build/x264-c24e06c2e184345ceb33eb20a15d1024d9fd3497"
# shellcheck disable=SC2086 # The cross options are separate words.
./configure --prefix="$prefix" --enable-static --disable-cli --disable-opencl $x264_cross
make -j"$jobs"
make install
cd "$build/ffmpeg-8.0.1"
# shellcheck disable=SC2086
PKG_CONFIG_PATH=$prefix/lib/pkgconfig PKG_CONFIG_LIBDIR=$prefix/lib/pkgconfig ./configure --prefix="$output" --disable-autodetect --disable-debug --disable-doc --disable-ffplay --disable-network --enable-gpl --enable-libx264 --enable-zlib --extra-cflags="-I$prefix/include" "--extra-ldflags=-L$prefix/lib$static" --pkg-config=pkg-config --pkg-config-flags=--static $ffmpeg_cross
make -j"$jobs"
make install
cp COPYING.GPLv2 "$output/sources/FFmpeg-COPYING.GPLv2"
cp "$build/x264-c24e06c2e184345ceb33eb20a15d1024d9fd3497/COPYING" "$output/sources/x264-COPYING"
cp "$build/zlib-1.3.1/LICENSE" "$output/sources/zlib-LICENSE"
cp ffbuild/config.log "$output/sources/ffmpeg-config.log"
rm -rf "$output/include" "$output/lib" "$output/share"
