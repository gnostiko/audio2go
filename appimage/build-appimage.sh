#!/usr/bin/env bash
# Build a portable audio2go AppImage (x86_64).
#
# Usage (from repo root):
#   ./appimage/build-appimage.sh
#
# Output:
#   dist/audio2go-VERSION-x86_64.AppImage
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_ID="org.audio2go.Audio2Go"
APP_NAME="audio2go"
VERSION="$(sed -n "s/^APPLICATION_VERSION = '\\(.*\\)'/\\1/p" src/audio2go/config.py | head -1)"
ARCH="$(uname -m)"
APPDIR="${ROOT}/build-appimage/AppDir"
DIST="${ROOT}/dist"
TOOLS="${ROOT}/.tools"
OUT_NAME="${APP_NAME}-${VERSION}-${ARCH}.AppImage"

if [ -z "$VERSION" ]; then
  echo "error: could not read APPLICATION_VERSION from src/audio2go/config.py" >&2
  exit 1
fi

GH_REPO="${GITHUB_REPOSITORY:-gnostiko/audio2go}"
GH_OWNER="${GH_REPO%%/*}"
GH_NAME="${GH_REPO#*/}"
ZSYNC_ASSET="${APP_NAME}-*-${ARCH}.AppImage.zsync"
UPDATE_INFORMATION="gh-releases-zsync|${GH_OWNER}|${GH_NAME}|latest|${ZSYNC_ASSET}"
export UPDATE_INFORMATION
export UPD_INFO="${UPDATE_INFORMATION}"

mkdir -p "$TOOLS" "$DIST"

echo "==> audio2go AppImage ${VERSION} (${ARCH})"
echo "    AppDir: ${APPDIR}"
echo "    Update: ${UPDATE_INFORMATION}"

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: missing required tool: $1" >&2
    exit 1
  fi
}
need python3
need gcc
need curl
need ffmpeg
if ! command -v pip3 >/dev/null 2>&1 && ! python3 -m pip --version >/dev/null 2>&1; then
  echo "error: missing pip" >&2
  exit 1
fi
if ! command -v glib-compile-schemas >/dev/null 2>&1; then
  echo "error: glib-compile-schemas required" >&2
  exit 1
fi
if ! command -v zsyncmake >/dev/null 2>&1; then
  echo "warning: zsyncmake not found — install package 'zsync' for Gear Lever delta updates" >&2
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MINOR="$(python3 -c 'import sys; print(sys.version_info.minor)')"
if [ "$PY_MINOR" -lt 11 ]; then
  echo "error: Python 3.11+ required (got ${PY_VER})" >&2
  exit 1
fi

download() {
  local url="$1" dest="$2"
  if [ -x "$dest" ]; then
    return 0
  fi
  echo "==> downloading $(basename "$dest")"
  curl -fL --retry 3 -o "${dest}.partial" "$url"
  mv "${dest}.partial" "$dest"
  chmod +x "$dest"
}

if [ "$ARCH" != "x86_64" ]; then
  echo "error: x86_64 AppImages only (got ${ARCH})" >&2
  exit 1
fi

download \
  "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" \
  "${TOOLS}/linuxdeploy"
download \
  "https://github.com/linuxdeploy/linuxdeploy-plugin-gtk/raw/master/linuxdeploy-plugin-gtk.sh" \
  "${TOOLS}/linuxdeploy-plugin-gtk.sh"
download \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
  "${TOOLS}/appimagetool"

export APPIMAGE_EXTRACT_AND_RUN=1

# ---------------------------------------------------------------------------
# Layout AppDir (no Meson — copy the Python package, schema, and icons)
# ---------------------------------------------------------------------------
echo "==> populate AppDir"
rm -rf "$APPDIR"
mkdir -p \
  "${APPDIR}/usr/bin" \
  "${APPDIR}/usr/lib" \
  "${APPDIR}/usr/lib/girepository-1.0" \
  "${APPDIR}/usr/lib/python3/site-packages" \
  "${APPDIR}/usr/share/audio2go" \
  "${APPDIR}/usr/share/applications" \
  "${APPDIR}/usr/share/glib-2.0/schemas" \
  "${APPDIR}/usr/share/icons/hicolor/scalable/apps" \
  "${APPDIR}/usr/share/icons/hicolor/symbolic/apps"

