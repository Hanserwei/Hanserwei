#!/usr/bin/env python3
"""Build a GitHub profile from the blog's public RSS feed.

Python 3.11+; standard library only. Network failures retain the last good source.
Use --offline to render the committed snapshot, or --check to verify that render.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def clean_text(value: str) -> str:
    # Titles/descriptions are text, never trusted HTML or Markdown.
    return " ".join(re.sub(r"<[^>]*>", "", html.unescape(value)).split())


def public_url(value: str, host: str) -> str | None:
    parsed = urlparse(value)
    if (
        parsed.scheme == "https"
        and parsed.netloc.lower() == host.lower()
        and not re.search(r"[\s<>\x00-\x1f]", value)
    ):
        return value
    return None


def fetch(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "likeyy.love":
        raise ValueError("Only the public HTTPS blog feed is allowed")
    headers = {"User-Agent": "Hanserwei-profile/1.0 (+https://github.com/Hanserwei/Hanserwei)"}
    for attempt in range(3):
        connection = HTTPSConnection(parsed.netloc, timeout=20)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            # No redirect following: requests stay on the configured blog host.
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise HTTPError(url, response.status, response.reason, response.headers, None)
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Response exceeds 4 MiB")
            return data
        except OSError as error:
            # HTTPError and TimeoutError both inherit OSError.
            if isinstance(error, HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
        finally:
            connection.close()
    raise RuntimeError("Fetch did not return")


def parse_json(data: str | bytes, source: str):
    try:
        return json.loads(data)
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid JSON from {source}: {error}") from error


def read_json(path: Path):
    try:
        return parse_json(path.read_text(encoding="utf-8"), str(path))
    except OSError as error:
        raise RuntimeError(f"Cannot read {path}: {error}") from error


def parse_feed(data: bytes, blog: str, limit: int) -> list[dict]:
    text = data.decode("utf-8-sig")
    if len(data) > MAX_BYTES or "\x00" in text:
        raise ValueError("Feed must be bounded UTF-8 XML")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("DTD/entity declarations are not allowed in the feed")
    # UTF-8 only, bounded input; all DTD and entity declarations rejected above.
    root = ET.fromstring(text)  # noqa: S314 — guarded RSS/Atom parser
    atom = "{http://www.w3.org/2005/Atom}"
    is_atom = root.tag == atom + "feed"
    entries = root.findall(atom + "entry") if is_atom else root.findall("./channel/item")
    posts = []
    seen = set()
    for entry in entries:
        if is_atom:
            title = entry.findtext(atom + "title", "")
            links = entry.findall(atom + "link")
            link = next((node.get("href", "") for node in links if node.get("rel", "alternate") == "alternate"), "")
            published = entry.findtext(atom + "published") or entry.findtext(atom + "updated", "")
        else:
            title = entry.findtext("title", "")
            link = entry.findtext("link", "")
            published = entry.findtext("pubDate", "")
        title = clean_text(title)
        link = public_url(link.strip(), urlparse(blog).netloc)
        if not title or not link or link in seen:
            continue
        try:
            stamp = datetime.fromisoformat(published.replace("Z", "+00:00")) if is_atom else parsedate_to_datetime(published)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        seen.add(link)
        posts.append({"title": title, "url": link, "published": stamp.astimezone(timezone.utc).isoformat()})
    # Python's stable sort preserves feed order when articles have equal dates.
    posts.sort(key=lambda post: post["published"], reverse=True)
    if not posts:
        raise ValueError("Feed contains no valid dated blog articles")
    return posts[:limit]


def refresh(config: dict, previous: dict, now: str) -> tuple[dict, list[str]]:
    snapshot = copy.deepcopy(previous)
    warnings = []
    sources = {
        "posts": lambda: parse_feed(fetch(config["feed"]), config["blog"], config["post_limit"]),
    }
    for name, load in sources.items():
        try:
            snapshot[name] = load()
        except (OSError, HTTPException, ValueError, KeyError, TypeError, ET.ParseError) as error:
            if not previous.get(name):
                raise RuntimeError(f"No cached {name} data is available: {error}") from error
            warnings.append(f"{name}: keeping last successful snapshot ({error})")
    if any(snapshot.get(key) != previous.get(key) for key in sources):
        snapshot["updated_at"] = now
    snapshot.setdefault("updated_at", now)
    return snapshot, warnings


def render(root: Path, config: dict, snapshot: dict) -> dict[str, str]:
    posts = []
    for post in snapshot["posts"][:config["post_limit"]]:
        date = datetime.fromisoformat(post["published"]).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        posts.append(f'<p><sub>{date}</sub><br /><a href="{escape(post["url"])}">{escape(post["title"])} ↗</a></p>')
    replacements = {
        "BLOG_POSTS": "\n\n".join(posts),
        "UPDATED_AT": snapshot["updated_at"],
    }
    template = (root / ".profile/README.template.md").read_text(encoding="utf-8")
    # Substitute only template tokens, never content from API responses.
    readme = re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: replacements[match[1]], template)
    return {"README.md": readme}


def write_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Render the committed snapshot without network access")
    parser.add_argument("--check", action="store_true", help="Verify generated files against the committed snapshot, without writing or fetching")
    args = parser.parse_args()
    config = read_json(ROOT / ".profile/config.json")
    cache_path = ROOT / ".profile/cache.json"
    previous = read_json(cache_path) if cache_path.exists() else {}
    if args.offline or args.check:
        if not previous:
            raise RuntimeError("No snapshot exists. Run once with network access first.")
        snapshot = previous
    else:
        now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M CST (UTC+8)")
        snapshot, warnings = refresh(config, previous, now)
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        if warnings and (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
            try:
                with open(summary, "a", encoding="utf-8") as stream:
                    stream.write("### Profile source warnings\n" + "\n".join(f"- {warning}" for warning in warnings) + "\n")
            except OSError as error:
                print(f"Warning: could not append Actions summary: {error}", file=sys.stderr)
    outputs = render(ROOT, config, snapshot)
    outputs[".profile/cache.json"] = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        stale = [name for name, content in outputs.items() if not (ROOT / name).exists() or (ROOT / name).read_text(encoding="utf-8") != content]
        if stale:
            print("Outdated generated files: " + ", ".join(stale), file=sys.stderr)
            return 1
        print(f"Verified {len(outputs)} generated files against the cached sources.")
        return 0
    changed = [name for name, content in outputs.items() if write_changed(ROOT / name, content)]
    print(f"Updated {len(changed)} files: {', '.join(changed) if changed else 'no content changes'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, ET.ParseError) as error:
        print(f"Profile update failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
