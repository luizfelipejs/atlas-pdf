"""Open-set categories: representation, calibration, induction, naming and persistence."""
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from indexer import connect, save_book
import categories as cat
from semantic import DIMENSIONS, SemanticUnavailable, normalize, numpy_module
from semantic_catalog import SemanticCatalog, migrate, theme_subjects, themes, sync_member, rebuild_centroids

WORDS = ("analysis models methods data structure results theory practice examples questions evidence "
         "systems processes values measures changes problems solutions techniques variables").split()


def body(name):
    rng = random.Random(name)
    return " ".join(rng.choice(WORDS) for _ in range(60)) + " " + name


class FakeEncoder:
    """Body vectors chosen by the test; no phrase encoder (body-only space)."""

    def __init__(self):
        self.vectors = {}

    def embedding_livro(self, text):
        return self.vectors[text], 1


def lexical(category, title_match=False, decision="classified", candidates=None):
    names = candidates or [category]
    return {"category": category if decision == "classified" else "Revisar", "confidence": .7,
            "reason": "Conceitos originais.",
            "analysis": {"decision": decision,
                         "candidates": [{"category": name, "title_match": title_match and i == 0}
                                        for i, name in enumerate(names)]}}


class CollectionTests(unittest.TestCase):
    """Synthetic library with the anisotropy seen in real MiniLM vectors."""

    def setUp(self):
        try:
            self.np = numpy_module()
        except SemanticUnavailable as error:
            self.skipTest(str(error))
        rng = self.np.random.default_rng(7)
        # A large shared component makes every raw cosine about 0.9, so the
        # initial 0.68 threshold cannot separate subjects in this library.
        self.offset = rng.normal(size=DIMENSIONS) * 3
        self.directions = {name: rng.normal(size=DIMENSIONS) for name in ("prob", "linear", "finance")}
        self.rng = rng

    def open(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-categories-test-")
        self.addCleanup(temporary.cleanup)
        db = connect(Path(temporary.name))
        self.addCleanup(db.close)
        encoder = FakeEncoder()
        return db, encoder, SemanticCatalog(db, encoder), Path(temporary.name)

    def vector(self, group, index):
        noise = self.np.random.default_rng(zlib.crc32(f"{group}:{index}".encode())).normal(size=DIMENSIONS) * .35
        return normalize(self.offset + self.directions[group] + noise)

    def library(self):
        books = []
        for i in range(12):
            books.append((f"prob{i}", "prob", lexical("Probabilidade", title_match=i < 6),
                          {"title": f"Probability theory notes {i}", "headings": [{"text": "Random variables", "level": 1}]}))
            books.append((f"lin{i}", "linear", lexical("Álgebra linear", title_match=i < 6),
                          {"title": f"Linear algebra lectures {i}", "headings": [{"text": "Vector spaces", "level": 1}]}))
        for i in range(8):
            # A new subject whose mathematical tools look like probability.
            decision = (lexical("Probabilidade") if i < 3 else
                        lexical("Revisar", decision="ambiguous", candidates=["Probabilidade", "Otimização"]))
            books.append((f"fin{i}", "finance", decision,
                          {"status": "ready", "kind": "livro", "title": f"Volatility models and option pricing, part {i}",
                           "headings": [{"text": "Stochastic volatility", "level": 1},
                                        {"text": "Option pricing", "level": 1}], "toc": ["Implied volatility surface"]}))
        return books

    def add(self, db, encoder, catalog, folder, name, group, decision, structure, manual=""):
        text = body(name)
        encoder.vectors[text] = self.vector(group, name)
        decision = json.loads(json.dumps(decision))
        result = catalog.classify(text, decision, name, structure=structure)
        book = {"id": name, "path": str(folder / name), "title": structure["title"], "category": manual or result["category"],
                "confidence": result["confidence"], "reason": result["reason"], "status": "indexed",
                "manual_category": manual, "analysis": json.dumps(result["analysis"])}
        save_book(db, book)
        return result

    def build(self, order=None, manual=None):
        db, encoder, catalog, folder = self.open()
        books = self.library()
        if order:
            random.Random(order).shuffle(books)
        for name, group, decision, structure in books:
            self.add(db, encoder, catalog, folder, name, group, decision, structure, (manual or {}).get(name, ""))
        return db, catalog

    @staticmethod
    def categories(db):
        return {row[0]: row[1] for row in db.execute("SELECT id,category FROM books")}

    @staticmethod
    def partition(categories):
        groups = {}
        for book, category in categories.items():
            groups.setdefault(category, set()).add(book)
        return sorted(sorted(items) for items in groups.values())

    def test_new_subject_becomes_named_category_outside_math_shelves(self):
        db, catalog = self.build()
        catalog.consolidate()
        result = self.categories(db)
        finance = {result[f"fin{i}"] for i in range(8)}
        self.assertEqual(len(finance), 1)
        theme = finance.pop()
        self.assertRegex(theme, r"^Tema \d+$")
        self.assertTrue(all(result[f"prob{i}"] == "Probabilidade" for i in range(12)))
        self.assertTrue(all(result[f"lin{i}"] == "Álgebra linear" for i in range(12)))
        subject = next(item for item in theme_subjects(db) if item["name"] == theme)
        self.assertIn(cat.fold(subject["label"]), {"option pricing", "volatility models", "stochastic volatility",
                                                   "volatility models and option pricing"})
        self.assertEqual(subject["family"], "Finanças e economia")
        analysis = json.loads(db.execute("SELECT analysis FROM books WHERE id='fin0'").fetchone()[0])
        self.assertEqual(analysis["semantic"]["structure"]["source"], "pdf")
        self.assertEqual(analysis["semantic"]["lexical"], "Probabilidade")

    def test_result_does_not_depend_on_reading_order(self):
        first, catalog = self.build(order=1)
        catalog.consolidate()
        second, catalog = self.build(order=2)
        catalog.consolidate()
        self.assertEqual(self.partition(self.categories(first)), self.partition(self.categories(second)))

    def test_consolidation_is_idempotent_and_keeps_theme_ids(self):
        db, catalog = self.build()
        catalog.consolidate()
        before = self.categories(db)
        self.assertEqual(catalog.consolidate(), [])
        self.assertEqual(self.categories(db), before)
        self.assertEqual(SemanticCatalog(db, catalog.encoder).consolidate(), [])

    def test_manual_choice_is_never_moved(self):
        db, catalog = self.build(manual={"fin5": "Estatística"})
        changes = catalog.consolidate()
        self.assertEqual(self.categories(db)["fin5"], "Estatística")
        self.assertNotIn("fin5", {change["id"] for change in changes})

    def test_copies_do_not_change_category_names(self):
        db, catalog = self.build()
        catalog.consolidate()
        before = theme_subjects(db)
        original = dict(db.execute("SELECT * FROM books WHERE id='fin0'").fetchone())
        profile = dict(db.execute("SELECT * FROM semantic_profiles WHERE book_id='fin0'").fetchone())
        for number in range(12):
            clone = dict(original, id=f"copy{number}", path=f"/copies/finance-{number}.pdf")
            save_book(db, clone)
            copied = dict(profile, book_id=clone["id"])
            db.execute(f"INSERT INTO semantic_profiles ({','.join(copied)}) VALUES ({','.join('?' for _ in copied)})",
                       tuple(copied.values()))
        db.commit()
        catalog.consolidate()
        self.assertEqual(theme_subjects(db), before)

    def test_absorbed_theme_is_retired_but_manual_reference_survives(self):
        db, catalog = self.build()
        catalog.consolidate()
        active = set(themes(db))
        used = set(self.categories(db).values())
        self.assertTrue(active <= used)
        # Themes created during consolidation must not be retired while in use.
        self.assertTrue({name for name in used if name.startswith("Tema ")} <= active)
        number = db.execute("INSERT INTO semantic_themes(label) VALUES ('Escolha manual')").lastrowid
        db.execute("UPDATE books SET category=?,manual_category=? WHERE id='lin0'", (f"Tema {number}",) * 2)
        sync_member(db, "lin0")
        rebuild_centroids(db)
        db.commit()
        catalog.consolidate()
        self.assertIn(f"Tema {number}", themes(db))
        self.assertEqual(self.categories(db)["lin0"], f"Tema {number}")

    def test_calibrated_mode_starts_after_enough_contents(self):
        db, encoder, catalog, folder = self.open()
        first = self.add(db, encoder, catalog, folder, "prob0", "prob", lexical("Probabilidade"),
                         {"title": "Probability"})
        self.assertEqual(first["analysis"]["semantic"]["mode"], "inicial")
        for name, group, decision, structure in self.library():
            last = self.add(db, encoder, catalog, folder, name, group, decision, structure)
        self.assertEqual(last["analysis"]["semantic"]["mode"], "calibrado")
        self.assertIn("threshold", last["analysis"]["semantic"]["candidates"][0])

    def test_structure_none_uses_text_proxy(self):
        db, encoder, catalog, folder = self.open()
        text = "Sumário\n1 Derivadas . . . . . . . . 3\n2 Integrais . . . . . . . . 9\n\f" + body("x")
        encoder.vectors[text] = normalize(self.offset)
        result = catalog.classify(text, lexical("Cálculo"), "x")
        self.assertEqual(result["category"], "Cálculo")
        self.assertEqual(result["analysis"]["semantic"]["structure"]["source"], "texto")
        terms = json.loads(db.execute("SELECT terms FROM semantic_profiles WHERE book_id='x'").fetchone()[0])
        self.assertIn("derivadas", terms["structure"])


class AlgorithmTests(unittest.TestCase):
    def setUp(self):
        try:
            self.np = numpy_module()
        except SemanticUnavailable as error:
            self.skipTest(str(error))

    def naive(self, similarity, minimum):
        clusters = [[i] for i in range(len(similarity))]
        while len(clusters) > 1:
            best = None
            for a in range(len(clusters)):
                for b in range(a + 1, len(clusters)):
                    value = similarity[self.np.ix_(clusters[a], clusters[b])].mean()
                    if best is None or value > best[0]:
                        best = (value, a, b)
            if best[0] < minimum:
                break
            clusters[best[1]] += clusters.pop(best[2])
        return sorted(sorted(items) for items in clusters)

    def test_nearest_neighbour_chain_matches_naive_average_linkage(self):
        rng = self.np.random.default_rng(3)
        points = cat._normalize_rows(self.np, rng.normal(size=(40, 6)) + self.np.repeat(rng.normal(size=(5, 6)) * 2, 8, axis=0))
        similarity = points @ points.T
        merges = cat.average_linkage(self.np, similarity)
        self.assertEqual(len(merges), 39)
        for minimum in (.2, .4, .6, .8):
            labels = cat.cut(40, merges, minimum)
            groups = {}
            for index, label in enumerate(labels):
                groups.setdefault(label, []).append(index)
            self.assertEqual(sorted(sorted(items) for items in groups.values()), self.naive(similarity, minimum))

    def test_centering_removes_shared_component(self):
        rng = self.np.random.default_rng(1)
        shared = rng.normal(size=32) * 4
        a, b = rng.normal(size=32), rng.normal(size=32)
        rows = [shared + a + rng.normal(size=32) * .1 for _ in range(5)] + [shared + b + rng.normal(size=32) * .1 for _ in range(5)]
        raw = cat._normalize_rows(self.np, self.np.stack(rows))
        self.assertGreater(float(raw[0] @ raw[9]), .8)
        space = cat.combine(self.np, {"body": rows})
        self.assertLess(float(space[0] @ space[9]), 0)
        self.assertGreater(float(space[0] @ space[1]), .8)

    def test_missing_components_are_skipped_per_document(self):
        rows = [self.np.eye(4)[i % 2] + 1 for i in range(4)]
        space = cat.combine(self.np, {"body": rows, "structure": [None, rows[1], None, rows[3]]})
        self.np.testing.assert_allclose(self.np.linalg.norm(space, axis=1), 1, atol=1e-6)

    def test_threshold_is_specific_to_group_and_shrinks_small_groups(self):
        prior = (.3, .1)
        tight = cat.threshold((.7, .05, 40), prior)
        loose = cat.threshold((.2, .12, 40), prior)
        self.assertGreater(tight, loose)
        self.assertAlmostEqual(cat.threshold(None, prior), .3 - cat.K_SIGMA * .1)
        self.assertLess(cat.threshold((.9, 0, 2), prior), .9)

    def test_discovery_requires_two_views_instead_of_shared_body_style(self):
        np = self.np
        body = np.array([[1., 0], [1., 0], [-1., 0], [-1., 0]])
        distinct = np.array([[1., 0], [0., 1], [-1., 0], [0., -1]])
        components = {"body": body, "structure": distinct, "keyphrases": distinct}
        combined = cat.combine(np, components)
        self.assertGreater(float(combined[0] @ combined[1]), cat.LINKAGE_CUT)
        affinity = cat.discovery_similarity(np, components, combined)
        self.assertAlmostEqual(float(affinity[0, 1]), 0.)
        components["structure"] = body
        affinity = cat.discovery_similarity(np, components, cat.combine(np, components))
        self.assertGreater(float(affinity[0, 1]), cat.LINKAGE_CUT)

    def test_discovery_missing_or_constant_views_do_not_reject_body_only_matches(self):
        np = self.np
        body = np.array([[1., 0], [1., .1], [-1., 0], [-1., -.1]])
        for structure in ([None] * 4, [np.ones(2)] * 4):
            components = {"body": body, "structure": structure}
            combined = cat.combine(np, components)
            np.testing.assert_allclose(cat.discovery_similarity(np, components, combined),
                                       combined @ combined.T, atol=1e-9)


class NamingTests(unittest.TestCase):
    def profile(self, title, *headings, text="", keyphrases=()):
        topic = {"keyphrases": [{"phrase": phrase} for phrase in keyphrases]}
        return cat.profile_terms([title, *headings], topic, text or title)

    def test_group_name_is_shared_by_members_not_first_book(self):
        profiles = {
            "a": self.profile("Ancient Rome: daily life", "Roman empire"),
            "b": self.profile("The Roman empire and its provinces", "Roman empire"),
            "c": self.profile("Emperors of the Roman empire", "Military campaigns"),
            "x": self.profile("Cooking recipes", "Baking bread"),
        }
        named = cat.name_groups({"g": ["a", "b", "c"]}, profiles, {"other": ["x"]})
        self.assertEqual(cat.fold(named["g"]["label"]), "roman empire")
        self.assertEqual(named["g"]["support"], 3)

    def test_noise_is_not_a_name(self):
        for phrase in ("Day free trial form", "Qkd", "Raiz estava", "Lecture notes", "Cambridge", "Completas contendo"):
            self.assertTrue(cat.is_noise(cat.fold(phrase), phrase), phrase)
        self.assertTrue(cat.is_noise("acm icpc", "ACM ICPC"))
        self.assertTrue(cat.is_noise("luiz felipe", "Luiz Felipe", proper={"luiz", "felipe"}))
        for phrase in ("Elliptic curves", "Numerical semigroups", "Teoria dos números", "Heston model"):
            self.assertFalse(cat.is_noise(cat.fold(phrase), phrase, proper={"heston"}), phrase)

    def test_people_names_written_only_capitalized_are_proper(self):
        profile = self.profile("Relatório", text="Luiz Felipe apresentou o relatório. Luiz Felipe discute regressão linear.")
        self.assertIn("felipe", profile["proper"])
        self.assertNotIn("regressao", profile["proper"])

    def test_singleton_name_comes_from_structure(self):
        profiles = {"a": self.profile("Data Encryption at the Intersection of Mathematics and Physics",
                                      "Quantum key distribution", keyphrases=["QKD"])}
        named = cat.name_groups({"t": ["a"]}, profiles)
        self.assertTrue(named["t"]["label"])
        self.assertNotEqual(cat.fold(named["t"]["label"]), "qkd")

    def test_generic_words_do_not_name_a_category_on_their_own(self):
        for phrase in ("Função", "Entrada", "Resultado", "Properties", "Metodologia"):
            self.assertTrue(cat.is_noise(cat.fold(phrase), phrase))
        self.assertFalse(cat.is_noise("funcao de custo", "Função de custo"))

    def test_complete_phrase_is_preferred_to_adjective_fragment(self):
        profiles = {name: self.profile("Fully homomorphic encryption", text="fully homomorphic encryption " * 8)
                    for name in ("a", "b", "c")}
        named = cat.name_groups({"g": list(profiles)}, profiles)
        self.assertEqual(cat.fold(named["g"]["label"]), "fully homomorphic encryption")

    def test_one_long_document_does_not_dominate_the_group_label(self):
        profiles = {"a": self.profile("Roman empire", "Military campaigns"),
                    "b": self.profile("Roman empire", "Daily life"),
                    "c": self.profile("Roman empire", "Ancient cities")}
        before = cat.name_groups({"g": list(profiles)}, profiles)["g"]["label"]
        for field in ("body", "structure"):
            profiles["a"][field] = {key: value * 1000 for key, value in profiles["a"][field].items()}
        self.assertEqual(cat.name_groups({"g": list(profiles)}, profiles)["g"]["label"], before)

    def test_families_follow_subject_then_kind_then_shelves(self):
        self.assertEqual(cat.family_for("Quantum measure", ["interference", "slit"]), "Física")
        self.assertEqual(cat.family_for("Volatility", ["option pricing", "heston"]), "Finanças e economia")
        self.assertEqual(cat.family_for("Numerical semigroups", [], cat.Counter({"Álgebra abstrata": 3}),
                                        shelf_families={"Álgebra abstrata": "Álgebra"}), "Álgebra")
        self.assertEqual(cat.family_for("Cadastro geral", [], kinds=cat.Counter({"formulario": 3})),
                         "Documentos e formulários")
        self.assertEqual(cat.family_for("Xyz abc", []), "Temas descobertos")

    def test_structure_proxy_reads_toc_and_numbered_headings(self):
        text = "Notas de Aula\nAnálise real\n\nSumário\n1 Derivada . . . . . . . 2\n1.1 Noções Iniciais . . . . 2\n\f1 Derivada\n1.2 Derivadas Laterais\n"
        structure = cat.structure_proxy(text, "")
        self.assertIn("Derivada", structure["toc"])
        self.assertIn("Derivadas Laterais", [item["text"] for item in structure["headings"]])
        self.assertIn("Notas de Aula", structure["front"])


class MigrationTests(unittest.TestCase):
    def test_old_catalog_gains_columns_and_profiles_table(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("CREATE TABLE semantic_themes(number INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL DEFAULT '', "
                         "label_version TEXT NOT NULL DEFAULT ''); INSERT INTO semantic_themes(label) VALUES ('Ecologia');")
        migrate(db)
        migrate(db)
        self.assertEqual(theme_subjects(db), [{"name": "Tema 1", "label": "Ecologia", "family": "Temas descobertos", "msc": ""}])
        self.assertEqual(db.execute("SELECT count(*) FROM semantic_profiles").fetchone()[0], 0)

    def test_small_or_unavailable_collection_does_not_consolidate(self):
        temporary = tempfile.TemporaryDirectory(prefix="atlas-categories-test-")
        self.addCleanup(temporary.cleanup)
        db = connect(Path(temporary.name))
        self.addCleanup(db.close)
        catalog = SemanticCatalog(db, FakeEncoder())
        self.assertEqual(catalog.consolidate(), [])
        catalog.unavailable = "sem modelo"
        self.assertEqual(catalog.consolidate(), [])


if __name__ == "__main__":
    unittest.main()