cp -a "${ROOT}/src/audio2go" "${APPDIR}/usr/share/audio2go/audio2go"
find "${APPDIR}/usr/share/audio2go" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

install -D -m644 "${ROOT}/data/org.audio2go.Audio2Go.gschema.xml" \
  "${APPDIR}/usr/share/glib-2.0/schemas/org.audio2go.Audio2Go.gschema.xml"
glib-compile-schemas "${APPDIR}/usr/share/glib-2.0/schemas"

install -D -m644 \
  "${ROOT}/data/icons/hicolor/scalable/apps/${APP_ID}.svg" \
  "${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
if [ -f "${ROOT}/data/icons/hicolor/symbolic/apps/${APP_ID}-symbolic.svg" ]; then
  install -D -m644 \
    "${ROOT}/data/icons/hicolor/symbolic/apps/${APP_ID}-symbolic.svg" \
    "${APPDIR}/usr/share/icons/hicolor/symbolic/apps/${APP_ID}-symbolic.svg"
fi

cat > "${APPDIR}/usr/bin/audio2go" <<'EOF'
#!/bin/sh
exec python3 -B -m audio2go.main "$@"
EOF
chmod +x "${APPDIR}/usr/bin/audio2go"

# ---------------------------------------------------------------------------
# Python deps
# ---------------------------------------------------------------------------
echo "==> pip install mutagen pillow"
python3 -m pip install --upgrade --no-compile \
  --target="${APPDIR}/usr/lib/python3/site-packages" \
  "mutagen>=1.47" \
  "pillow>=10.0.0"

echo "==> bundling PyGObject (gi)"
python3 - "$APPDIR" <<'PY'
import shutil
import sys
from pathlib import Path

appdir = Path(sys.argv[1])
try:
    import gi
except ImportError as exc:
    print(f"error: PyGObject (gi) not installed on build host: {exc}", file=sys.stderr)
    sys.exit(1)

src_root = Path(gi.__file__).resolve().parent
site = src_root.parent
dest_site = appdir / "usr/lib/python3/site-packages"
dest_site.mkdir(parents=True, exist_ok=True)

dst_gi = dest_site / "gi"
if dst_gi.exists():
    shutil.rmtree(dst_gi)
shutil.copytree(src_root, dst_gi, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
print(f"    gi → {dst_gi}")

for extra in ("pygtkcompat",):
    src = site / extra
    if src.is_dir():
        dst = dest_site / extra
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"    {extra} → {dst}")
PY

# ---------------------------------------------------------------------------
# GI typelibs
# ---------------------------------------------------------------------------
echo "==> bundling GObject Introspection typelibs"
TYPELIB_DIRS=()
for d in \
  /usr/lib/girepository-1.0 \
  /usr/lib64/girepository-1.0 \
  /usr/lib/x86_64-linux-gnu/girepository-1.0
do
  [ -d "$d" ] && TYPELIB_DIRS+=("$d")
done
if [ "${#TYPELIB_DIRS[@]}" -eq 0 ]; then
  echo "error: no girepository-1.0 directory on build host" >&2
  exit 1
fi

REQUIRED_TYPELIBS=(
  Gtk-4.0 Adw-1 Gdk-4.0 Gsk-4.0
  GLib-2.0 GObject-2.0 Gio-2.0 GModule-2.0
  GdkPixbuf-2.0 Graphene-1.0 HarfBuzz-0.0
  Pango-1.0 PangoCairo-1.0
  cairo-1.0 freetype2-2.0 fontconfig-2.0
  xlib-2.0
  GIRepository-2.0
)
OPTIONAL_TYPELIBS=(
  GIRepository-3.0
  GdkWayland-4.0 GdkX11-4.0
  Soup-3.0 Soup-2.4 Json-1.0
)

