"""Regression tests for untrusted feed data and unattended profile updates."""

import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from scripts import update_profile as profile


RSS = b"""<rss version="2.0"><channel>
<item><title>Old post</title><link>https://likeyy.love/archives/old</link><pubDate>Mon, 21 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title><![CDATA[DDD <b>&amp; Java</b> | [guide] {{TOOLBOX}}]]></title><link>https://likeyy.love/archives/new?a=1&amp;b=2</link><pubDate>Tue, 22 Sep 2026 18:00:00 GMT</pubDate></item>
<item><title>Duplicate</title><link>https://likeyy.love/archives/old</link><pubDate>Wed, 23 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>Unsafe</title><link>javascript:alert(1)</link><pubDate>Wed, 23 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>Wrong host</title><link>https://likeyy.love.evil.example/post</link><pubDate>Wed, 23 Sep 2026 08:00:00 GMT</pubDate></item>
<item><title>Bad date</title><link>https://likeyy.love/archives/bad</link><pubDate>invalid</pubDate></item>
</channel></rss>"""


def repository(name="demo", **extra):
    return {
        "name": name, "description": "A public project", "language": "Python",
        "stargazers_count": 2, "pushed_at": "2026-09-22T08:00:00Z",
        "fork": False, "private": False, "archived": False, "disabled": False,
        **extra,
    }


def snapshot():
    return {
        "updated_at": "2026-09-23 09:23 CST (UTC+8)",
        "github": {"public_repos": 22, "repos": [{
            "name": "demo", "description": "Pipe | [link](javascript:alert(1)) <img>",
            "language": "Python", "stars": 2, "pushed_at": "2026-09-22T08:00:00Z",
        }]},
        "posts": profile.parse_feed(RSS, "https://likeyy.love", 5),
    }


