"""Topic quality, provenance, bounded inference and upgrade compatibility."""
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from topics import MAX_CANDIDATES, MAX_PASSAGES, candidates_from, discover_topic, fold
from semantic import ENCODER, SemanticUnavailable, numpy_module
from semantic_catalog import migrate, theme_subjects
import test_semantic

ECOLOGY = (
    "Ecologia de populações\n"
    "A ecologia de populações analisa a dinâmica populacional, a capacidade de suporte e a competição entre espécies. "
    "A dinâmica populacional depende das taxas de natalidade, mortalidade e migração. "
    "A ecologia de populações explica o crescimento populacional e as interações entre predadores e presas. "
    "A capacidade de suporte limita a densidade das populações nos ecossistemas. "
)
COOKING = (
    "This cooking book teaches delicious recipes for everyday meals. Fresh vegetables are chopped, seasoned and cooked in a kitchen. "
    "The baker mixes flour, yeast and water to prepare bread dough. Detailed instructions explain baking temperatures, "
    "ingredient measurements and techniques for preparing tasty food."
)
ROME = (
    "The history of ancient Rome explores the Roman empire, emperors and political institutions. It studies military campaigns, "
    "everyday life, ancient culture, archaeology, historic documents, cities and society in classical antiquity. "
    "This textbook describes historical events and explains how civilizations evolved over centuries."
)


