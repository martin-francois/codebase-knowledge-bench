#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: independent_verifier.sh OUTER_DELIVERY_ZIP EMPTY_OUTPUT_ROOT" >&2
  exit 64
fi

OUTER=$1
OUTPUT=$2

if [[ -e "$OUTPUT" && -n "$(find "$OUTPUT" -mindepth 1 -print -quit)" ]]; then
  echo "independent verifier output must be empty" >&2
  exit 64
fi

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/independent-verifier-bootstrap.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/inner"

validate_names() {
  local archive=$1
  local maximum=$2
  local names=$3
  unzip -Z1 "$archive" >"$names"
  local count
  count=$(wc -l <"$names")
  if (( count == 0 || count > maximum )); then
    echo "ZIP member-count boundary failed" >&2
    exit 65
  fi
  if awk '
    $0 == "" || $0 ~ /^\// || $0 ~ /\\/ ||
    $0 ~ /[[:space:][:cntrl:]]/ ||
    index($0, "*") || index($0, "?") ||
    index($0, "[") || index($0, "]") ||
    $0 == "." || $0 == ".." ||
    $0 ~ /(^|\/)\.\.(\/|$)/ || $0 ~ /(^|\/)\.(\/|$)/ {
      bad = 1
    }
    END { exit bad ? 0 : 1 }
  ' "$names"; then
    echo "unsafe ZIP member name" >&2
    exit 65
  fi
  sed 's#/*$##' "$names" >"$names.normalized"
  if [[ -n "$(LC_ALL=C sort "$names.normalized" | uniq -d)" ]]; then
    echo "duplicate ZIP member name" >&2
    exit 65
  fi
  if [[ -n "$(LC_ALL=C tr '[:upper:]' '[:lower:]' <"$names.normalized" | sort | uniq -d)" ]]; then
    echo "case-fold ZIP member collision" >&2
    exit 65
  fi
  if awk '
    {
      raw = $0
      directory = raw ~ /\/$/
      sub(/\/+$/, "", raw)
      paths[raw] = directory
    }
    END {
      for (path in paths) {
        count = split(path, parts, "/")
        parent = ""
        for (component_index = 1; component_index < count; component_index += 1) {
          if (parent == "") {
            parent = parts[component_index]
          } else {
            parent = parent "/" parts[component_index]
          }
          if (parent in paths && !paths[parent]) {
            exit 1
          }
        }
      }
    }
  ' "$names"; then
    :
  else
    echo "ZIP file/directory collision" >&2
    exit 65
  fi
}

validate_sizes() {
  local archive=$1
  local maximum_members=$2
  local maximum_member_bytes=$3
  unzip -l "$archive" | awk \
    -v maximum_members="$maximum_members" \
    -v maximum_member_bytes="$maximum_member_bytes" '
    NR > 3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9][0-9][0-9][0-9]-/ {
      if ($1 > maximum_member_bytes) {
        exit 2
      }
      total += $1
      count += 1
    }
    END {
      if (total > 1600000000 || count > maximum_members) {
        exit 3
      }
    }
  '
}

validate_ratios() {
  local archive=$1
  zipinfo -l "$archive" | awk '
    $1 ~ /^[-dl]/ && $4 ~ /^[0-9]+$/ && $6 ~ /^[0-9]+$/ {
      if (($4 > 0 && $6 == 0) || ($6 > 0 && $4 / $6 > 200)) {
        exit 2
      }
    }
  '
}

validate_names "$OUTER" 10 "$STAGE/outer-names"
validate_sizes "$OUTER" 10 1500000000
validate_ratios "$OUTER"
EXPECTED_OUTER=$(printf '%s\n' \
  agent-response.md \
  delivery-manifest.json \
  delivery-validation.json \
  independent-verifier.sh \
  review-handoff/review-handoff.zip \
  review-handoff/review-handoff.zip.sha256 \
  review-handoff/review-handoff.zip.validation.json | LC_ALL=C sort)
