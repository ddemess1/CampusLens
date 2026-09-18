"""Запуск: python -m unittest discover -s tests -v"""
import asyncio
import unittest

from app import pipeline
from app.categorize import categorize
from app.dedup import dhash, hamming
from app.models import Candidate, SourceHit, University
from app.resolver import NotFound
from app.sources.official import extract_preview_images
from app.verify import filter_reason, score, text_mentions_university
from tests import fakes


def run(coro):
    return asyncio.run(coro)


def cand(title, **kw):
    hit = kw.pop("hit", SourceHit("commons_category", "Commons", "u"))
    defaults = dict(key=title.lower(), title=title, thumb_url="t", full_url="f", page_url="p",
                    width=1600, height=1200, mime="image/jpeg", license="CC BY 4.0", sources=[hit])
    defaults.update(kw)
    return Candidate(**defaults)


UNI = University(qid="Q1", name="Nazarbayev University",
                 names=["Nazarbayev University", "Назарбаев Университет", "NU"],
                 lat=51.09, lon=71.40, city_name="Astana", city_lat=51.16, city_lon=71.47)


class CategorizeTests(unittest.TestCase):
    def test_library_by_title(self):
        c = cand("Nazarbayev University Library interior.jpg")
        categorize(c)
        self.assertEqual(c.category, "library")

    def test_subcategory_beats_generic_building(self):
        c = cand("Block 22 building.jpg",
                 hit=SourceHit("commons_subcategory", "Commons", "u", "Dormitories of Nazarbayev University"))
        categorize(c)
        self.assertEqual(c.category, "dormitory")

    def test_russian_keywords(self):
        c = cand("Студенты на выпускном КазНУ.jpg")
        categorize(c)
        self.assertEqual(c.category, "student_life")

    def test_city_subject(self):
        c = cand("Baiterek.jpg", subject="city")
        categorize(c)
        self.assertEqual(c.category, "city")

    def test_default_campus(self):
        c = cand("IMG_1234.jpg")
        categorize(c)
        self.assertEqual(c.category, "campus")
        self.assertIn("по умолчанию", c.category_reason)


class VerifyTests(unittest.TestCase):
    def test_filters(self):
        self.assertIsNotNone(filter_reason(cand("NU logo.png")))
        self.assertIsNotNone(filter_reason(cand("Map of campus.jpg")))
        self.assertIsNotNone(filter_reason(cand("x.svg", mime="image/svg+xml")))
        self.assertIsNotNone(filter_reason(cand("small.jpg", width=300, height=200)))
        self.assertIsNotNone(filter_reason(cand("nolicense.jpg", license="")))
        self.assertIsNone(filter_reason(cand("Planetarium of NU.jpg")))

    def test_mentions(self):
        self.assertTrue(text_mentions_university(cand("Nazarbayev University atrium.jpg"), UNI))
        self.assertTrue(text_mentions_university(cand("NU atrium.jpg"), UNI))
        self.assertFalse(text_mentions_university(cand("Astana street.jpg"), UNI))
        # «University» и название города сами по себе ничего не доказывают
        self.assertFalse(text_mentions_university(cand("Astana University building.jpg"), UNI))

    def test_score_geo_and_text(self):
        good = cand("Nazarbayev University main hall.jpg", lat=51.0902, lon=71.4001, date="2023")
        score(good, UNI)
        self.assertGreaterEqual(good.confidence, 0.75)
        self.assertEqual(good.status, "verified")

        far = cand("Some photo.jpg", lat=43.2, lon=76.9,
                   hit=SourceHit("commons_geo", "geo", "u"))
        score(far, UNI)
        self.assertEqual(far.status, "unverified")
        self.assertTrue(any(r.delta < 0 for r in far.reasons))

    def test_vision_rejects(self):
        c = cand("Nazarbayev University 1.jpg")
        c.vision = {"is_photo": True, "relevant": False, "note": "тарелка с едой", "category": None}
        score(c, UNI)
        self.assertLess(c.confidence, 0.5)


class DedupTests(unittest.TestCase):
    def test_recompressed_copy_is_close(self):
        a = fakes.IMAGES["https://upload.example/thumb/Al-Farabi_university_lecture_hall_2.jpg"]
        b = fakes.IMAGES["https://upload.example/thumb/Al-Farabi_university_lecture_hall_2_copy.jpg"]
        other = fakes.make_image(1234)
        self.assertLessEqual(hamming(dhash(a), dhash(b)), 6)
        self.assertGreater(hamming(dhash(a), dhash(other)), 6)

    def test_bad_bytes(self):
        self.assertIsNone(dhash(b"not an image"))


