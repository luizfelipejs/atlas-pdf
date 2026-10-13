"""Representative behavioral checks for the offline classifier."""

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from classifier import CATEGORIES, classify


_CONTEXT = (
    "Este material apresenta definições, demonstrações e exercícios resolvidos "
    "para compreender os conceitos estudados. Cada resultado é explicado de "
    "forma detalhada e acompanhado de aplicações e problemas para estudo. "
)


class ClassifierTests(unittest.TestCase):
    def test_portuguese_linear_algebra(self):
        text = _CONTEXT + "Matrizes autovalores autovetores. Espaços vetoriais, determinantes e transformação linear."
        result = classify(text, "Álgebra Linear")
        self.assertEqual(result["category"], "Álgebra linear")
        self.assertGreater(result["confidence"], .7)
        self.assertIn("autovalores", result["reason"])

    def test_english_complex_analysis(self):
        text = _CONTEXT + "Complex analysis studies holomorphic functions, contour integrals and the residue theorem. Analytic continuation gives the Laurent series."
        self.assertEqual(classify(text)["category"], "Análise complexa")

    def test_spanish_probability(self):
        text = _CONTEXT + "Probabilidad condicional y variables aleatorias. El espacio muestral y la esperanza matematica permiten estudiar la ley de los grandes numeros."
        self.assertEqual(classify(text)["category"], "Probabilidade")

    def test_all_mathematical_shelves(self):
        cases = {
            "Cálculo": "Cálculo diferencial, derivadas, integrais e regra da cadeia.",
            "Álgebra abstrata": "Teoria de grupos, homomorfismos e subgrupos. Estudamos um anel comutativo.",
            "Geometria": "Geometria euclidiana, triângulos, polígonos, circunferência e teorema de Pitágoras.",
            "Topologia": "Topologia, homeomorfismos, homotopia, compacidade e grupo fundamental.",
            "Análise real": "Análise real, sequências de Cauchy, convergência uniforme, supremo e ínfimo em espaços métricos.",
            "Equações diferenciais": "Equações diferenciais e problema de valor inicial com condições de contorno e separação de variáveis.",
            "Estatística": "Estatística, teste de hipótese, intervalo de confiança, amostragem e regressão.",
            "Matemática discreta": "Matemática discreta, relações de equivalência, indução matemática e relações de recorrência em conjuntos finitos.",
            "Teoria dos números": "Teoria dos números, números primos, congruências, divisibilidade e aritmética modular.",
            "Otimização": "Otimização convexa, programação linear, região viável, função objetivo e simplex.",
            "Métodos numéricos": "Métodos numéricos, interpolação, quadratura, erro de truncamento e método de Newton.",
            "Matemática básica": "Matemática básica, frações, adição, subtração, multiplicação e porcentagem.",
            "Teoria dos grafos": "Graph theory: vertices, edges, adjacency matrix, spanning tree, graph coloring and Hamiltonian cycle.",
            "Combinatória": "Combinatorics studies permutations, generating functions, inclusion exclusion and Catalan numbers.",
            "Lógica matemática": "Mathematical logic: first order logic, quantifiers, Godel incompleteness theorem and proof theory.",
            "Teoria da medida": "Measure theory develops sigma algebras, measurable functions, Lebesgue integration and the Radon Nikodym theorem.",
            "Análise funcional": "Functional analysis studies Banach spaces, Hilbert spaces, bounded operators and the Hahn Banach theorem.",
            "Geometria diferencial": "Differential geometry studies smooth manifolds, Riemannian metrics, tangent bundles and covariant derivatives.",
            "Sistemas dinâmicos": "Dynamical systems: bifurcations, Lyapunov exponents, strange attractors and ergodic theory.",
            "Algoritmos e computação": "Algorithm design: data structures, time complexity, divide and conquer, binary search and sorting algorithms.",
            "Aprendizado de máquina": "Machine learning: supervised learning, neural networks, backpropagation, training data, overfitting and cross validation.",
            "Redes neurais em grafos": "Graph neural networks: graph convolution, message passing, node classification, graph embeddings and link prediction.",
        }
        for category, text in cases.items():
            with self.subTest(category=category):
                self.assertEqual(classify(_CONTEXT + text)["category"], category)

    def test_frequencies_use_body_only_and_normalize_accents(self):
        result = classify(_CONTEXT + "ÁRVORE árvore Arvore matriz matriz e de and the y la el.", "Autovalores")
        counts = {entry["word"]: entry["count"] for entry in result["keywords"]}
        self.assertEqual(counts["arvore"], 3)
        self.assertEqual(counts["matriz"], 2)
        for excluded in ("autovalores", "and", "the", "de", "la"):
            self.assertNotIn(excluded, counts)

    def test_no_text_ignores_title(self):
        result = classify("", "Álgebra linear autovalores matrizes")
        self.assertEqual(result["category"], "Revisar")
        self.assertEqual(result["keywords"], [])
        self.assertIn("OCR", result["reason"])

    def test_too_short_needs_review(self):
        self.assertEqual(classify("Teoria de grupos e homomorfismos")["category"], "Revisar")

    def test_repetition_does_not_create_independent_evidence(self):
        self.assertEqual(classify("matriz " * 500)["category"], "Revisar")

    def test_nonmath_despite_misleading_filename(self):
        text = _CONTEXT + "Medicina: paciente, tratamento, diagnóstico e hospital. O paciente apresenta sintomas e precisa de cuidados para a doença."
        result = classify(text, "Cálculo diferencial, álgebra linear e matrizes.pdf")
        self.assertEqual(result["category"], "Outros assuntos")
        self.assertNotIn("Cálculo", result["scores"])

    def test_generic_long_nonmath_prose(self):
        text = "O viajante caminhava lentamente pela estrada observando montanhas distantes enquanto lembrava acontecimentos antigos. " * 10
        self.assertEqual(classify(text)["category"], "Outros assuntos")

    def test_mixed_subjects_need_review(self):
        text = _CONTEXT + "Autovalores, autovetores e espaço vetorial. Homomorfismos, subgrupos e teoria de grupos."
        result = classify(text)
        self.assertEqual(result["category"], "Revisar")
        self.assertIn("ambíguo", result["reason"])

    def test_title_cannot_override_content(self):
        text = _CONTEXT + "Estatística com amostragem, regressão e intervalo de confiança. Teste de hipótese e máxima verossimilhança."
        self.assertEqual(classify(text, "Geometria euclidiana e triângulos")["category"], "Estatística")

    def test_hyphenation_is_repaired(self):
        text = _CONTEXT + "Autova-\nlores e autovetores no espaço vetorial. Matrizes e transformação linear."
        result = classify(text)
        self.assertEqual(result["category"], "Álgebra linear")
        self.assertIn("autovalores", [entry["word"] for entry in result["keywords"]])

    def test_contract(self):
        result = classify(_CONTEXT + "Probabilidade e variáveis aleatórias no espaço amostral.")
        self.assertIn(result["category"], CATEGORIES)
        self.assertLessEqual(len(result["keywords"]), 20)
        self.assertGreaterEqual(result["confidence"], 0)
        self.assertLessEqual(result["confidence"], 1)
        self.assertIsInstance(result["reason"], str)
        self.assertTrue(all(isinstance(item["count"], int) for item in result["keywords"]))

    def test_multi_megabyte_extracted_text(self):
        passage = _CONTEXT + "Matrizes, autovalores e autovetores no espaço vetorial. "
        text = passage * (4_000_000 // len(passage) + 1)
        result = classify(text)
        self.assertEqual(result["category"], "Álgebra linear")
        counts = {item["word"]: item["count"] for item in result["keywords"]}
        self.assertEqual(counts["autovalores"], text.count("autovalores"))


if __name__ == "__main__":
    unittest.main()