class FeedTests(unittest.TestCase):
    def test_sort_deduplicate_sanitize_and_reject_invalid_entries(self):
        posts = profile.parse_feed(RSS, "https://likeyy.love", 5)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["title"], "DDD & Java | [guide] {{TOOLBOX}}")
        self.assertEqual(posts[1]["url"], "https://likeyy.love/archives/old")
        self.assertEqual(len(profile.parse_feed(RSS, "https://likeyy.love", 1)), 1)

    def test_atom_supports_default_namespace_and_alternate_link(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>Atom &amp; RSS</title><link rel="self" href="https://elsewhere.example/self"/>
        <link rel="alternate" href="https://likeyy.love/archives/atom"/>
        <updated>2026-09-22T09:00:00+08:00</updated></entry></feed>'''
        posts = profile.parse_feed(feed, "https://likeyy.love", 5)
        self.assertEqual(posts[0]["published"], "2026-09-22T01:00:00+00:00")

    def test_empty_feed_and_html_error_do_not_replace_good_articles(self):
        for feed in [b"<rss><channel/></rss>", b"<html>Unavailable</html>", b"broken xml"]:
            with self.subTest(feed=feed), self.assertRaises((ValueError, ET.ParseError)):
                profile.parse_feed(feed, "https://likeyy.love", 5)

    def test_entity_declarations_are_rejected(self):
        xml = '<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>'
        for encoding in ["utf-8", "utf-16", "utf-16-le", "utf-32"]:
            with self.subTest(encoding=encoding), self.assertRaises(ValueError):
                profile.parse_feed(xml.encode(encoding), "https://likeyy.love", 5)

    def test_host_credentials_and_non_https_links_are_rejected(self):
        for url in ["file:///etc/passwd", "http://likeyy.love/post", "https://user@likeyy.love/post", "https://likeyy.love/x\n"]:
            self.assertIsNone(profile.public_url(url, "likeyy.love"))


class GitHubTests(unittest.TestCase):
    def test_only_active_original_public_projects_are_selected(self):
        repos = [repository("older", pushed_at="2026-09-20T08:00:00Z"), repository("newer"),
                 repository("fork", fork=True), repository("secret", private=True),
                 repository("archived", archived=True), repository("disabled", disabled=True),
                 repository("Hanserwei")]
        with patch.object(profile, "fetch", side_effect=[json.dumps({"public_repos": 22}).encode(), json.dumps(repos).encode()]):
            result = profile.github_snapshot("Hanserwei")
        self.assertEqual([repo["name"] for repo in result["repos"]], ["newer", "older"])
        self.assertEqual(result["public_repos"], 22)

    def test_pagination_reads_beyond_first_hundred_repositories(self):
        first = [repository(f"fork-{i}", fork=True) for i in range(100)]
        with patch.object(profile, "fetch", side_effect=[json.dumps({"public_repos": 101}).encode(), json.dumps(first).encode(), json.dumps([repository()]).encode()]) as fetch:
            result = profile.github_snapshot("Hanserwei")
        self.assertEqual(len(result["repos"]), 1)
        self.assertIn("page=2", fetch.call_args.args[0])

    def test_bad_counts_and_malformed_json_are_reported(self):
        for value in [-1, True, "22", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                profile.count(value)
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            profile.parse_json("not json", "test")

    def test_fetch_rejects_non_source_urls_before_network_access(self):
        for url in ["file:///etc/passwd", "https://evil.example/rss.xml"]:
            with self.assertRaises(ValueError):
                profile.fetch(url)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = {"username": "Hanserwei", "blog": "https://likeyy.love", "feed": "https://likeyy.love/rss.xml", "post_limit": 5}
        self.previous = snapshot()

    def test_blog_failure_preserves_posts_but_github_can_update(self):
        fresh = copy.deepcopy(self.previous["github"])
        fresh["public_repos"] = 23
        with patch.object(profile, "github_snapshot", return_value=fresh), patch.object(profile, "fetch", side_effect=URLError("offline")):
            result, warnings = profile.refresh(self.config, self.previous, "new time")
        self.assertEqual(result["posts"], self.previous["posts"])
        self.assertEqual(result["github"]["public_repos"], 23)
        self.assertEqual(result["updated_at"], "new time")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(self.previous["github"]["public_repos"], 22)

    def test_total_outage_keeps_snapshot_and_timestamp_identical(self):
        with patch.object(profile, "github_snapshot", side_effect=URLError("offline")), patch.object(profile, "fetch", side_effect=URLError("offline")):
            result, warnings = profile.refresh(self.config, self.previous, "new time")
        self.assertEqual(result, self.previous)
        self.assertEqual(len(warnings), 2)

    def test_successful_unchanged_refresh_does_not_create_daily_churn(self):
        with patch.object(profile, "github_snapshot", return_value=self.previous["github"]), patch.object(profile, "fetch", return_value=RSS):
            result, warnings = profile.refresh(self.config, self.previous, "new time")
        self.assertEqual(result, self.previous)
        self.assertEqual(warnings, [])

    def test_first_run_without_any_cache_fails_closed(self):
        with patch.object(profile, "github_snapshot", side_effect=URLError("offline")), self.assertRaises(RuntimeError):
            profile.refresh(self.config, {}, "new time")


class RenderTests(unittest.TestCase):
    def test_render_is_deterministic_and_external_text_cannot_inject_markup(self):
        config = json.loads((profile.ROOT / ".profile/config.json").read_text(encoding="utf-8"))
        result = profile.render(profile.ROOT, config, snapshot())
        self.assertEqual(result, profile.render(profile.ROOT, config, snapshot()))
        readme = result["README.md"]
        self.assertIn("2026-09-23</sub>", readme)  # UTC+8 blog date
        self.assertIn("a=1&amp;b=2", readme)
        self.assertIn("{{TOOLBOX}}", readme)  # Feed content isn't template code.
        self.assertIn("Pipe | [link](javascript:alert(1)) &lt;img&gt;", readme)
        self.assertNotIn("<img>", readme)
        for name, content in result.items():
            if name.endswith(".svg"):
                # Only our generated SVG and checked-in icons enter this assertion.
                element = ET.fromstring(content)  # noqa: S314 — trusted local output
                self.assertEqual(element.tag, "{http://www.w3.org/2000/svg}svg")

    def test_noop_write_preserves_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "README.md"
            self.assertTrue(profile.write_changed(target, "hello\n"))
            original = target.stat().st_mtime_ns
            self.assertFalse(profile.write_changed(target, "hello\n"))
            self.assertEqual(target.stat().st_mtime_ns, original)


if __name__ == "__main__":
    unittest.main()
