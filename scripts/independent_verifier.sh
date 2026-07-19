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

MEMBER_MANIFEST_REL=runtime/bootstrap-python-members.txt
MEMBER_MANIFEST="$STAGE/bootstrap-python-members.txt"
# POSIX ulimit -f is in 512-byte blocks. Bound the untrusted manifest
# before packaged Python is available to validate its exact identity.
ulimit -S -f 128 || {
  echo "cannot enforce bootstrap manifest byte limit" >&2
  exit 66
}
if ! "$UNZIP" -p "$INNER" "$MEMBER_MANIFEST_REL" >"$MEMBER_MANIFEST"
then
  echo "bootstrap member manifest is missing" >&2
  exit 65
fi
ulimit -S -f unlimited || {
  echo "cannot restore bootstrap member file limit" >&2
  exit 66
}

exec 3<"$MEMBER_MANIFEST"
IFS= read -r manifest_header <&3 || {
  echo "bootstrap member manifest is empty" >&2
  exit 65
}
if [ "$manifest_header" != bootstrap-python-members-v1 ]; then
  echo "bootstrap member manifest header mismatch" >&2
  exit 65
fi
member_count=0
total_bytes=0
seen_members='
'
required_python=false
required_libpython=false
required_stdlib=false
required_loader=false
while IFS=' ' read -r mode bytes digest member extra <&3
do
  if [ -n "${extra:-}" ] || [ -z "${member:-}" ]; then
    echo "invalid bootstrap member manifest row" >&2
    exit 65
  fi
  case "$mode" in
    0644|0755) ;;
    *)
      echo "invalid bootstrap member mode: $mode" >&2
      exit 65
      ;;
  esac
  case "$bytes" in
    ''|*[!0-9]*)
      echo "invalid bootstrap member byte count: $bytes" >&2
      exit 65
      ;;
  esac
  if [ "$bytes" -le 0 ] || [ "$bytes" -gt 300000000 ]; then
    echo "bootstrap member byte limit exceeded: $member" >&2
    exit 65
  fi
  case "$digest" in
    *[!0-9a-f]*)
      echo "invalid bootstrap member SHA-256: $member" >&2
      exit 65
      ;;
  esac
  if [ "${#digest}" -ne 64 ]; then
    echo "invalid bootstrap member SHA-256 length: $member" >&2
    exit 65
  fi
  case "$member" in
    /*|../*|*/../*|*/..|*//*|*\\*)
      echo "unsafe bootstrap member path: $member" >&2
      exit 65
      ;;
  esac
  case "$member" in
    runtime/bootstrap-python/bin/*|\
    runtime/bootstrap-python/lib/*|\
    runtime/bootstrap-python/system-libs/*) ;;
    *)
      echo "bootstrap member prefix is not allowed: $member" >&2
      exit 65
      ;;
  esac
  case "$seen_members" in
    *"
$member
"*)
      echo "duplicate bootstrap member: $member" >&2
      exit 65
      ;;
  esac
  seen_members="${seen_members}${member}
"
  member_count=$((member_count + 1))
  total_bytes=$((total_bytes + bytes))
  if [ "$member_count" -gt 128 ] || [ "$total_bytes" -gt 1000000000 ]; then
    echo "bootstrap member contract limit exceeded" >&2
    exit 65
  fi
  blocks=$(((bytes + 511) / 512))
  ulimit -S -f "$blocks" || {
    echo "cannot enforce bootstrap member byte limit: $member" >&2
    exit 66
  }
  stream_member "$member" "${mode#0}"
  case "$member" in
    runtime/bootstrap-python/bin/python3.14)
      required_python=true ;;
    runtime/bootstrap-python/lib/libpython3.14.so.1.0)
      required_libpython=true ;;
    runtime/bootstrap-python/lib/python314.zip)
      required_stdlib=true ;;
    runtime/bootstrap-python/system-libs/ld-linux-x86-64.so.2)
      required_loader=true ;;
  esac
done
exec 3<&-
ulimit -S -f unlimited || {
  echo "cannot restore bootstrap stream file limit" >&2
  exit 66
}
if [ "$required_python" != true ] || \
   [ "$required_libpython" != true ] || \
   [ "$required_stdlib" != true ] || \
   [ "$required_loader" != true ]; then
  echo "bootstrap member manifest lacks a required runtime member" >&2
  exit 65
fi
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
