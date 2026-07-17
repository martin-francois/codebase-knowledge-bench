#!/bin/sh
set -eu

unset LD_LIBRARY_PATH PYTHONPATH JAVA_HOME NODE_PATH

if [ "$#" -ne 2 ]; then
  echo "usage: independent_verifier.sh OUTER_DELIVERY_ZIP EMPTY_OUTPUT_ROOT" >&2
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
SHELL_PATH=$("$READLINK" -f "/proc/$$/exe") || {
  echo "host readlink lacks required -f capability" >&2
  exit 66
}
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
INDEPENDENT_VERIFIER_BOOTSTRAP=\
"sanitized POSIX shell; exact-name unzip streaming; packaged ELF loader"
export INDEPENDENT_VERIFIER_UNZIP_PATH="$UNZIP"
export INDEPENDENT_VERIFIER_SHELL_PATH="$SHELL_PATH"
export INDEPENDENT_VERIFIER_MKDIR_PATH="$MKDIR"
export INDEPENDENT_VERIFIER_CHMOD_PATH="$CHMOD"
export INDEPENDENT_VERIFIER_MKTEMP_PATH="$MKTEMP"
export INDEPENDENT_VERIFIER_READLINK_PATH="$READLINK"
export INDEPENDENT_VERIFIER_BOOTSTRAP_STAGE="$STAGE"
export PYTHONDONTWRITEBYTECODE=1

PYTHONHOME="$PREFIX" exec "$LOADER" \
  --library-path "$LIBRARIES" \
  "$PYTHON" "$VERIFIER" \
  --outer "$OUTER" \
  --output "$OUTPUT"