if [[ "$(LC_ALL=C sort "$STAGE/outer-names")" != "$EXPECTED_OUTER" ]]; then
  echo "outer delivery member set mismatch" >&2
  exit 65
fi

INNER="$STAGE/review-handoff.zip"
unzip -p "$OUTER" review-handoff/review-handoff.zip >"$INNER"
EXPECTED_SHA=$(
  unzip -p "$OUTER" review-handoff/review-handoff.zip.sha256 |
    awk 'NR == 1 { print $1 }'
)
if [[ "$(sha256sum "$INNER" | awk '{print $1}')" != "$EXPECTED_SHA" ]]; then
  echo "inner detached checksum mismatch" >&2
  exit 65
fi

validate_names "$INNER" 20000 "$STAGE/inner-names"
validate_sizes "$INNER" 20000 300000000
validate_ratios "$INNER"

# Bootstrap without asking a generic ZIP extractor to materialize links or
# paths. Each already name-validated regular member is streamed to one
# explicitly constructed destination. The packaged Python then performs the
# complete manifest/type/mode/link validation before qualification.
zipinfo -l "$INNER" | awk '
  $1 ~ /^-/ && $NF ~ /^runtime\/bootstrap-python\// {
    print $1 "\t" $NF
  }
  $NF == "verification/independent-verifier/independent_verifier.py" {
    if ($1 ~ /^-/) {
      print $1 "\t" $NF
    }
  }
' >"$STAGE/bootstrap-regular-files"
for required in \
  runtime/bootstrap-python/bin/python3.14 \
  verification/independent-verifier/independent_verifier.py
do
  if ! awk -F '\t' -v required="$required" \
    '$2 == required { found += 1 } END { exit found == 1 ? 0 : 1 }' \
    "$STAGE/bootstrap-regular-files"
  then
    echo "required bootstrap regular file is missing or duplicated" >&2
    exit 65
  fi
done
while IFS=$'\t' read -r permissions member
do
  target="$STAGE/inner/$member"
  mkdir -p "$(dirname "$target")"
  unzip -p "$INNER" "$member" >"$target"
  case "$permissions" in
    *x*) chmod 755 "$target" ;;
    *) chmod 644 "$target" ;;
  esac
done <"$STAGE/bootstrap-regular-files"

BOOTSTRAP="$STAGE/inner/runtime/bootstrap-python/bin/python3.14"
if [[ ! -x "$BOOTSTRAP" ]]; then
  echo "packaged verifier Python is not executable" >&2
  exit 65
fi
export LD_LIBRARY_PATH="$STAGE/inner/runtime/bootstrap-python/system-libs"
export PYTHONDONTWRITEBYTECODE=1
export INDEPENDENT_VERIFIER_BOOTSTRAP=\
"outer-only launcher; bounded ZIP bootstrap; packaged Python"
export INDEPENDENT_VERIFIER_UNZIP_PATH
INDEPENDENT_VERIFIER_UNZIP_PATH=$(command -v unzip)
export INDEPENDENT_VERIFIER_UNZIP_SHA256
INDEPENDENT_VERIFIER_UNZIP_SHA256=$(
  sha256sum "$INDEPENDENT_VERIFIER_UNZIP_PATH" | awk '{print $1}'
)
export INDEPENDENT_VERIFIER_ZIPINFO_PATH
INDEPENDENT_VERIFIER_ZIPINFO_PATH=$(command -v zipinfo)
export INDEPENDENT_VERIFIER_ZIPINFO_SHA256
INDEPENDENT_VERIFIER_ZIPINFO_SHA256=$(
  sha256sum "$INDEPENDENT_VERIFIER_ZIPINFO_PATH" | awk '{print $1}'
)
exec "$BOOTSTRAP" \
  "$STAGE/inner/verification/independent-verifier/independent_verifier.py" \
  --outer "$OUTER" \
  --output "$OUTPUT"
