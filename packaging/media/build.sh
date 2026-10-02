#!/bin/sh
set -eu
platform=${1:-linux}
mkdir -p /build /output/bin /output/sources
cp /inputs/ffmpeg-8.0.1.tar.xz /inputs/x264-c24e06c.tar.gz /inputs/zlib-1.3.1.tar.gz /output/sources/
cp /package/sources.json /package/media/build.sh /output/sources/
cd /build
tar xf /inputs/x264-c24e06c.tar.gz
tar xf /inputs/ffmpeg-8.0.1.tar.xz
tar xf /inputs/zlib-1.3.1.tar.gz
# zlib: Matroska tracks are often zlib-compressed (mkvmerge does it to every
# picture subtitle track by default); without it they cannot be decoded.
cd /build/zlib-1.3.1
if [ "$platform" = windows ]; then
  make -f win32/Makefile.gcc PREFIX=x86_64-w64-mingw32- BINARY_PATH=/build/prefix/bin INCLUDE_PATH=/build/prefix/include LIBRARY_PATH=/build/prefix/lib install
else
  CFLAGS=-fPIC ./configure --static --prefix=/build/prefix
  make -j4
  make install
fi
cd /build
cross=''
if [ "$platform" = windows ]; then cross='--host=x86_64-w64-mingw32 --cross-prefix=x86_64-w64-mingw32-'; fi
cd /build/x264-c24e06c2e184345ceb33eb20a15d1024d9fd3497
./configure --prefix=/build/prefix --enable-static --disable-cli --disable-opencl $cross
make -j4
make install
cd /build/ffmpeg-8.0.1
cross=''
static=''
if [ "$platform" = windows ]; then cross='--enable-cross-compile --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32-'; static=' -static'; fi
PKG_CONFIG_PATH=/build/prefix/lib/pkgconfig ./configure --prefix=/output --disable-autodetect --disable-debug --disable-doc --disable-ffplay --disable-network --enable-gpl --enable-libx264 --enable-zlib --extra-cflags=-I/build/prefix/include "--extra-ldflags=-L/build/prefix/lib$static" --pkg-config=pkg-config --pkg-config-flags=--static $cross
make -j4
make install
cp COPYING.GPLv2 /output/sources/FFmpeg-COPYING.GPLv2
cp /build/x264-c24e06c2e184345ceb33eb20a15d1024d9fd3497/COPYING /output/sources/x264-COPYING
cp /build/zlib-1.3.1/LICENSE /output/sources/zlib-LICENSE
cp ffbuild/config.log /output/sources/ffmpeg-config.log
rm -rf /output/include /output/lib /output/share
