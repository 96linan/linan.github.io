#!/usr/bin/env python3
"""
Generate a public record of citing papers whose authors are "big-name" scholars.

What it does
------------
1. Reads your own paper DOIs from:
   - _data/publications.yml, if present; otherwise
   - _pages/publications.md, _pages/publications-c.md, _pages/s-publications.md
2. Uses OpenAlex to find papers that cite each of your papers.
3. Checks each citing paper's authors.
4. Records the citing paper if at least one author meets the configurable
   "big-name scholar" thresholds or appears in _data/big_names.yml.

Outputs
-------
- _includes/big_name_citations.md  -> rendered by /big-name-citations/
- assets/data/big_name_citations.csv
- assets/data/big_name_citations.json

Local run
---------
pip install requests pyyaml
python scripts/track_big_name_citations.py
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPENALEX = "https://api.openalex.org"

CONFIG_DEFAULTS: dict[str, Any] = {
    # Author thresholds for "big-name scholar"; tune these for your field.
    "min_author_citations": 10000,
    "min_author_works": 80,
    "min_author_h_index": 35,

    # Use None for all citing works. Use a number, e.g. 500, to keep Actions fast.
    "max_citing_works_per_paper": None,

    # Use None for all years. Example: 2020 only keeps citing papers since 2020.
    "since_year": None,

    # API politeness.
    "sleep_seconds": 0.10,
    "mailto": os.getenv("OPENALEX_MAILTO", "lnn18003x@gmail.com"),
}

SOURCE_PAGES = [
    ROOT / "_pages" / "publications.md",
    ROOT / "_pages" / "publications-c.md",
    ROOT / "_pages" / "s-publications.md",
]

OUT_MD = ROOT / "_includes" / "big_name_citations.md"
OUT_CSV = ROOT / "assets" / "data" / "big_name_citations.csv"
OUT_JSON = ROOT / "assets" / "data" / "big_name_citations.json"
AUTHOR_CACHE = ROOT / "assets" / "data" / "openalex_author_cache.json"


@dataclass
class Hit:
    my_paper_title: str
    my_paper_doi: str
    citing_title: str
    citing_year: int | None
    citing_doi: str
    citing_url: str
    big_author: str
    big_author_openalex_id: str
    big_author_citations: int | None
    big_author_works: int | None
    big_author_h_index: int | None
    match_reason: str


def read_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if data is not None else default


def config() -> dict[str, Any]:
    cfg = dict(CONFIG_DEFAULTS)
    cfg.update(read_yaml(ROOT / "_data" / "citation_tracker.yml", {}))
    return cfg


CFG = config()


def api_get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = dict(params or {})
    if CFG.get("mailto"):
        params["mailto"] = CFG["mailto"]

    last_error: Exception | None = None
    for attempt in range(6):
        try:
            response = requests.get(url, params=params, timeout=45)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(60, 2 ** attempt))
                continue
            response.raise_for_status()
            time.sleep(float(CFG.get("sleep_seconds", 0.1)))
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(min(60, 2 ** attempt))
    raise RuntimeError(f"OpenAlex request failed: {url}") from last_error


def clean_doi(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip().rstrip(".,;)")
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    return value.lower()


def extract_dois_from_markdown() -> list[dict[str, str]]:
    doi_re = re.compile(r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
    pubs: list[dict[str, str]] = []
    seen: set[str] = set()

    for page in SOURCE_PAGES:
        if not page.exists():
            continue
        for line in page.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = doi_re.search(line)
            if not match:
                continue
            doi = clean_doi(match.group(1))
            if not doi or doi in seen:
                continue
            # A readable fallback title for logs, not used as the source of truth.
            title = re.sub(r"<[^>]+>", "", line)
            title = re.sub(r"\*\*|”|“|\"", "", title)
            title = re.sub(r"^\s*\d+\.\s*", "", title).strip()
            pubs.append({"doi": doi, "title": title[:220]})
            seen.add(doi)
    return pubs


def load_publications() -> list[dict[str, str]]:
    explicit = read_yaml(ROOT / "_data" / "publications.yml", [])
    if explicit:
        return [{"doi": clean_doi(p.get("doi")), "title": p.get("title", "")} for p in explicit]
    return extract_dois_from_markdown()


def load_big_names() -> set[str]:
    rows = read_yaml(ROOT / "_data" / "big_names.yml", [])
    names: set[str] = set()
    for row in rows:
        for key in ("name", "openalex_id", "orcid"):
            value = str(row.get(key, "")).strip().lower()
            if value:
                names.add(value.split("/")[-1])
    return names


def find_work_by_doi(doi: str) -> dict[str, Any] | None:
    if not doi:
        return None
    try:
        return api_get(f"{OPENALEX}/works/doi:{quote(doi, safe='')}")
    except Exception as exc:
        print(f"[WARN] DOI not found in OpenAlex: {doi} ({exc})")
        return None


def iter_citing_works(cited_by_api_url: str):
    cursor = "*"
    seen = 0
    limit = CFG.get("max_citing_works_per_paper")
    while True:
        data = api_get(
            cited_by_api_url,
            {
                "cursor": cursor,
                "per-page": 200,
                "select": "id,doi,title,publication_year,authorships,primary_location",
            },
        )
        results = data.get("results", [])
        if not results:
            return

        for work in results:
            year = work.get("publication_year")
            if CFG.get("since_year") and (year or 0) < int(CFG["since_year"]):
                continue
            yield work
            seen += 1
            if limit and seen >= int(limit):
                return

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            return


def load_author_cache() -> dict[str, Any]:
    if AUTHOR_CACHE.exists():
        try:
            return json.loads(AUTHOR_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_author_cache(cache: dict[str, Any]) -> None:
    AUTHOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    AUTHOR_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


AUTHOR_DETAILS = load_author_cache()


def get_author_details(author_id: str) -> dict[str, Any]:
    aid = author_id.rstrip("/").split("/")[-1]
    if aid in AUTHOR_DETAILS:
        return AUTHOR_DETAILS[aid]
    data = api_get(
        f"{OPENALEX}/authors/{aid}",
        {"select": "id,display_name,orcid,works_count,cited_by_count,summary_stats"},
    )
    AUTHOR_DETAILS[aid] = data
    return data


def big_author_reason(author_stub: dict[str, Any], pinned: set[str]) -> tuple[bool, str, dict[str, Any]]:
    raw_id = author_stub.get("id") or ""
    aid = raw_id.rstrip("/").split("/")[-1]
    name = str(author_stub.get("display_name") or "").strip()
    orcid = str(author_stub.get("orcid") or "").rstrip("/").split("/")[-1]

    if name.lower() in pinned or aid.lower() in pinned or orcid.lower() in pinned:
        details = get_author_details(aid) if aid else author_stub
        return True, "manual whitelist", details

    if not aid:
        return False, "", author_stub

    details = get_author_details(aid)
    cited = int(details.get("cited_by_count") or 0)
    works = int(details.get("works_count") or 0)
    h_index = int((details.get("summary_stats") or {}).get("h_index") or 0)

    if h_index >= int(CFG["min_author_h_index"]):
        return True, f"h-index ≥ {CFG['min_author_h_index']}", details
    if cited >= int(CFG["min_author_citations"]):
        return True, f"citations ≥ {CFG['min_author_citations']}", details
    if works >= int(CFG["min_author_works"]) and cited >= int(CFG["min_author_citations"]) // 2:
        return True, "works + citations threshold", details
    return False, "", details


def work_url(work: dict[str, Any]) -> str:
    location = work.get("primary_location") or {}
    return location.get("landing_page_url") or work.get("doi") or work.get("id") or ""


def main() -> None:
    publications = load_publications()
    pinned = load_big_names()
    hits: dict[tuple[str, str, str], Hit] = {}

    if not publications:
        raise SystemExit("No publications found. Add DOIs to _pages/publications*.md or _data/publications.yml.")

    print(f"Found {len(publications)} unique DOI(s) for your publications.")

    for pub in publications:
        work = find_work_by_doi(pub.get("doi", ""))
        if not work:
            continue

        title = work.get("title") or pub.get("title") or pub.get("doi") or ""
        cited_by_count = int(work.get("cited_by_count") or 0)
        print(f"Checking: {title[:100]} ({cited_by_count} citations)")

        cited_by_api_url = work.get("cited_by_api_url")
        if not cited_by_api_url or cited_by_count == 0:
            continue

        for citing in iter_citing_works(cited_by_api_url):
            for authorship in citing.get("authorships") or []:
                author_stub = authorship.get("author") or {}
                ok, reason, details = big_author_reason(author_stub, pinned)
                if not ok:
                    continue

                stats = details.get("summary_stats") or {}
                hit = Hit(
                    my_paper_title=title,
                    my_paper_doi=clean_doi(work.get("doi") or pub.get("doi")),
                    citing_title=citing.get("title") or "",
                    citing_year=citing.get("publication_year"),
                    citing_doi=clean_doi(citing.get("doi")),
                    citing_url=work_url(citing),
                    big_author=details.get("display_name") or author_stub.get("display_name") or "",
                    big_author_openalex_id=details.get("id") or author_stub.get("id") or "",
                    big_author_citations=details.get("cited_by_count"),
                    big_author_works=details.get("works_count"),
                    big_author_h_index=stats.get("h_index"),
                    match_reason=reason,
                )
                key = (hit.my_paper_doi, hit.citing_title, hit.big_author)
                hits[key] = hit

    save_author_cache(AUTHOR_DETAILS)
    rows = sorted(
        hits.values(),
        key=lambda h: ((h.citing_year or 0), (h.big_author_citations or 0), h.big_author),
        reverse=True,
    )

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    OUT_JSON.write_text(json.dumps([asdict(h) for h in rows], ensure_ascii=False, indent=2), encoding="utf-8")

    fields = list(Hit.__annotations__.keys())
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    lines = [
        '<div class="notice--info" markdown="1">',
        f"This page is automatically updated by GitHub Actions. Current records: **{len(rows)}**.",
        "</div>",
        "",
        "| Year | Big-name author | Citing paper | My cited paper | Reason |",
        "|---:|---|---|---|---|",
    ]

    if rows:
        for h in rows:
            citing = f"[{h.citing_title}]({h.citing_url})" if h.citing_url else h.citing_title
            mine = h.my_paper_title.replace("|", "\\|")
            lines.append(
                f"| {h.citing_year or ''} | {h.big_author} | {citing} | {mine} | {h.match_reason} |"
            )
    else:
        lines.append("|  | No matching records yet. |  |  |  |")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Done. Wrote {len(rows)} record(s).")


if __name__ == "__main__":
    main()