copy_typelib() {
  local name="$1" required="$2"
  local found=""
  for dir in "${TYPELIB_DIRS[@]}"; do
    if [ -f "${dir}/${name}.typelib" ]; then
      found="${dir}/${name}.typelib"
      break
    fi
  done
  if [ -n "$found" ]; then
    install -D -m644 "$found" \
      "${APPDIR}/usr/lib/girepository-1.0/${name}.typelib"
    return 0
  fi
  if [ "$required" = "1" ]; then
    echo "error: missing required typelib ${name}.typelib" >&2
    return 1
  fi
  echo "    optional typelib missing: ${name}"
  return 0
}

for t in "${REQUIRED_TYPELIBS[@]}"; do
  copy_typelib "$t" 1
done
for t in "${OPTIONAL_TYPELIBS[@]}"; do
  copy_typelib "$t" 0 || true
done

# ---------------------------------------------------------------------------
# Shared library helpers
# ---------------------------------------------------------------------------
copy_lib() {
  local src="$1"
  [ -f "$src" ] || return 1
  local real
  real="$(readlink -f "$src" 2>/dev/null || echo "$src")"
  [ -f "$real" ] || real="$src"
  local base
  base="$(basename "$real")"
  if [ ! -e "${APPDIR}/usr/lib/${base}" ]; then
    install -D -m755 "$real" "${APPDIR}/usr/lib/${base}" 2>/dev/null || return 1
  fi
  return 0
}

resolve_soname() {
  local soname="$1"
  local path=""
  path="$(ldconfig -p 2>/dev/null | awk -v n="$soname" '$1==n {print $NF; exit}' || true)"
  if [ -n "$path" ] && [ -f "$path" ]; then
    printf '%s\n' "$path"
    return 0
  fi
  local cand
  for cand in \
    "/usr/lib/${soname}" \
    "/usr/lib64/${soname}" \
    "/usr/lib/x86_64-linux-gnu/${soname}"
  do
    if [ -e "$cand" ]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  return 1
}

ensure_soname_links() {
  local libdir="${APPDIR}/usr/lib"
  [ -d "$libdir" ] || return 0
  local f soname base
  for f in "$libdir"/lib*.so*; do
    [ -f "$f" ] || continue
    [ -L "$f" ] && continue
    base="$(basename "$f")"
    soname="$(readelf -d "$f" 2>/dev/null | awk -F'[][]' '/SONAME/ {print $2; exit}' || true)"
    if [ -n "$soname" ] && [ "$soname" != "$base" ]; then
      if [ ! -e "${libdir}/${soname}" ]; then
        ln -sfn "$base" "${libdir}/${soname}"
      fi
    fi
    case "$base" in
      *.so.[0-9]*.[0-9]*)
        local short
        short="$(echo "$base" | sed -E 's/(\.so\.[0-9]+)\..*/\1/')"
        if [ -n "$short" ] && [ "$short" != "$base" ] && [ ! -e "${libdir}/${short}" ]; then
          ln -sfn "$base" "${libdir}/${short}"
        fi
        ;;
    esac
  done
}

bundle_ldd_deps() {
  local real
  real="$(readlink -f "$1")"
  ldd "$real" 2>/dev/null | awk '
    /=> \// { print $3 }
    /^\t\// { print $1 }
  ' | while read -r lib; do
    [ -n "$lib" ] || continue
    [ -f "$lib" ] || continue
    case "$lib" in
      *ld-linux*) continue ;;
      *linux-vdso*) continue ;;
      */libc.so.*) continue ;;
      */libm.so.*) continue ;;
      */libdl.so.*) continue ;;
      */librt.so.*) continue ;;
      */libpthread.so.*) continue ;;
    esac
    copy_lib "$lib" || true
  done
}

