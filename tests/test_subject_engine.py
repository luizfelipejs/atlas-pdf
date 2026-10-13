"""Regression cases for subject/tool confusion and hierarchical classification."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from classifier import classify
from indexer import sampling_ranges

CONTEXT = "This textbook develops definitions, worked examples, exercises and proofs of the main results for students. "
LINEAR = "Matrices, eigenvalues, eigenvectors, vector spaces, determinants, inner products and diagonalization. "
GRAPH = "Graph theory, vertices, edges, adjacency matrices, spanning trees, graph coloring and Hamiltonian cycles. "
GNN = "Graph neural networks, graph convolution, message passing, node classification, graph embeddings and link prediction. "


class SubjectEngineTests(unittest.TestCase):
    def test_graph_textbook_uses_linear_algebra(self):
        decision = classify(CONTEXT + GRAPH * 3 + LINEAR * 60, "Spectral Graph Theory")
        self.assertEqual(decision["category"], "Teoria dos grafos")
        algebra = next(item for item in decision["analysis"]["candidates"] if item["category"] == "Álgebra linear")
        self.assertEqual(algebra["role"], "ferramenta de apoio")

    def test_gnn_textbook_has_large_prerequisite_chapter(self):
        text = CONTEXT + LINEAR * 100 + "\f" + GNN * 5 + "\f" + GNN * 5
        self.assertEqual(classify(text, "Introduction to Graph Neural Networks")["category"], "Redes neurais em grafos")

    def test_unknown_pdf_metadata_uses_filename_not_parent_folder(self):
        self.assertEqual(classify(CONTEXT + GNN + LINEAR * 10, "(anonymous)", "Graph Neural Networks Essentials")["category"], "Redes neurais em grafos")

    def test_misleading_graph_title_cannot_override_linear_content(self):
        self.assertEqual(classify(CONTEXT + LINEAR * 6, "Graph Neural Networks")["category"], "Álgebra linear")

    def test_gnn_is_more_specific_than_general_neural_networks(self):
        decision = classify(CONTEXT + GNN * 4 + "Neural networks and backpropagation use training data. " * 20, "Graph Neural Networks")
        self.assertEqual(decision["category"], "Redes neurais em grafos")

    def test_ordinary_plot_is_not_graph_theory(self):
        text = CONTEXT + "Differential calculus, derivatives, integrals, chain rule and Taylor series. The graph of the function is drawn in a figure."
        self.assertEqual(classify(text, "Calculus")["category"], "Cálculo")

    def test_survey_retains_parent_with_supported_children(self):
        text = CONTEXT + GRAPH + "Combinatorics, permutations, generating functions, Catalan numbers and inclusion exclusion."
        decision = classify(text, "Discrete Mathematics: Elementary and Beyond")
        self.assertEqual(decision["category"], "Matemática discreta")
        self.assertIn("Teoria dos grafos", [item["category"] for item in decision["analysis"]["secondary"]])

    def test_functional_analysis_uses_vector_spaces(self):
        text = CONTEXT + "Functional analysis, Banach spaces, Hilbert spaces, bounded operators, Hahn Banach and weak convergence. " * 4 + LINEAR * 20
        self.assertEqual(classify(text, "Functional Analysis")["category"], "Análise funcional")

    def test_numerical_linear_algebra_is_numerical(self):
        text = CONTEXT + LINEAR * 20 + "Numerical linear algebra, iterative methods, floating point, condition number, numerical stability and truncation error. " * 4
        self.assertEqual(classify(text, "Numerical Linear Algebra")["category"], "Métodos numéricos")

    def test_isolated_bibliography_does_not_define_subject(self):
        text = CONTEXT + LINEAR * 12 + "\nReferences\n" + GNN * 2
        self.assertEqual(classify(text, "Linear Algebra")["category"], "Álgebra linear")

    def test_translations_do_not_become_independent_concepts(self):
        decision = classify(CONTEXT + "matrix matrices matriz matrizes " * 30)
        self.assertEqual(decision["category"], "Revisar")

    def test_scores_are_deterministic_and_explainable(self):
        first = classify(CONTEXT + GRAPH, "Graph Theory")
        self.assertEqual(first, classify(CONTEXT + GRAPH, "Graph Theory"))
        top = first["analysis"]["candidates"][0]
        self.assertEqual(top["msc"], "05C")
        self.assertTrue(top["terms"])
        self.assertGreaterEqual(top["signals"], 3)

    def test_numerical_calculus_is_not_ordinary_calculus(self):
        text = CONTEXT + "Calculo diferencial derivadas integrais regra da cadeia. " * 25 + "Calculo numerico interpolacao quadratura metodo de Newton ponto flutuante erro de truncamento. " * 4
        self.assertEqual(classify(text, "Cálculo Numérico Computacional")["category"], "Métodos numéricos")

    def test_real_analysis_does_not_become_calculus(self):
        text = CONTEXT + "Calculus derivatives integrals Taylor series partial derivatives. " * 30 + "Mathematical analysis: Cauchy sequences, uniform convergence, supremum, metric spaces, Bolzano Weierstrass and uniform continuity. " * 6
        self.assertEqual(classify(text, "Mathematical Analysis: A Special Course")["category"], "Análise real")

    def test_camelcase_filename_keeps_subject_boundaries(self):
        text = CONTEXT + "Sequencias de Cauchy, supremo, convergencia uniforme, espaco metrico, numeros reais e continuidade uniforme. " * 4
        self.assertEqual(classify(text, "IMPAIntroducaoAnalise")["category"], "Análise real")

    def test_sampling_covers_long_book_without_exceeding_budget(self):
        for pages in (1, 59, 60, 61, 70, 100, 400, 1500):
            with self.subTest(pages=pages):
                ranges = sampling_ranges(pages)
                chosen = [page for first, last in ranges for page in range(first, last + 1)]
                self.assertEqual(len(chosen), len(set(chosen)))
                self.assertLessEqual(len(chosen), 60)
                self.assertEqual(min(chosen), 1)
                self.assertLessEqual(max(chosen), pages)
                if pages > 100:
                    self.assertGreater(max(chosen), pages * .75)


if __name__ == "__main__":
    unittest.main()
