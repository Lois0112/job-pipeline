"""不需要额外依赖的单元测试：python -m unittest discover -s tests -v"""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import digest  # noqa: E402


def make_job(**overrides):
    job = {
        "title": "射频工程师",
        "company": "某公司",
        "city": "长沙",
        "track": "通信工程-射频",
        "deadline": "招满即止",
        "url": "https://example.com/job",
    }
    job.update(overrides)
    return job


class NormalizeTest(unittest.TestCase):
    def test_missing_field_raises(self):
        with self.assertRaises(digest.JobError) as ctx:
            digest.normalize({"title": "x", "company": "y"}, 0)
        self.assertIn("缺字段", str(ctx.exception))

    def test_bad_url_without_reason_raises(self):
        with self.assertRaises(digest.JobError):
            digest.normalize(make_job(url="boss直聘搜一下"), 0)

    def test_bad_url_with_reason_is_allowed(self):
        job = digest.normalize(
            make_job(url="", no_url_reason="需自行搜索公司官网"), 0
        )
        self.assertEqual(job["url"], "")
        self.assertEqual(job["no_url_reason"], "需自行搜索公司官网")


class DedupeTest(unittest.TestCase):
    def test_same_company_title_city_kept_once(self):
        jobs = [digest.normalize(make_job(), 0), digest.normalize(make_job(), 1)]
        kept, removed = digest.dedupe(jobs)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, 1)

    def test_different_city_is_not_duplicate(self):
        jobs = [
            digest.normalize(make_job(city="长沙"), 0),
            digest.normalize(make_job(city="太原"), 1),
        ]
        kept, removed = digest.dedupe(jobs)
        self.assertEqual((len(kept), removed), (2, 0))


class ExpireTest(unittest.TestCase):
    def test_iso_deadline_in_past_is_expired(self):
        today = dt.date(2026, 11, 4)
        self.assertTrue(digest.is_expired(make_job(deadline="2026-11-01"), today))
        self.assertFalse(digest.is_expired(make_job(deadline="2026-12-31"), today))

    def test_free_text_deadline_never_expires(self):
        today = dt.date(2026, 11, 4)
        self.assertFalse(digest.is_expired(make_job(deadline="招满即止"), today))


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.jobs = [
            digest.normalize(make_job(track="互联网-前端/测试"), 0),
            digest.normalize(make_job(title="网络优化", track="通信工程-测试/网络优化"), 1),
        ]
        self.meta = {"date": "2026-11-04", "scope": ["长沙"], "advice": ["先去投国网"]}

    def test_markdown_has_link_and_ordered_sections(self):
        md = digest.render_markdown(self.jobs, self.meta, {"deduped": 0, "expired": 0})
        self.assertIn("[投递页面](https://example.com/job)", md)
        # 通信工程必须排在互联网前面
        self.assertLess(md.index("通信工程"), md.index("互联网"))

    def test_html_escapes_title(self):
        jobs = [digest.normalize(make_job(title="<script>x</script>"), 0)]
        out = digest.render_html(jobs, self.meta, {"deduped": 0, "expired": 0})
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


class EndToEndTest(unittest.TestCase):
    def test_build_writes_both_files(self):
        payload = {
            "date": "2026-11-04",
            "jobs": [make_job(), make_job(city="太原")],
            "advice": ["投国网"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "jobs.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            result = digest.build(src, os.path.join(tmp, "out"), today=dt.date(2026, 11, 4))
            self.assertEqual(result["total"], 2)
            self.assertTrue(os.path.isfile(result["markdown"]))
            self.assertTrue(os.path.isfile(result["html"]))


if __name__ == "__main__":
    unittest.main()
