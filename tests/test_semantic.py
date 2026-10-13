"""Semantic grouping, persistence and real multilingual CPU inference."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from indexer import Cancelled, connect, save_book
from semantic import DIMENSIONS, ENCODER, SemanticUnavailable, normalize, numpy_module, text_chunks
from semantic_catalog import SemanticCatalog, rebuild_centroids, sync_member, themes

BODY = "Cities civilizations cultures historic societies customs traditions documents archives research people buildings places events periods sources discoveries ancient modern medieval evidence literature languages knowledge. "


class FakeEncoder:
    def __init__(self):
        self.vectors = {}
        self.calls = 0

    def embedding_livro(self, text):
        self.calls += 1
        return self.vectors[text], 1


class SemanticCatalogTests(unittest.TestCase):
    def setUp(self):
        try:
            self.np = numpy_module()
        except SemanticUnavailable as error:
            self.skipTest(str(error))
        temporary = tempfile.TemporaryDirectory(prefix="atlas-semantic-test-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.db = connect(self.folder)
        self.addCleanup(self.db.close)
        self.encoder = FakeEncoder()
        self.catalog = SemanticCatalog(self.db, self.encoder)

    def add(self, name, direction, lexical="Revisar", book_id=None, text=None):
        text = text or BODY + name
        vector = self.np.zeros(DIMENSIONS, dtype=self.np.float32)
        vector[:len(direction)] = direction
        self.encoder.vectors[text] = normalize(vector)
        decision = self.catalog.classify(text, {"category": lexical, "confidence": .8,
                                               "reason": "Conceitos originais.", "analysis": {}}, book_id or name)
        book = {"id": book_id or name, "path": str(self.folder / (book_id or name)), "title": name,
                "category": decision["category"], "confidence": decision["confidence"],
                "reason": decision["reason"], "status": "indexed", "manual_category": "",
                "analysis": json.dumps(decision["analysis"])}
        save_book(self.db, book)
        return decision

    def test_new_topics_and_threshold_equality(self):
        first = self.add("history", [1, 0])
        close = self.add("related", [.68, (1 - .68**2)**.5])
        distant = self.add("cooking", [0, 0, 1])
        self.assertEqual(first["category"], "Tema 1")
        self.assertEqual(close["category"], "Tema 1")
        self.assertEqual(close["analysis"]["semantic"]["source"], "centroid")
        self.assertEqual(distant["category"], "Tema 2")
        self.assertEqual(themes(self.db), ["Tema 1", "Tema 2"])

    def test_persistence_between_process_connections(self):
        self.add("history", [1, 0])
        other = connect(self.folder)
        try:
            catalog = SemanticCatalog(other, self.encoder)
            text = BODY + "new history"
            self.encoder.vectors[text] = self.encoder.vectors[BODY + "history"]
            result = catalog.classify(text, {"category": "Revisar", "confidence": 0, "reason": "", "analysis": {}}, "new")
            self.assertEqual(result["category"], "Tema 1")
        finally:
            other.close()

    def test_duplicate_content_does_not_move_or_weight_centroid(self):
        first = self.add("original", [1, 0])
        duplicate = self.add("copy", [1, 0], text=BODY + "original")
        self.assertEqual(duplicate["category"], first["category"])
        self.assertEqual(duplicate["analysis"]["semantic"]["source"], "identical_content")
        self.assertEqual(self.db.execute("SELECT quantity FROM semantic_centroids").fetchone()[0], 1)
        self.assertEqual(self.encoder.calls, 1)

    def test_reclassifying_singleton_excludes_self_and_retains_theme(self):
        first = self.add("history", [1, 0])
        again = self.add("history", [1, 0])
        self.assertEqual(first["category"], again["category"])
        self.assertEqual(again["analysis"]["semantic"]["candidates"], [])
        self.assertEqual(again["analysis"]["semantic"]["source"], "retained_theme")
        self.assertEqual(len(themes(self.db)), 1)
        self.assertEqual(self.encoder.calls, 1)

    def test_centroid_is_exact_mean_of_unit_vectors(self):
        for name, direction in [("a", [1, 0]), ("b", [0, 1]), ("c", [0, 1])]:
            self.add(name, direction, "Topologia")
        row = self.db.execute("SELECT vector_sum,quantity FROM semantic_centroids WHERE category='Topologia'").fetchone()
        total = self.np.frombuffer(row[0], dtype="<f4")
        self.assertEqual(row[1], 3)
        self.np.testing.assert_allclose(total[:2], [1, 2])
        self.np.testing.assert_allclose(normalize(total)[:2], [1 / 5**.5, 2 / 5**.5], rtol=1e-6)

    def test_changed_content_removes_old_membership(self):
        self.add("history", [1, 0], book_id="book")
        changed = self.add("cooking", [0, 1], book_id="book")
        self.assertEqual(changed["category"], "Tema 2")
        self.assertEqual([row[0] for row in self.db.execute("SELECT category FROM semantic_centroids")], ["Tema 2"])

    def test_manual_correction_and_missing_books_update_centroids(self):
        self.add("book", [1, 0])
        self.db.execute("UPDATE books SET category='Topologia',manual_category='Topologia' WHERE id='book'")
        sync_member(self.db, "book")
        rebuild_centroids(self.db)
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT category FROM semantic_centroids").fetchone()[0], "Topologia")
        self.db.execute("UPDATE books SET missing=1 WHERE id='book'")
        rebuild_centroids(self.db)
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT count(*) FROM semantic_centroids").fetchone()[0], 0)
        self.db.execute("UPDATE books SET missing=0 WHERE id='book'")
        sync_member(self.db, "book")
        rebuild_centroids(self.db)
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT quantity FROM semantic_centroids").fetchone()[0], 1)

    def test_unavailable_model_preserves_lexical_decision(self):
        class BrokenEncoder:
            def embedding_livro(self, _text):
                raise SemanticUnavailable("fixture")
        result = SemanticCatalog(self.db, BrokenEncoder()).classify(BODY, {
            "category": "Topologia", "confidence": .8, "reason": "Original", "analysis": {}}, "book")
        self.assertEqual(result["category"], "Topologia")
        self.assertEqual(result["analysis"]["semantic"]["status"], "unavailable")
        self.assertEqual(themes(self.db), [])

    def test_short_text_does_not_create_a_theme(self):
        result = self.catalog.classify("empty text", {"category": "Revisar", "analysis": {}}, "book")
        self.assertEqual(result["category"], "Revisar")
        self.assertEqual(themes(self.db), [])

    def test_cancellation_rolls_back_book_theme_and_centroid_together(self):
        self.add("history", [1, 0])
        with patch("indexer.rebuild_centroids", side_effect=Cancelled):
            with self.assertRaises(Cancelled):
                self.add("cooking", [0, 1])
        self.assertEqual(self.db.execute("SELECT count(*) FROM books").fetchone()[0], 1)
        self.assertEqual(themes(self.db), ["Tema 1"])
        self.assertEqual(self.db.execute("SELECT quantity FROM semantic_centroids").fetchone()[0], 1)


class RealEncoderTests(unittest.TestCase):
    def test_multilingual_model_and_unrelated_subjects(self):
        try:
            ENCODER.load()
        except SemanticUnavailable as error:
            self.skipTest(str(error))
        english = "The history of ancient Rome covers the Roman empire, emperors, political institutions, wars and everyday life in classical antiquity."
        portuguese = "A história da Roma antiga abrange o império romano, imperadores, instituições políticas, guerras e a vida cotidiana na antiguidade clássica."
        cooking = "Cooking recipes explain preparation of fresh vegetables, seasonings, baking bread and making delicious meals in a kitchen."
        a, _ = ENCODER.embedding_livro(english)
        b, _ = ENCODER.embedding_livro(portuguese)
        c, _ = ENCODER.embedding_livro(cooking)
        self.assertEqual(a.shape, (384,))
        self.assertAlmostEqual(float(numpy_module().linalg.norm(a)), 1, places=5)
        self.assertGreater(float(a @ b), .68)
        self.assertLess(float(a @ c), .68)

    def test_chunk_budget_spans_entire_book_and_rejects_empty_text(self):
        words = [f"word{i}" for i in range(20000)]
        chunks = text_chunks(" ".join(words))
        self.assertEqual(len(chunks), 96)
        self.assertTrue(chunks[0].startswith("word0 "))
        self.assertTrue(chunks[-1].endswith("word19999"))
        with self.assertRaises(ValueError):
            text_chunks(" \n ")


if __name__ == "__main__":
    unittest.main()