bundle_python_stdlib() {
  local pyver platlib
  pyver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  platlib="$(python3 -c 'import sys; print(sys.platlibdir)')"
  local src=""
  for cand in \
    "/usr/${platlib}/python${pyver}" \
    "/usr/lib64/python${pyver}" \
    "/usr/lib/python${pyver}"
  do
    if [ -f "${cand}/encodings/__init__.py" ] || [ -f "${cand}/os.py" ]; then
      src="$cand"
      break
    fi
  done
  if [ -z "$src" ]; then
    echo "error: Python stdlib not found under /usr/{lib,lib64}/python${pyver}" >&2
    return 1
  fi
  local dest="${APPDIR}/usr/${platlib}/python${pyver}"
  echo "==> bundling Python ${pyver} stdlib from ${src}"
  rm -rf "$dest"
  mkdir -p "$dest"
  python3 - "$src" "$dest" <<'PY'
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
EXCLUDE_DIR_NAMES = {
    "__pycache__", "test", "tests", "idle_test", "idlelib", "turtledemo",
    "tkinter", "ensurepip", "pydoc_data", "site-packages", "dist-packages",
}
EXCLUDE_DIR_PREFIXES = ("config-",)
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".o", ".a")

def ignore(_directory, names):
    skipped = []
    for name in names:
        if name in EXCLUDE_DIR_NAMES:
            skipped.append(name)
            continue
        if any(name.startswith(p) for p in EXCLUDE_DIR_PREFIXES):
            skipped.append(name)
            continue
        if name.endswith(EXCLUDE_SUFFIXES) or name.endswith("-gdb.py"):
            skipped.append(name)
    return skipped

shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore, symlinks=True)
(dst / "site-packages").mkdir(parents=True, exist_ok=True)
print(f"    python stdlib → {dst}")
PY
  if [ "$platlib" = "lib64" ]; then
    mkdir -p "${APPDIR}/usr/lib"
    ln -sfn "../lib64/python${pyver}" "${APPDIR}/usr/lib/python${pyver}"
  else
    mkdir -p "${APPDIR}/usr/lib64"
    ln -sfn "../lib/python${pyver}" "${APPDIR}/usr/lib64/python${pyver}"
  fi
  if [ -d "${dest}/lib-dynload" ]; then
    find "${dest}/lib-dynload" -name '*.so' 2>/dev/null | while read -r so; do
      bundle_ldd_deps "$so" || true
    done
  fi
}

copy_bin_with_libs() {
  local src="$1"
  local dest_name="${2:-$(basename "$src")}"
  if [ ! -x "$src" ]; then
    return 1
  fi
  echo "    bundling $src → usr/bin/${dest_name}"
  install -D -m755 "$src" "${APPDIR}/usr/bin/${dest_name}"
  bundle_ldd_deps "$src"
  return 0
}

echo "==> bundling helper binaries + libraries"
find "${APPDIR}/usr/lib/python3/site-packages/gi" -name '*.so' 2>/dev/null | while read -r so; do
  bundle_ldd_deps "$so" || true
done

HOST_PYTHON="$(readlink -f "$(command -v python3)")"
copy_bin_with_libs "$HOST_PYTHON" "python3" || true
if [ -x "/usr/bin/python${PY_VER}" ]; then
  copy_bin_with_libs "/usr/bin/python${PY_VER}" "python${PY_VER}" || true
fi
if [ ! -e "${APPDIR}/usr/bin/python3" ] && [ -x "${APPDIR}/usr/bin/python${PY_VER}" ]; then
  ln -sfn "python${PY_VER}" "${APPDIR}/usr/bin/python3"
fi

for lib in /usr/lib/libpython3*.so* /usr/lib64/libpython3*.so* /usr/lib/x86_64-linux-gnu/libpython3*.so*; do
  [ -f "$lib" ] || continue
  copy_lib "$lib" || true
done

bundle_python_stdlib

copy_bin_with_libs "$(command -v ffmpeg)" ffmpeg
if command -v ffprobe >/dev/null 2>&1; then
  copy_bin_with_libs "$(command -v ffprobe)" ffprobe || true
fi

