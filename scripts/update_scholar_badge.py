#!/usr/bin/env python3
"""Update assets/images/scholar_citations.svg from Google Scholar profile."""
from __future__ import annotations

import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SCHOLAR_URL = "https://scholar.google.com/citations?user=SeO0JUwAAAAJ&hl=zh-CN"
OUT = ROOT / "assets" / "images" / "scholar_citations.svg"


def fetch_citations() -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; GitHubActions/1.0)"}
    html = requests.get(SCHOLAR_URL, headers=headers, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")

    for row in soup.select("tr"):
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) >= 2 and cells[0] in {"引用次数", "Citations"}:
            count = re.sub(r"\D", "", cells[1])
            if count:
                return count

    raise RuntimeError("Could not find citation count on Google Scholar profile.")


def make_svg(count: str) -> str:
    return f"""<svg width="132" height="34" viewBox="0 0 132 34" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="引用 {count}">
  <defs><linearGradient id="g" x1="24" y1="5" x2="24" y2="29" gradientUnits="userSpaceOnUse"><stop stop-color="#2F80ED"/><stop offset="1" stop-color="#5FA8FF"/></linearGradient></defs>
  <rect x="1" y="1" width="130" height="32" rx="7" fill="#F8FAFC" stroke="#D7DEE8"/>
  <rect x="86" y="1" width="45" height="32" rx="7" fill="#D8ECFF"/>
  <path d="M86 1h7v32h-7z" fill="#D8ECFF"/>
  <g transform="translate(14 7)">
    <path d="M10 0 20 6v8l-10 6L0 14V6L10 0Z" fill="url(#g)"/>
    <circle cx="10" cy="10" r="3.2" fill="white" opacity=".92"/>
    <path d="M10 6.8v6.4M6.8 10h6.4" stroke="#2F80ED" stroke-width="1.6" stroke-linecap="round"/>
  </g>
  <text x="40" y="21.5" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif" font-size="14" font-weight="600" fill="#4B5563">引用</text>
  <text x="101" y="21.5" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif" font-size="14" font-weight="700" fill="#356B98">{count}</text>
</svg>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(make_svg(fetch_citations()), encoding="utf-8")


if __name__ == "__main__":
    main()