class OfficialSiteTests(unittest.TestCase):
    def test_extract_skips_logo(self):
        imgs, title = extract_preview_images(fakes.OFFICIAL_HTML, "https://www.kaznu.kz")
        self.assertEqual(imgs, ["https://www.kaznu.kz/img/campus-photo.jpg"])
        self.assertIn("KazNU", title)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        pipeline._profile_cache.clear()
        pipeline._query_cache.clear()

    def test_full_profile(self):
        stages = []

        async def emit(stage, state, detail):
            stages.append((stage, state))

        res = run(pipeline.build_profile(fakes.FakeFetcher(), "КазНУ", emit))
        u = res["university"]
        self.assertEqual(u["qid"], "Q1")  # философ Q2 отброшен
        self.assertEqual(u["city"], "Алматы")
        self.assertEqual(u["country"], "Казахстан")
        self.assertIsNotNone(u["distance_to_center_km"])

        titles = {p["title"] for p in res["photos"]}
        self.assertIn("KazNU main building.jpg", titles)
        self.assertNotIn("KazNU logo.svg", titles)
        self.assertNotIn("Tiny KazNU photo.jpg", titles)
        self.assertNotIn("KazNU no license.jpg", titles)
        self.assertNotIn("Random shop near Timiryazev street.jpg", titles)  # только близость по карте
        # из двух одинаковых аудиторий осталась одна
        lecture = [t for t in titles if "lecture hall" in t]
        self.assertEqual(len(lecture), 1)
        self.assertGreaterEqual(res["stats"]["duplicates"], 1)
        # главное здание пришло из трёх выборок и объединено
        self.assertGreaterEqual(res["stats"]["same_file_merged"], 2)

        cats = {p["title"]: p["category"] for p in res["photos"] + res["hidden"]}
        self.assertEqual(cats["KazNU reading room.jpg"], "library")
        self.assertEqual(cats["Hostel 5 building.jpg"], "dormitory")
        self.assertEqual(cats["Al-Farabi university lecture hall 2.jpg"], "education")
        self.assertEqual(cats["Almaty old square.jpg"], "city")

        for p in res["photos"]:
            self.assertIn(p["status"], ("verified", "likely"))
            self.assertTrue(p["source"]["url"])
            self.assertTrue(p["reasons"])

        self.assertTrue(any(p["source"]["type"] == "official_site" for p in res["photos"]))
        self.assertIn(("categorize", "done"), stages)
        self.assertTrue(any("AI-анализ" in w for w in res["warnings"]))

    def test_cache(self):
        f = fakes.FakeFetcher()
        run(pipeline.build_profile(f, "КазНУ"))
        n = len(f.calls)
        res = run(pipeline.build_profile(f, "КазНУ"))
        self.assertTrue(res["cached"])
        self.assertLess(len(f.calls) - n, 3)

    def test_source_down_is_reported(self):
        res = run(pipeline.build_profile(fakes.FakeFetcher(fail_hosts={"www.kaznu.kz"}), "КазНУ"))
        self.assertTrue(any("Официальный сайт" in w for w in res["warnings"]))
        self.assertGreater(len(res["photos"]), 0)

    def test_commons_down_still_answers(self):
        res = run(pipeline.build_profile(fakes.FakeFetcher(fail_hosts={"commons.wikimedia.org"}), "КазНУ"))
        self.assertEqual(res["university"]["qid"], "Q1")
        self.assertTrue(any("недоступен" in w for w in res["warnings"]))

    def test_unknown_name(self):
        with self.assertRaises(NotFound) as ctx:
            run(pipeline.build_profile(fakes.FakeFetcher(unknown_query=True), "Хогвартс"))
        self.assertEqual(ctx.exception.suggestion, "kaznu")


if __name__ == "__main__":
    unittest.main()


class VisionTests(unittest.TestCase):
    def test_parse_with_fences(self):
        from app.vision import parse_response
        out = parse_response('```json\n[{"i": 1, "is_photo": true, "category": "library", "relevant": true, "note": "читальный зал"}]\n```')
        self.assertEqual(out[0]["category"], "library")
        self.assertEqual(parse_response("мусор"), [])

    def test_vision_changes_category_and_score(self):
        from app import config

        class VisionFetcher(fakes.FakeFetcher):
            async def post_json(self, url, payload, headers=None, timeout=0):
                n = sum(1 for part in payload["contents"][0]["parts"] if "inline_data" in part)
                items = [{"i": i, "is_photo": True, "category": "student_life", "relevant": True,
                          "note": "студенты у входа"} for i in range(1, n + 1)]
                import json as _j
                return {"candidates": [{"content": {"parts": [{"text": _j.dumps(items)}]}}]}

        pipeline._profile_cache.clear()
        pipeline._query_cache.clear()
        old = config.GEMINI_API_KEY
        config.GEMINI_API_KEY = "test"
        try:
            res = run(pipeline.build_profile(VisionFetcher(), "КазНУ"))
        finally:
            config.GEMINI_API_KEY = old
        by = {p["title"]: p for p in res["photos"]}
        # без ключевых слов → категория от AI
        self.assertEqual(by["Превью сайта www.kaznu.kz: campus-photo.jpg"]["category"], "student_life")
        # сильные метаданные (подкатегория библиотеки) AI не перебивает
        self.assertEqual(by["KazNU reading room.jpg"]["category"], "library")
        self.assertTrue(any("AI:" in r["text"] for r in by["KazNU reading room.jpg"]["reasons"]))
        self.assertTrue(res["vision_enabled"])
