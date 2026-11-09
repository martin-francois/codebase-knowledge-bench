#!/usr/bin/env python3
"""Reissue a compact publication released before the canary as a new canary-carrying revision.

The input publication is read through its own checksums and hash bindings and is not modified.
The new revision adds the benchmark canary and records the replaced revision's hashes under
`provenance.canaryReissue`; every other field stays as it was. The shared writer then fails
unless every written file carries the canary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark_canary import CANARY_FIELD, with_canary
from build_publication import (
    compress_research,
    sha256_bytes,
    sha256_file,
    write_compact_publication,
)
from merge_publication_extension import read_compact_publication

REISSUE_FIELD_GUIDE = (
    "Hashes of the publication revision this one replaces. The replacement only added the "
    "benchmark contamination canary; run data and findings are unchanged."
)


def reissue_with_canary(
    source: Path,
) -> tuple[dict[str, Any], dict[str, Any], bytes, str]:
    manifest, research = read_compact_publication(source)
    if CANARY_FIELD in research or CANARY_FIELD in manifest:
        raise SystemExit("the input publication already carries the canary")
    previous = {
        "manifestSha256": sha256_file(source / "publication-manifest.json"),
        "checksumsSha256": sha256_file(source / "SHA256SUMS"),
        "resultDataSha256": manifest["resultDataSha256"],
        "resultDataBytes": manifest["resultDataBytes"],
        "compressedResearchDataPath": manifest["compressedResearchData"]["path"],
        "compressedResearchDataSha256": manifest["compressedResearchData"]["sha256"],
        "compressedResearchDataBytes": manifest["compressedResearchData"]["bytes"],
    }
    research = with_canary(research)
    research["provenance"] = {
        **research["provenance"],
        "canaryReissue": {"previousRevision": previous, "runDataChanged": False},
    }
    research["fieldGuide"] = {
        **research["fieldGuide"],
        "provenance.canaryReissue": REISSUE_FIELD_GUIDE,
    }
    research_bytes, compressed_bytes, compressed_name = compress_research(research)
    for forbidden in (b"/home/", b"/root/"):
        if forbidden in research_bytes:
            raise SystemExit("reissued research data contains a private absolute host path")
    reissued_manifest = with_canary(
        {
            **manifest,
            "resultDataSha256": sha256_bytes(research_bytes),
            "resultDataBytes": len(research_bytes),
            "compressedResearchData": {
                **manifest["compressedResearchData"],
                "path": compressed_name,
                "sha256": sha256_bytes(compressed_bytes),
                "bytes": len(compressed_bytes),
            },
        }
    )
    return research, reissued_manifest, compressed_bytes, compressed_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_publication", type=Path)
    parser.add_argument("output_directory", type=Path)
    arguments = parser.parse_args()
    source = arguments.source_publication.resolve()
    output = arguments.output_directory.resolve()
    # The writer clears research-data files from the output, which must not be source files.
    if output.is_relative_to(source):
        raise SystemExit("write the reissued revision outside the source publication")
    research, manifest, compressed_bytes, compressed_name = reissue_with_canary(source)
    write_compact_publication(output, research, manifest, compressed_bytes, compressed_name)
    print(
        json.dumps(
            {
                "previousRevision": research["provenance"]["canaryReissue"]["previousRevision"],
                "resultDataSha256": manifest["resultDataSha256"],
                "compressedResearchData": manifest["compressedResearchData"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
