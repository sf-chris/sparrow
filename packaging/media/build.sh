#!/bin/sh
set -eu
platform=${1:-linux}
mkdir -p /build /output/bin /output/sources
cp /inputs/ffmpeg-8.0.1.tar.xz /inputs/x264-c24e06c.tar.gz /output/sources/
cp /package/sources.json /package/media/build.sh /output/sources/
cd /build
tar xf /inputs/x264-c24e06c.tar.gz
tar xf /inputs/ffmpeg-8.0.1.tar.xz
cross=''
if [ "$platform" = windows ]; then cross='--host=x86_64-w64-mingw32 --cross-prefix=x86_64-w64-mingw32-'; fi
cd /build/x264-c24e06c2e184345ceb33eb20a15d1024d9fd3497
./configure --prefix=/build/prefix --enable-static --disable-cli --disable-opencl $cross
make -j4
make install
cd /build/ffmpeg-8.0.1
cross=''
if [ "$platform" = windows ]; then cross='--enable-cross-compile --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32- --extra-ldflags=-static'; fi
PKG_CONFIG_PATH=/build/prefix/lib/pkgconfig ./configure --prefix=/output --disable-autodetect --disable-debug --disable-doc --disable-ffplay --disable-network --enable-gpl --enable-libx264 --pkg-config=pkg-config --pkg-config-flags=--static $cross
make -j4
make install
cp COPYING.GPLv2 /output/sources/FFmpeg-COPYING.GPLv2
cp /build/x264-c24e06c2e184345ceb33eb20a15d1024d9fd3497/COPYING /output/sources/x264-COPYING
cp ffbuild/config.log /output/sources/ffmpeg-config.log
rm -rf /output/include /output/lib /output/share
