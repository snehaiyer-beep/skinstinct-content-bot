"""
Google News lookup — no API key or account, using Google News' public RSS
search endpoint. Deliberately minimal: one query, top result only, and
best-effort (returns None on any failure) so a broken news lookup never
blocks drafting — the pipeline is designed to draft with or without a news
angle, per the voice skill's own instruction to ignore a poor fit.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

logger = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    unescaped = html.unescape(_TAG_RE.sub("", text or ""))
    return re.sub(r"\s+", " ", unescaped).strip()


def _format_date(pub_date: str) -> str:
    try:
        return datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z").strftime("%Y-%m-%d")
    except ValueError:
        return pub_date


def _parse_top_item(xml_text: str) -> dict | None:
    root = ElementTree.fromstring(xml_text)
    item = root.find("./channel/item")
    if item is None:
        return None

    title = html.unescape((item.findtext("title") or "").strip())
    link = (item.findtext("link") or "").strip()
    pub_date = (item.findtext("pubDate") or "").strip()
    source_el = item.find("source")
    source = source_el.text.strip() if source_el is not None and source_el.text else None

    headline = title
    if source and headline.endswith(f" - {source}"):
        # Google News typically repeats the source name at the end of the title
        # even when a separate <source> tag is also present — strip the duplicate.
        headline = headline[: -(len(source) + 3)].strip()
    elif not source and " - " in title:
        headline, _, source = title.rpartition(" - ")

    summary = _strip_html(item.findtext("description") or "") or headline

    return {
        "headline": headline.strip(),
        "source": (source or "unknown source").strip(),
        "date": _format_date(pub_date),
        "link": link,
        "summary": summary,
    }


def fetch_top_news(query: str) -> dict | None:
    if not query or not query.strip():
        return None
    url = RSS_URL.format(query=quote_plus(query.strip()))
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return _parse_top_item(response.text)
    except Exception:  # noqa: BLE001 — a broken news lookup should never block drafting
        logger.exception("Google News lookup failed for query=%r", query)
        return None