echo "==> bundling GTK 4 / libadwaita libraries"
for soname in libgtk-4.so.1 libadwaita-1.so.0 libgdk_pixbuf-2.0.so.0 \
  libpango-1.0.so.0 libpangocairo-1.0.so.0 libgobject-2.0.so.0 \
  libglib-2.0.so.0 libgio-2.0.so.0 libgmodule-2.0.so.0 \
  libgraphene-1.0.so.0 libharfbuzz.so.0 libepoxy.so.0 \
  libcairo.so.2 libfribidi.so.0 libcloudproviders.so.0 \
  libgirepository-2.0.so.0 libgirepository-1.0.so.1 \
  libffi.so.8 libpcre2-8.so.0; do
  path="$(resolve_soname "$soname" || true)"
  if [ -n "$path" ] && [ -f "$path" ]; then
    echo "    lib $path"
    copy_lib "$path"
    bundle_ldd_deps "$path"
  else
    echo "    warning: $soname not found on host"
  fi
done

echo "==> bundling gdk-pixbuf loaders"
for pbdir in /usr/lib/gdk-pixbuf-2.0 /usr/lib64/gdk-pixbuf-2.0 \
  /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0; do
  if [ -d "$pbdir" ]; then
    mkdir -p "${APPDIR}/usr/lib"
    cp -a "$pbdir" "${APPDIR}/usr/lib/" 2>/dev/null || true
    find "${APPDIR}/usr/lib/gdk-pixbuf-2.0" -name '*.so' 2>/dev/null | while read -r so; do
      bundle_ldd_deps "$so" || true
    done
    break
  fi
done
if command -v gdk-pixbuf-query-loaders >/dev/null 2>&1; then
  for loaders_dir in "${APPDIR}/usr/lib"/gdk-pixbuf-2.0/*/loaders; do
    [ -d "$loaders_dir" ] || continue
    cache="${loaders_dir}/../loaders.cache"
    GDK_PIXBUF_MODULEDIR="$loaders_dir" \
      gdk-pixbuf-query-loaders >"$cache" 2>/dev/null || true
    if [ -f "$cache" ]; then
      sed -i "s|$APPDIR|./|g" "$cache" 2>/dev/null || true
    fi
  done
fi

find "${APPDIR}/usr/bin" -name 'lib*.so*' -delete 2>/dev/null || true
ensure_soname_links

# ---------------------------------------------------------------------------
# Desktop entry + icon + AppRun
# ---------------------------------------------------------------------------
echo "==> desktop entry and icon"
install -D -m644 "${ROOT}/appimage/${APP_ID}.desktop" \
  "${APPDIR}/${APP_ID}.desktop"
sed -i 's/^Exec=.*/Exec=audio2go/' "${APPDIR}/${APP_ID}.desktop"
install -D -m644 "${ROOT}/appimage/${APP_ID}.desktop" \
  "${APPDIR}/usr/share/applications/${APP_ID}.desktop"
sed -i 's/^Exec=.*/Exec=audio2go/' \
  "${APPDIR}/usr/share/applications/${APP_ID}.desktop"
cp -f "${ROOT}/data/icons/hicolor/scalable/apps/${APP_ID}.svg" \
  "${APPDIR}/${APP_ID}.svg"
install -D -m755 "${ROOT}/appimage/AppRun.in" "${APPDIR}/AppRun"

# ---------------------------------------------------------------------------
# linuxdeploy
# ---------------------------------------------------------------------------
echo "==> linuxdeploy"
export PATH="${TOOLS}:${PATH}"
if [ ! -e "${TOOLS}/linuxdeploy-plugin-gtk" ]; then
  ln -sfn linuxdeploy-plugin-gtk.sh "${TOOLS}/linuxdeploy-plugin-gtk"
fi
export NO_STRIP=1
export DEPLOY_GTK_VERSION=4

find "${APPDIR}" \( -name '*.o' -o -name '*.a' \) -type f -delete 2>/dev/null || true
for junk in \
  "${APPDIR}/usr/lib"/python3.*/site-packages \
  "${APPDIR}/usr/lib64"/python3.*/site-packages
do
  [ -e "$junk" ] || continue
  find "$junk" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
done

LD_LOG="${ROOT}/build-appimage/linuxdeploy.log"
mkdir -p "$(dirname "$LD_LOG")"

