import unittest

from services.news import _parse_top_item

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
<title>"vitamin c" - Google News</title>
<item>
  <title>Study finds Vitamin C degrades fast - Example Times</title>
  <link>https://news.google.com/rss/articles/abc123?oc=5</link>
  <pubDate>Sun, 20 Sep 2026 14:30:00 GMT</pubDate>
  <source url="https://example.com">Example Times</source>
  <description>&lt;a href="https://example.com/article"&gt;Study finds Vitamin C degrades fast&lt;/a&gt;&amp;nbsp;&amp;nbsp;Example Times</description>
</item>
<item>
  <title>Second item - Another Source</title>
  <link>https://news.google.com/rss/articles/def456?oc=5</link>
  <pubDate>Sat, 19 Sep 2026 09:00:00 GMT</pubDate>
</item>
</channel>
</rss>
"""

NO_SOURCE_TAG_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel>
<item>
  <title>Headline without a source tag - Fallback Source</title>
  <link>https://example.com/x</link>
  <pubDate>Mon, 21 Sep 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>
"""

EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel><title>no results</title></channel></rss>
"""


class TestNewsParsing(unittest.TestCase):
    def test_parses_top_item_with_source_tag(self):
        item = _parse_top_item(SAMPLE_RSS)
        self.assertEqual(item["headline"], "Study finds Vitamin C degrades fast")
        self.assertEqual(item["source"], "Example Times")
        self.assertEqual(item["date"], "2026-09-20")
        self.assertEqual(item["link"], "https://news.google.com/rss/articles/abc123?oc=5")
        self.assertIn("Study finds Vitamin C degrades fast", item["summary"])
        self.assertNotIn("&nbsp;", item["summary"])
        self.assertNotIn("<a", item["summary"])

    def test_falls_back_to_splitting_title_when_no_source_tag(self):
        item = _parse_top_item(NO_SOURCE_TAG_RSS)
        self.assertEqual(item["headline"], "Headline without a source tag")
        self.assertEqual(item["source"], "Fallback Source")

    def test_returns_none_when_no_items(self):
        self.assertIsNone(_parse_top_item(EMPTY_RSS))


if __name__ == "__main__":
    unittest.main()
