import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote

from search_engine import SearchIndex, highlight, parse_query, tokenize
from search_engine.server import create_server
from search_engine.tokenizer import edit_distance

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "documents.json")


def ids(result):
    return [h["id"] for h in result["hits"]]


class TokenizerTests(unittest.TestCase):
    def test_cyrillic_and_case(self):
        self.assertEqual([t.text for t in tokenize("Хөвсгөл НУУР, Үндэсний!")], ["хөвсгөл", "нуур", "үндэсний"])

    def test_offsets(self):
        tok = tokenize("сайн байна уу")[1]
        self.assertEqual((tok.position, tok.start, tok.end), (1, 5, 10))

    def test_edit_distance(self):
        self.assertEqual(edit_distance("хархорм", "хархорум", 2), 1)
        self.assertEqual(edit_distance("abc", "xyz", 1), 2)


class QueryParserTests(unittest.TestCase):
    def test_parse(self):
        clauses = parse_query('монгол "нийслэл хот" -улаанбаатар title:хаан http:x', ["title", "body"])
        self.assertEqual(clauses[0].terms, ["монгол"])
        self.assertTrue(clauses[1].phrase)
        self.assertEqual(clauses[1].terms, ["нийслэл", "хот"])
        self.assertTrue(clauses[2].negate)
        self.assertEqual(clauses[3].field, "title")
        self.assertEqual([c.terms[0] for c in clauses[4:]], ["http", "x"])


class SearchIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = SearchIndex.load(DATA)

    def test_basic_search(self):
        self.assertEqual(ids(self.index.search("наадам"))[0], "naadam")

    def test_title_boost(self):
        self.assertEqual(ids(self.index.search("чингис"))[0], "chinggis-khaan")

    def test_prefix_matches_suffixed_words(self):
        found = set(ids(self.index.search("ном", limit=20)))
        self.assertTrue({"secret-history", "mongol-script", "library"} <= found)

    def test_fuzzy_and_suggestion(self):
        result = self.index.search("Хархорм")
        self.assertEqual(ids(result)[0], "karakorum")
        self.assertEqual(result["suggestion"], "хархорум")

    def test_no_suggestion_for_known_words(self):
        self.assertIsNone(self.index.search("говь")["suggestion"])

    def test_phrase(self):
        self.assertEqual(ids(self.index.search('"нийслэл хот"')), ["karakorum"])

    def test_negation(self):
        self.assertEqual(ids(self.index.search("нийслэл -улаанбаатар")), ["karakorum"])

    def test_field_restriction(self):
        self.assertEqual(ids(self.index.search("title:хаан")), ["chinggis-khaan"])

    def test_and_then_or_fallback(self):
        both = self.index.search("чингис хаан")
        self.assertEqual(both["mode"], "and")
        either = self.index.search("хөвсгөл тэмээ")
        self.assertEqual(either["mode"], "or")
        self.assertEqual(set(ids(either)), {"khuvsgul", "gobi"})

    def test_filters_and_facets(self):
        result = self.index.search("", filters={"category": "технологи"}, facets=["category"])
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["facets"]["category"], [{"value": "Технологи", "count": 3}])

    def test_pagination(self):
        page1 = self.index.search("уламжлал", limit=2)
        page2 = self.index.search("уламжлал", limit=2, offset=2)
        self.assertGreater(page1["total"], 2)
        self.assertFalse(set(ids(page1)) & set(ids(page2)))

    def test_highlight_snippet(self):
        hit = self.index.search("бөх")["hits"][0]
        self.assertIn("<mark>бөх</mark>", hit["highlights"]["body"])

    def test_autocomplete(self):
        self.assertIn("монгол гэр", self.index.autocomplete("монгол гэ"))
        self.assertEqual(self.index.autocomplete("монгол "), [])

    def test_empty_query(self):
        self.assertEqual(self.index.search("")["total"], 0)
        self.assertEqual(self.index.search("   !!! ")["total"], 0)


class MutationTests(unittest.TestCase):
    def test_update_and_remove(self):
        index = SearchIndex()
        index.add({"id": "1", "title": "Алим", "body": "улаан алим"})
        index.add({"id": "1", "title": "Лийр", "body": "шар лийр"})
        self.assertEqual(len(index), 1)
        self.assertEqual(index.search("алим")["total"], 0)
        self.assertEqual(ids(index.search("лийр")), ["1"])
        self.assertTrue(index.remove("1"))
        self.assertFalse(index.remove("1"))
        self.assertEqual(index.stats()["terms"], 0)

    def test_generated_id(self):
        index = SearchIndex()
        doc_id = index.add({"title": "Шинэ"})
        self.assertIn(doc_id, index)

    def test_save_and_load(self):
        index = SearchIndex()
        index.add({"id": "a", "title": "Тэмээ", "tags": ["говь"]})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "docs.json")
            index.save(path)
            loaded = SearchIndex.load(path)
        self.assertEqual(ids(loaded.search("говь")), ["a"])

    def test_highlight_escapes_html(self):
        out = highlight("<b>ном</b> & ном", {"ном"}, None)
        self.assertEqual(out, "&lt;b&gt;<mark>ном</mark>&lt;/b&gt; &amp; <mark>ном</mark>")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_path = os.path.join(cls.tmp.name, "docs.json")
        index = SearchIndex.load(DATA)
        cls.server = create_server(index, port=0, data_path=cls.data_path)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def request(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def test_index_page(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Nyamka", body)

    def test_search_endpoint(self):
        status, body = self.request("/api/search?q=" + quote("морин хуур"))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["hits"][0]["id"], "morin-khuur")
        self.assertIn("category", data["facets"])

    def test_bad_limit(self):
        self.assertEqual(self.request("/api/search?q=x&limit=abc")[0], 400)

    def test_add_get_delete(self):
        status, body = self.request("/api/documents", "POST", {"id": "test-doc", "title": "Шатар", "body": "Шатрын тэмцээн"})
        self.assertEqual(status, 201)
        self.assertTrue(os.path.exists(self.data_path))
        data = json.loads(self.request("/api/search?q=" + quote("шатар"))[1])
        self.assertEqual(data["hits"][0]["id"], "test-doc")
        self.assertEqual(self.request("/api/documents/test-doc")[0], 200)
        self.assertEqual(self.request("/api/documents/test-doc", "DELETE")[0], 200)
        self.assertEqual(self.request("/api/documents/test-doc")[0], 404)

    def test_invalid_post(self):
        self.assertEqual(self.request("/api/documents", "POST", [1, 2])[0], 400)


if __name__ == "__main__":
    unittest.main()