class TopicTests(unittest.TestCase):
    def test_short_and_repetitive_text_abstains(self):
        for text in ("", "Muito pouco texto.", "populações dinâmica " * 100):
            result = discover_topic(text, None)
            self.assertEqual(result["status"], "insufficient")
            self.assertFalse(result["label"])

    def test_statistical_fallback_is_explicit_and_grounded(self):
        result = discover_topic(ECOLOGY, None)
        self.assertEqual(result["status"], "suggested")
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["label"], "Ecologia de populações")
        for item in result["keyphrases"]:
            self.assertIn(fold(item["phrase"]), fold(ECOLOGY))
            self.assertTrue(any(fold(item["phrase"]) in fold(text) for text in item["evidence"]))

    def test_neural_failure_preserves_statistical_suggestion(self):
        class BrokenEncoder:
            def encode_texts(self, texts):
                raise SemanticUnavailable("offline model absent")
        result = discover_topic(ECOLOGY, BrokenEncoder())
        self.assertEqual(result["status"], "suggested")
        self.assertIn("offline", result["warning"])
        self.assertTrue(result["label"])

    def test_candidates_do_not_cross_punctuation_or_sentence_boundaries(self):
        _, candidates = candidates_from("Roman empire, delicious recipes. Fresh vegetables; political institutions. " * 4)
        names = {item["phrase"] for item in candidates}
        self.assertNotIn("empire delicious", names)
        self.assertNotIn("recipes fresh", names)
        self.assertIn("roman empire", names)

    def test_bibliography_and_copyright_do_not_name_the_book(self):
        body = ECOLOGY * 6
        _, candidates = candidates_from(body + "\nReferences\nQuantum electrodynamics and black holes. " * 2)
        self.assertFalse(any("quantum" in item["phrase"] for item in candidates))
        self.assertEqual(discover_topic("Copyright all rights reserved. " * 30 + ECOLOGY, None)["label"], "Ecologia de populações")

    def test_bounded_budget_spans_long_document(self):
        passages, candidates = candidates_from(ECOLOGY * 200 + "Finalmarker astronomy stars galaxies.")
        self.assertLessEqual(len(passages), MAX_PASSAGES)
        self.assertLessEqual(len(candidates), MAX_CANDIDATES)
        self.assertIn("Finalmarker", passages[-1])

    def test_running_headers_do_not_become_topic_evidence(self):
        text = "\n".join("Authorname Some Journal 2024 page " + str(i) + "\n" + ECOLOGY for i in range(12))
        _, candidates = candidates_from(text)
        self.assertTrue(all(item["count"] == 1 for item in candidates
                            if "authorname" in item["phrase"] or "journal" in item["phrase"]))

    def test_author_credits_are_not_topic_candidates(self):
        text = ("AUTORES\nJúlio César Ferreira Lima\nMaria Helena Gabriel\n\n" + ECOLOGY + "\n") * 3
        _, candidates = candidates_from(text)
        self.assertFalse(any("julio" in item["key"] or "maria" in item["key"] for item in candidates))

    def test_acronym_expansion_is_learned_from_content(self):
        text = ("Quantum key distribution (QKD) enables secure communication using quantum physics. "
                "Quantum key distribution protects secret keys between distant participants. "
                "QKD detects eavesdropping through measurements of photons and polarization. "
                "Quantum key distribution supports encryption and communication security.")
        _, candidates = candidates_from(text)
        self.assertTrue(any(item["key"] == "quantum key distribution" and item["acronym"] == "qkd" for item in candidates))

    def test_empty_form_fields_are_not_themes(self):
        text = "Contact Name: _____________\nAddress: _____________\nTelephone: _____________\n\n" + ECOLOGY
        _, candidates = candidates_from(text)
        self.assertFalse(any("contact" in item["key"] or "telephone" in item["key"] or "address" in item["key"] for item in candidates))

    def test_function_words_are_not_topic_candidates(self):
        _, candidates = candidates_from("Não não não. Above below above. " * 20 + ECOLOGY)
        self.assertFalse(any(set(item["key"].split()) & {"nao", "above", "below"} for item in candidates))

    def test_old_theme_schema_migrates_without_changing_ids(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("CREATE TABLE semantic_themes(number INTEGER PRIMARY KEY AUTOINCREMENT); INSERT INTO semantic_themes DEFAULT VALUES;")
        migrate(db)
        self.assertEqual(theme_subjects(db)[0]["name"], "Tema 1")
        db.execute("UPDATE semantic_themes SET label='Ecologia' WHERE number=1")
        migrate(db)
        self.assertEqual(theme_subjects(db)[0]["label"], "Ecologia")

    def test_real_multilingual_topics_and_mmr_provenance(self):
        try:
            ENCODER.load()
        except SemanticUnavailable as error:
            self.skipTest(str(error))
        for text, expected in ((ECOLOGY, "ecologia de populacoes"), (COOKING, "cooking"), (ROME, "ancient rome")):
            with self.subTest(expected=expected):
                result = discover_topic(text, ENCODER)
                self.assertEqual(result["status"], "ready")
                self.assertEqual(fold(result["label"]), expected)
                self.assertIn("MMR", result["method"])
                keys = [fold(item["phrase"]) for item in result["keyphrases"]]
                for index, phrase in enumerate(keys):
                    self.assertIn(phrase, fold(text))
                    for other in keys[index + 1:]:
                        self.assertNotIn(f" {phrase} ", f" {other} ")
                        self.assertNotIn(f" {other} ", f" {phrase} ")

    def test_batch_encoder_keeps_independent_unit_vectors_and_overflows(self):
        try:
            ENCODER.load()
        except SemanticUnavailable as error:
            self.skipTest(str(error))
        np = numpy_module()
        texts = [ECOLOGY * 3, COOKING]
        batch = ENCODER.encode_texts(texts)
        self.assertEqual(batch.shape, (2, 384))
        np.testing.assert_allclose(np.linalg.norm(batch, axis=1), [1, 1], atol=1e-5)
        for i, text in enumerate(texts):
            # Dynamic ONNX quantization depends slightly on batch composition.
            self.assertGreater(float(batch[i] @ ENCODER.encode_texts([text])[0]), .99)


class TopicPersistenceTests(unittest.TestCase):
    setUp = test_semantic.SemanticCatalogTests.setUp
    add = test_semantic.SemanticCatalogTests.add

    def test_cached_profile_and_stable_shelf_labels(self):
        profile = {"version": "test", "status": "ready", "label": "História romana", "keyphrases": [], "needs_review": False}
        with patch("semantic_catalog.discover_topic", return_value=profile) as discover:
            first = self.add("history", [1, 0], text=ROME)
            second = self.add("copy", [1, 0], text=ROME)
        self.assertEqual(discover.call_count, 1)
        self.assertEqual(first["category"], second["category"])
        self.assertEqual(theme_subjects(self.db)[0]["label"], "História romana")
        self.assertEqual(second["analysis"]["topic"]["label"], "História romana")
        self.assertEqual(self.db.execute("SELECT count(*) FROM semantic_topics").fetchone()[0], 1)
        self.db.execute("UPDATE books SET manual_category='Teoria dos grafos',category='Teoria dos grafos' WHERE id='copy'")
        migrate(self.db)
        self.assertEqual(self.db.execute("SELECT category FROM books WHERE id='copy'").fetchone()[0], "Teoria dos grafos")


if __name__ == "__main__":
    unittest.main()