USE_GTK_PLUGIN=0
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists gtk4 2>/dev/null; then
  USE_GTK_PLUGIN=1
else
  echo "    note: gtk4.pc not found — skipping linuxdeploy GTK plugin"
fi

run_linuxdeploy() {
  local mode="$1"
  local -a args=(
    --verbosity=2
    --appdir="$APPDIR"
    --executable="${APPDIR}/usr/bin/python3"
    --executable="${APPDIR}/usr/bin/ffmpeg"
    --desktop-file="${APPDIR}/usr/share/applications/${APP_ID}.desktop"
    --icon-file="${APPDIR}/usr/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
  )
  if [ "$mode" = "with-gtk" ]; then
    args+=(--plugin gtk)
  fi
  "${TOOLS}/linuxdeploy" "${args[@]}"
}

set +e
: >"$LD_LOG"
if [ "$USE_GTK_PLUGIN" -eq 1 ]; then
  run_linuxdeploy with-gtk >>"$LD_LOG" 2>&1
  LD_RC=$?
  if [ "$LD_RC" -ne 0 ]; then
    echo "    warning: GTK plugin failed — retrying without it (see ${LD_LOG})" >&2
    {
      echo "----- retry without gtk plugin -----"
      run_linuxdeploy plain
    } >>"$LD_LOG" 2>&1
    LD_RC=$?
  fi
else
  run_linuxdeploy plain >>"$LD_LOG" 2>&1
  LD_RC=$?
fi
set -e
if [ "$LD_RC" -ne 0 ]; then
  echo "warning: linuxdeploy failed; continuing with manual bundles (see ${LD_LOG})" >&2
  grep -E 'ERROR:|error:' "$LD_LOG" 2>/dev/null | tail -5 >&2 || true
else
  echo "    linuxdeploy OK (details: ${LD_LOG})"
fi

install -D -m755 "${ROOT}/appimage/AppRun.in" "${APPDIR}/AppRun"
cp -f "${ROOT}/appimage/${APP_ID}.desktop" "${APPDIR}/${APP_ID}.desktop"
sed -i 's/^Exec=.*/Exec=audio2go/' "${APPDIR}/${APP_ID}.desktop"
cp -f "${ROOT}/data/icons/hicolor/scalable/apps/${APP_ID}.svg" "${APPDIR}/${APP_ID}.svg"
ensure_soname_links

# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------
echo "==> smoke-test AppDir imports (isolated)"
set +e
(
  PY="${APPDIR}/usr/bin/python3"
  [ -x "$PY" ] || {
    echo "error: bundled python3 missing" >&2
    exit 1
  }
  env -i \
    HOME="${HOME:-/tmp}" \
    PATH="${APPDIR}/usr/bin:/usr/bin:/bin" \
    APPDIR="$APPDIR" \
    PYTHONHOME="${APPDIR}/usr" \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH="${APPDIR}/usr/share/audio2go:${APPDIR}/usr/lib/python3/site-packages" \
    LD_LIBRARY_PATH="${APPDIR}/usr/lib" \
    GI_TYPELIB_PATH="${APPDIR}/usr/lib/girepository-1.0" \
    GSETTINGS_SCHEMA_DIR="${APPDIR}/usr/share/glib-2.0/schemas" \
    GDK_PIXBUF_MODULE_FILE="$(echo "${APPDIR}/usr/lib"/gdk-pixbuf-2.0/*/loaders.cache 2>/dev/null | awk '{print $1; exit}')" \
    "$PY" -B - <<'PY'
import sys
from pathlib import Path

print("python", sys.version.split()[0], file=sys.stderr)
stdlib = (
    Path(sys.prefix)
    / sys.platlibdir
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
)
assert "AppDir" in str(stdlib.resolve()) or "build-appimage" in str(stdlib), (
    f"stdlib not from AppDir: {stdlib}"
)
assert (stdlib / "encodings").is_dir(), f"encodings missing under {stdlib}"

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib  # noqa: F401
import mutagen  # noqa: F401
import audio2go.main  # noqa: F401
from audio2go.sync.encode import ffmpeg_bin

ffmpeg = ffmpeg_bin()
assert ffmpeg, "ffmpeg not found on PATH inside AppDir"
print("ok: Gtk/Adw/mutagen/audio2go/ffmpeg", ffmpeg, file=sys.stderr)
PY
)
SMOKE=$?
set -e
if [ "$SMOKE" -ne 0 ]; then
  echo "error: AppDir smoke-test failed — fix packaging before release" >&2
  exit 1
