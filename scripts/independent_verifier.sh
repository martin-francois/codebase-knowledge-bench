#!/bin/sh
set -eu

unset LD_LIBRARY_PATH PYTHONPATH JAVA_HOME NODE_PATH

if [ "$#" -ne 2 ]; then
  echo "usage: independent-verifier-bootstrap independent-verifier.sh OUTER_ZIP OUTPUT_ROOT" >&2
  exit 64
fi

OUTER=$1
OUTPUT=$2
READLINK=$(command -v readlink) || {
  echo "host readlink is required" >&2
  exit 66
}
READLINK=$("$READLINK" -f "$READLINK") || {
  echo "host readlink lacks required -f capability" >&2
  exit 66
}
UNZIP=$(command -v unzip) || {
  echo "host unzip is required" >&2
  exit 66
}
UNZIP=$("$READLINK" -f "$UNZIP") || {
  echo "host unzip path cannot be resolved" >&2
  exit 66
}
MKDIR=$(command -v mkdir) || {
  echo "host mkdir is required" >&2
  exit 66
}
MKDIR=$("$READLINK" -f "$MKDIR") || {
  echo "host mkdir path cannot be resolved" >&2
  exit 66
}
CHMOD=$(command -v chmod) || {
  echo "host chmod is required" >&2
  exit 66
}
CHMOD=$("$READLINK" -f "$CHMOD") || {
  echo "host chmod path cannot be resolved" >&2
  exit 66
}
MKTEMP=$(command -v mktemp) || {
  echo "host mktemp is required" >&2
  exit 66
}
MKTEMP=$("$READLINK" -f "$MKTEMP") || {
  echo "host mktemp path cannot be resolved" >&2
  exit 66
}
GETCONF=$(command -v getconf) || {
  echo "host getconf is required for userspace identity" >&2
  exit 66
}
GETCONF=$("$READLINK" -f "$GETCONF") || {
  echo "host getconf path cannot be resolved" >&2
  exit 66
}
UNAME=$(command -v uname) || {
  echo "host uname is required for kernel identity" >&2
  exit 66
}
UNAME=$("$READLINK" -f "$UNAME") || {
  echo "host uname path cannot be resolved" >&2
  exit 66
}
SHELL_PATH=${INDEPENDENT_VERIFIER_SHELL_PATH:-}
if [ -z "$SHELL_PATH" ]; then
  SHELL_PATH=$(command -v sh) || {
    echo "host shell is required" >&2
    exit 66
  }
fi
SHELL_PATH=$("$READLINK" -f "$SHELL_PATH") || {
  echo "host shell path cannot be resolved" >&2
  exit 66
}
HOST_USERSPACE_DISTRIBUTION=unknown
if [ -r /etc/os-release ]; then
  ID=
  VERSION_ID=
  # The distribution-owned os-release file is the authoritative userspace ID.
  . /etc/os-release
  HOST_USERSPACE_DISTRIBUTION="${ID:-unknown} ${VERSION_ID:-unknown}"
fi
HOST_USERSPACE_GLIBC=unknown
HOST_USERSPACE_GLIBC=$("$GETCONF" GNU_LIBC_VERSION 2>/dev/null || true)
HOST_USERSPACE_GLIBC=${HOST_USERSPACE_GLIBC#glibc }
HOST_KERNEL=$("$UNAME" -srmo 2>/dev/null || "$UNAME" -sr 2>/dev/null || echo unknown)
STAGE=$("$MKTEMP" -d \
  "${TMPDIR:-/tmp}/independent-verifier-bootstrap.XXXXXX")
"$MKDIR" -p "$STAGE/inner"

INNER="$STAGE/review-handoff.zip"
if ! "$UNZIP" -p "$OUTER" \
  review-handoff/review-handoff.zip >"$INNER"
then
  echo "host unzip lacks required exact-name -p streaming" >&2
  exit 66
fi

stream_member() {
  member=$1
  mode=$2
  target="$STAGE/inner/$member"
  parent=${target%/*}
  "$MKDIR" -p "$parent"
  if ! "$UNZIP" -p "$INNER" "$member" >"$target"
  then
    echo "required bootstrap member is missing: $member" >&2
    exit 65
  fi
  "$CHMOD" "$mode" "$target"
}

stream_member runtime/bootstrap-python/bin/python3.14 755
stream_member runtime/bootstrap-python/lib/libpython3.14.so.1.0 755
stream_member runtime/bootstrap-python/lib/python314.zip 644
stream_member \
  runtime/bootstrap-python/system-libs/ld-linux-x86-64.so.2 755
stream_member runtime/bootstrap-python/system-libs/libc.so.6 755
stream_member runtime/bootstrap-python/system-libs/libdl.so.2 755
stream_member runtime/bootstrap-python/system-libs/libm.so.6 755
stream_member runtime/bootstrap-python/system-libs/libpthread.so.0 755
stream_member runtime/bootstrap-python/system-libs/librt.so.1 755
stream_member runtime/bootstrap-python/system-libs/libutil.so.1 755
stream_member \
  verification/independent-verifier/independent_verifier.py 644

PREFIX="$STAGE/inner/runtime/bootstrap-python"
LOADER="$PREFIX/system-libs/ld-linux-x86-64.so.2"
LIBRARIES="$PREFIX/system-libs:$PREFIX/lib"
PYTHON="$PREFIX/bin/python3.14"
VERIFIER="$STAGE/inner/verification/independent-verifier/independent_verifier.py"

export INDEPENDENT_VERIFIER_BOOTSTRAP
: "${INDEPENDENT_VERIFIER_BOOTSTRAP:=sanitized POSIX shell; exact-name unzip streaming; packaged ELF loader}"
export INDEPENDENT_VERIFIER_UNZIP_PATH="$UNZIP"
export INDEPENDENT_VERIFIER_SHELL_PATH="$SHELL_PATH"
export INDEPENDENT_VERIFIER_MKDIR_PATH="$MKDIR"
export INDEPENDENT_VERIFIER_CHMOD_PATH="$CHMOD"
export INDEPENDENT_VERIFIER_MKTEMP_PATH="$MKTEMP"
export INDEPENDENT_VERIFIER_READLINK_PATH="$READLINK"
export INDEPENDENT_VERIFIER_GETCONF_PATH="$GETCONF"
export INDEPENDENT_VERIFIER_UNAME_PATH="$UNAME"
export INDEPENDENT_VERIFIER_BOOTSTRAP_STAGE="$STAGE"
export INDEPENDENT_VERIFIER_HOST_USERSPACE_DISTRIBUTION="$HOST_USERSPACE_DISTRIBUTION"
export INDEPENDENT_VERIFIER_HOST_USERSPACE_GLIBC="$HOST_USERSPACE_GLIBC"
export INDEPENDENT_VERIFIER_HOST_KERNEL="$HOST_KERNEL"
export INDEPENDENT_VERIFIER_PACKAGED_LOADER="$LOADER"
export PYTHONDONTWRITEBYTECODE=1

PYTHONHOME="$PREFIX" exec "$LOADER" \
  --library-path "$LIBRARIES" \
  "$PYTHON" "$VERIFIER" \
  --outer "$OUTER" \
  --output "$OUTPUT"
