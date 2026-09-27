"""Regression tests for untrusted feed data and unattended profile updates."""

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


def snapshot():
    return {
        "updated_at": "2026-09-23 09:23 CST (UTC+8)",
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


class FetchTests(unittest.TestCase):
    def test_malformed_json_is_reported(self):
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            profile.parse_json("not json", "test")

    def test_fetch_rejects_non_source_urls_before_network_access(self):
        for url in ["file:///etc/passwd", "https://evil.example/rss.xml", "https://api.github.com/users/Hanserwei"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                profile.fetch(url)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = {"blog": "https://likeyy.love", "feed": "https://likeyy.love/rss.xml", "post_limit": 5}
        self.previous = snapshot()

    def test_new_articles_update_snapshot_without_mutating_previous(self):
        feed = RSS.replace(b"Old post", b"Updated post")
        with patch.object(profile, "fetch", return_value=feed):
            result, warnings = profile.refresh(self.config, self.previous, "new time")
        self.assertEqual(result["posts"][1]["title"], "Updated post")
        self.assertEqual(result["updated_at"], "new time")
        self.assertEqual(self.previous["posts"][1]["title"], "Old post")
        self.assertEqual(warnings, [])

    def test_outage_keeps_posts_and_timestamp_identical(self):
        with patch.object(profile, "fetch", side_effect=URLError("offline")):
            result, warnings = profile.refresh(self.config, self.previous, "new time")
        self.assertEqual(result, self.previous)
        self.assertEqual(len(warnings), 1)

    def test_successful_unchanged_refresh_does_not_create_daily_churn(self):
        with patch.object(profile, "fetch", return_value=RSS):
            result, warnings = profile.refresh(self.config, self.previous, "new time")
        self.assertEqual(result, self.previous)
        self.assertEqual(warnings, [])

    def test_first_run_without_any_cache_fails_closed(self):
        with patch.object(profile, "fetch", side_effect=URLError("offline")), self.assertRaises(RuntimeError):
            profile.refresh(self.config, {}, "new time")


class RenderTests(unittest.TestCase):
    def test_render_is_deterministic_and_external_text_cannot_inject_markup(self):
        config = json.loads((profile.ROOT / ".profile/config.json").read_text(encoding="utf-8"))
        data = snapshot()
        data["posts"][1]["title"] = 'Title </a><img src=x onerror="alert(1)">'
        result = profile.render(profile.ROOT, config, data)
        self.assertEqual(result, profile.render(profile.ROOT, config, data))
        readme = result["README.md"]
        self.assertIn("2026-09-23</sub>", readme)  # UTC+8 blog date
        self.assertIn("a=1&amp;b=2", readme)
        self.assertIn("{{TOOLBOX}}", readme)  # Feed content isn't template code.
        self.assertIn("Title &lt;/a&gt;&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", readme)
        self.assertNotIn("<img>", readme)
        config["post_limit"] = 1
        limited = profile.render(profile.ROOT, config, data)["README.md"]
        self.assertNotIn("Title &lt;/a&gt;", limited)

    def test_noop_write_preserves_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "README.md"
            self.assertTrue(profile.write_changed(target, "hello\n"))
            original = target.stat().st_mtime_ns
            self.assertFalse(profile.write_changed(target, "hello\n"))
            self.assertEqual(target.stat().st_mtime_ns, original)


if __name__ == "__main__":
    unittest.main()