fi

if command -v objdump >/dev/null 2>&1; then
  echo "==> glibc symbol floor (build host libraries)"
  MAX_GLIBC="2.0"
  for f in \
    "${APPDIR}/usr/bin/python3" \
    "${APPDIR}/usr/lib"/libpython3*.so* \
    "${APPDIR}/usr/lib/libgtk-4.so.1" \
    "${APPDIR}/usr/lib/libglib-2.0.so.0" \
    "${APPDIR}/usr/lib/libadwaita-1.so.0"
  do
    [ -e "$f" ] || continue
    ver="$(objdump -T "$f" 2>/dev/null | grep -oE 'GLIBC_[0-9.]+' | sort -uV | tail -1 | sed 's/GLIBC_//')"
    if [ -n "$ver" ]; then
      echo "    $(basename "$f"): GLIBC_${ver}"
      if printf '%s\n' "$MAX_GLIBC" "$ver" | sort -V | tail -1 | grep -qx "$ver"; then
        MAX_GLIBC="$ver"
      fi
    fi
  done
  echo "    → this AppImage needs host glibc >= ${MAX_GLIBC}"
fi

# ---------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------
echo "==> appimagetool (update info for Gear Lever)"
rm -f "${DIST}/${OUT_NAME}" "${DIST}/${OUT_NAME}.zsync"
install -D -m755 "${ROOT}/appimage/AppRun.in" "${APPDIR}/AppRun"
rm -rf "${ROOT}/squashfs-root" 2>/dev/null || true

set +e
ARCH="$ARCH" "${TOOLS}/appimagetool" \
  --no-appstream \
  -u "${UPDATE_INFORMATION}" \
  "$APPDIR" \
  "${DIST}/${OUT_NAME}"
AT_RC=$?
if [ "$AT_RC" -ne 0 ]; then
  echo "warning: appimagetool with -u failed; retrying without update info" >&2
  ARCH="$ARCH" "${TOOLS}/appimagetool" --no-appstream \
    "$APPDIR" "${DIST}/${OUT_NAME}"
  AT_RC=$?
fi
set -e
if [ "$AT_RC" -ne 0 ]; then
  echo "error: appimagetool failed" >&2
  exit 1
fi

chmod +x "${DIST}/${OUT_NAME}"

if [ -f "${DIST}/${OUT_NAME}.zsync" ]; then
  :
elif [ -f "${OUT_NAME}.zsync" ]; then
  mv -f "${OUT_NAME}.zsync" "${DIST}/${OUT_NAME}.zsync"
elif [ -f "${ROOT}/${OUT_NAME}.zsync" ]; then
  mv -f "${ROOT}/${OUT_NAME}.zsync" "${DIST}/${OUT_NAME}.zsync"
fi

if command -v readelf >/dev/null 2>&1; then
  EMBEDDED="$(readelf --string-dump=.upd_info --wide "${DIST}/${OUT_NAME}" 2>/dev/null | tr '\n' ' ' || true)"
  if echo "$EMBEDDED" | grep -q 'gh-releases-zsync'; then
    echo "    embedded .upd_info OK (Gear Lever can auto-detect GitHub updates)"
  else
    echo "    warning: .upd_info not found — Gear Lever needs a manual update URL"
  fi
fi

echo
echo "Built: ${DIST}/${OUT_NAME}"
ls -lh "${DIST}/${OUT_NAME}"
if [ -f "${DIST}/${OUT_NAME}.zsync" ]; then
  ls -lh "${DIST}/${OUT_NAME}.zsync"
fi
echo
echo "Run:"
echo "  ${DIST}/${OUT_NAME}"
