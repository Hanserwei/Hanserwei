#!/usr/bin/env python3
"""Build a GitHub profile from public GitHub data and the blog's RSS feed.

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
FONT = "Arial, PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, monospace"


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
    if parsed.scheme != "https" or parsed.netloc not in {"api.github.com", "likeyy.love"}:
        raise ValueError("Only the public GitHub API and the HTTPS blog feed are allowed")
    headers = {"User-Agent": "Hanserwei-profile/1.0 (+https://github.com/Hanserwei/Hanserwei)"}
    if parsed.netloc == "api.github.com":
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if token := os.environ.get("GITHUB_TOKEN"):
            headers.update({"Authorization": f"Bearer {token}"})
    for attempt in range(3):
        connection = HTTPSConnection(parsed.netloc, timeout=20)
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            # No redirect following: credentials can never leave the API host.
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


def count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("GitHub counts must be non-negative integers")
    return value


def github_snapshot(username: str) -> dict:
    user = parse_json(fetch(f"https://api.github.com/users/{username}"), "GitHub user")
    repos = []
    for page in range(1, 101):
        batch = parse_json(fetch(
            f"https://api.github.com/users/{username}/repos?type=owner&sort=pushed&per_page=100&page={page}"
        ), "GitHub repositories")
        if not isinstance(batch, list):
            raise ValueError("GitHub repositories response is not a list")
        for repo in batch:
            if repo.get("fork") or repo.get("private") or repo.get("archived") or repo.get("disabled"):
                continue
            if repo["name"].casefold() == username.casefold():
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo["name"]):
                continue
            repos.append({
                "name": repo["name"],
                "description": clean_text(repo.get("description") or "持续构建与探索中。"),
                "language": repo.get("language") or "Code",
                "stars": count(repo["stargazers_count"]),
                "pushed_at": repo["pushed_at"],
            })
        if len(batch) < 100:
            break
    else:
        raise ValueError("GitHub pagination exceeded 100 pages")
    if not repos:
        raise ValueError("GitHub returned no eligible public projects")
    repos.sort(key=lambda repo: (repo["pushed_at"], repo["name"]), reverse=True)
    return {"public_repos": count(user["public_repos"]), "repos": repos}


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
        "github": lambda: github_snapshot(config["username"]),
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


def svg_document(width: int, height: int, title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">\n'
        f'<title id="title">{escape(title)}</title>\n'
        f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="18" fill="#121923" stroke="#303b4a"/>\n'
        f'{body}\n</svg>\n'
    )


def project_card(project: dict, repo: dict | None) -> str:
    accent = project["accent"]
    detail = f'★ {repo["stars"]}   /   {repo["language"]}   /   {repo["pushed_at"][:10]}' if repo else "EXPLORE THE REPOSITORY"
    body = f'''<path d="M20 1H550" stroke="{accent}" stroke-width="2"/>
<text x="27" y="36" fill="{accent}" font-family="{MONO}" font-size="12" letter-spacing="1.5">{escape(project['category'])}</text>
<text x="541" y="38" text-anchor="end" fill="#66748a" font-family="{MONO}" font-size="17">{escape(project['glyph'])}</text>
<text x="26" y="85" fill="#edf2f7" font-family="{FONT}" font-size="32" font-weight="700" letter-spacing="-.7">{escape(project['title'])}</text>
<text x="27" y="124" fill="#d1dae4" font-family="{FONT}" font-size="21">{escape(project['description'][0])}</text>
<text x="27" y="154" fill="#a6b4c8" font-family="{FONT}" font-size="18">{escape(project['description'][1])}</text>
<text x="27" y="192" fill="{accent}" font-family="{FONT}" font-size="17">{escape(project['stack'])}</text>
<path d="M27 210H543" stroke="#2b3647"/>
<text x="27" y="237" fill="#97a8be" font-family="{MONO}" font-size="14">{escape(detail)}</text>
<text x="541" y="238" text-anchor="end" fill="{accent}" font-family="{FONT}" font-size="21">↗</text>'''
    return svg_document(570, 258, f"{project['title']} — {' '.join(project['description'])}", body)


def stats_card(config: dict, snapshot: dict, mobile: bool = False) -> tuple[str, str]:
    repos = snapshot["github"]["repos"]
    recent = max(repo["pushed_at"] for repo in repos)[:10]
    items = [(str(snapshot["github"]["public_repos"]), "PUBLIC REPOS", "#b4f272"),
             (str(len(config["featured"])), "SELECTED BUILDS", "#b6a0ff"),
             ("likeyy.love", "DIGITAL GARDEN", "#76d9ec"),
             (recent, "LATEST CODE PUSH", "#f3bd78")]
    body = []
    for index, (value, label, color) in enumerate(items):
        x = 30 + (index % 2 if mobile else index) * 285
        y = index // 2 * 100 if mobile else 0
        if index and not mobile:
            body.append(f'<path d="M{x - 16} 26V92" stroke="#303b4a"/>')
        body.append(f'<text x="{x}" y="{54 + y}" fill="{color}" font-family="{FONT}" font-size="30" font-weight="700">{escape(value)}</text>')
        body.append(f'<text x="{x}" y="{83 + y}" fill="#99a8bc" font-family="{MONO}" font-size="12" letter-spacing="1.5">{label}</text>')
    alt = f'{snapshot["github"]["public_repos"]} 个公开仓库 · {len(config["featured"])} 个精选项目 · 博客 likeyy.love · 最近代码推送 {recent}'
    return svg_document(600 if mobile else 1160, 214 if mobile else 112, alt, "\n".join(body)), alt


def icon_chip(root: Path, tool: dict) -> str:
    # Checked-in SVG assets from a pinned upstream revision, never network input.
    source = ET.fromstring((root / "assets/icons" / f'{tool["icon"]}.svg').read_bytes())  # noqa: S314
    source.set("x", "23")
    source.set("y", "17")
    source.set("width", "42")
    source.set("height", "42")
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    body = (
        '<!-- Dashboard Icons: Apache-2.0; see ../icons/LICENSE and NOTICE.md. '
        'Modified presentation: added backing, label and positioning. -->\n'
        '<rect x="13" y="7" width="62" height="62" rx="16" fill="#edf2f7"/>'
        + ET.tostring(source, encoding="unicode")
        + f'<text x="44" y="92" text-anchor="middle" fill="#cbd6e5" font-family="{FONT}" font-size="12">{escape(tool["name"])}</text>'
    )
    return svg_document(88, 108, tool["name"], body)


def render(root: Path, config: dict, snapshot: dict) -> dict[str, str]:
    outputs = {}
    username = config["username"]
    repos = snapshot["github"]["repos"]
    by_name = {repo["name"]: repo for repo in repos}
    cards, index = [], []
    for project in config["featured"]:
        name = project["repo"]
        target = f"https://github.com/{username}/{name}"
        asset = f"assets/generated/project-{name}.svg"
        outputs[asset] = project_card(project, by_name.get(name))
        alt = f'{project["title"]} — {project["description"][0]} {project["stack"]}'
        cards.append(f'<a href="{target}"><img src="{asset}" width="420" alt="{escape(alt)}" /></a>')
        index.append(f'- **[{project["title"]}]({target})** — {" ".join(project["description"])} `{project["stack"]}`')
    recent = []
    for repo in repos[:config["recent_limit"]]:
        name = repo["name"]
        recent.append(
            f'<p><a href="https://github.com/{username}/{name}"><strong>{escape(name)} ↗</strong></a>'
            f' &nbsp; <sub>{escape(repo["language"])} · {escape(repo["pushed_at"][:10])}</sub>'
            f'<br />{escape(repo["description"])}</p>'
        )
    posts = []
    for post in snapshot["posts"][:config["post_limit"]]:
        date = datetime.fromisoformat(post["published"]).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        posts.append(f'<p><sub>{date}</sub><br /><a href="{escape(post["url"])}"><strong>{escape(post["title"])} ↗</strong></a></p>')
    tools = []
    for tool in config["toolbox"]:
        asset = f'assets/generated/tool-{tool["icon"]}.svg'
        outputs[asset] = icon_chip(root, tool)
        tools.append(f'<a href="{escape(tool["url"])}"><img src="{asset}" width="76" height="93" alt="{escape(tool["name"])}" /></a>')
    pulse, stats_alt = stats_card(config, snapshot)
    outputs["assets/generated/pulse.svg"] = pulse
    outputs["assets/generated/pulse-mobile.svg"] = stats_card(config, snapshot, mobile=True)[0]
    replacements = {
        "STATS_ALT": escape(stats_alt),
        "PROJECT_CARDS": '<p align="center">\n' + "\n".join(cards) + "\n</p>",
        "PROJECT_INDEX": "\n".join(index),
        "RECENT_PROJECTS": "\n\n".join(recent),
        "BLOG_POSTS": "\n\n".join(posts),
        "TOOLBOX": '<p align="center">\n' + "\n".join(tools) + "\n</p>",
        "UPDATED_AT": snapshot["updated_at"],
    }
    template = (root / ".profile/README.template.md").read_text(encoding="utf-8")
    # Substitute only template tokens, never content from API responses.
    outputs["README.md"] = re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: replacements[match[1]], template)
    return outputs


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
