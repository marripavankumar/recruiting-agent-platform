"""Rebuild the semantic search index from the Candidate and Job databases.

    python -m recruiting.reindex            index everything again
    python -m recruiting.reindex --missing  only if the index has fewer entries than the records

Needed after changing RAP_EMBEDDER or RAP_EMBEDDING_MODEL: each embedder has its own search
collections, so a new one starts empty. The scheduler runs the `--missing` check every hour,
so after a model change the index fills by itself; run this to do it at once. Indexing is an
upsert, so running it twice does no harm. Erased candidates are not in the database, so they
are never indexed again.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recruiting.api.platform import Platform

logger = logging.getLogger(__name__)

SCAN_LIMIT = 100_000


def reindex(platform: Platform, missing_only: bool = False) -> dict[str, int]:
    """Index every candidate and job; with `missing_only`, only the kinds whose index has fewer
    entries than the records. Returns how many of each were indexed."""
    from recruiting.agents.jd_analysis import index_job
    from recruiting.agents.resume_intake import index_candidate

    search = platform.search
    result = {"candidates": 0, "jobs": 0}
    candidates = platform.candidates.list(limit=SCAN_LIMIT)
    if not missing_only or search.count("candidate") < len(candidates):
        for candidate in candidates:
            index_candidate(search, candidate)
            result["candidates"] += 1
    jobs = [j for j in platform.jobs.list() if j.analysis]  # only analysed jobs are searchable
    if not missing_only or search.count("job") < len(jobs):
        for job in jobs:
            index_job(search, job)
            result["jobs"] += 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m recruiting.reindex",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--missing", action="store_true", help="only if entries are missing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    from recruiting.api.services import build_services
    from recruiting.config import Settings

    settings = Settings()
    platform = build_services(settings).platform
    if platform is None:
        print("No records are configured (RAP_STORAGE).", file=sys.stderr)
        return 1
    started = time.monotonic()
    result = reindex(platform, missing_only=args.missing)
    embedder = type(platform.search.embedder).__name__
    print(f"Indexed {result['candidates']} candidates and {result['jobs']} jobs with "
          f"{embedder} in {time.monotonic() - started:.1f}s")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
