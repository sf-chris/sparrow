#!/bin/sh
# Sparrow Node's own download app for each Linux architecture, without building
# or running anything for that architecture: apk installs Alpine's signed
# Transmission packages into an empty root per architecture (it checks every
# package against Alpine's keys), then only the daemon, the libraries it loads,
# musl's loader and the certificate bundle are kept. The daemon runs through
# that loader, so it needs nothing from the computer it lands on.
set -eu
out=${1:-/output/node-tools}
for arch in x86_64 aarch64; do
  root=/tmp/root-$arch
  mkdir -p "$root"
  apk --root "$root" --arch "$arch" --initdb --no-scripts --no-cache --quiet \
    --keys-dir "/usr/share/apk/keys/$arch" --repositories-file /etc/apk/repositories \
    add transmission-daemon ca-certificates-bundle
  dest="$out/linux-$arch/transmission"
  mkdir -p "$dest/lib" "$dest/libexec"
  cp "$root/usr/bin/transmission-daemon" "$dest/libexec/"
  loader=$(cd "$root/lib" && ls ld-musl-*.so.1)
  cp -L "$root/lib/$loader" "$dest/lib/"
  # Every shared library in the root belongs to the daemon's dependencies.
  for folder in "$root/lib" "$root/usr/lib"; do
    find "$folder" -maxdepth 1 -name '*.so*' \( -type f -o -type l \) | while read -r library; do
      [ "$(basename "$library")" = "$loader" ] && continue
      cp -P "$library" "$dest/lib/"
    done
  done
  cp "$root/etc/ssl/certs/ca-certificates.crt" "$dest/ca-certificates.crt"
  # What was installed, for licences and for telling bundles apart.
  apk --root "$root" --arch "$arch" info -v 2>/dev/null | sort > "$dest/packages.txt"
  mkdir -p "$out/linux-$arch/bin"
  cat > "$out/linux-$arch/bin/transmission-daemon" <<SCRIPT
#!/bin/sh
here=\$(CDPATH= cd -- "\$(dirname -- "\$0")/../transmission" && pwd)
CURL_CA_BUNDLE="\${CURL_CA_BUNDLE:-\$here/ca-certificates.crt}"
export CURL_CA_BUNDLE
exec "\$here/lib/$loader" --library-path "\$here/lib" "\$here/libexec/transmission-daemon" "\$@"
SCRIPT
  chmod 755 "$out/linux-$arch/bin/transmission-daemon"
done
