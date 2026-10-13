"""Structure contracts: real Poppler, typography, outlines, failure and caching."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import structure as st
from indexer import Cancelled, cached_structure
from test_indexer import write_pdf


XML = '''<pdf2xml>
<page number="1" width="918" height="1188">
<fontspec id="0" size="36" family="Helvetica-Bold"/>
<fontspec id="1" size="18" family="Helvetica"/>
<text top="100" left="80" width="550" height="40" font="0">Quantum Optics</text>
<text top="200" left="80" width="700" height="22" font="1">Quantum optics studies photons and interference in optical experiments.</text>
<text top="225" left="80" width="700" height="22" font="1">The quantum state describes measurements of light and its properties.</text>
<text top="250" left="80" width="700" height="22" font="1">Photons interact with matter and produce observable interference patterns.</text>
</page>
<outline>
<item page="2">1 Photons &amp; matter</item>
<outline><item page="3">1.1 Interference</item></outline>
<item page="4">2 Optical experiments</item>
</outline>
</pdf2xml>'''
TEXT = ("Quantum optics studies photons and interference. The quantum state describes "
        "measurements of light and the properties of optical experiments. ") * 10


def xml_runner(command, *, timeout, output, limited):
    output.write(XML.encode())
    return 0, ""


class StructureTests(unittest.TestCase):
    def test_nested_outline_decodes_entities_and_preserves_pages(self):
        result = st.parse_xml(XML)
        self.assertEqual([(h["level"], h["page"]) for h in result["outline"]], [(1, 2), (2, 3), (1, 4)])
        self.assertEqual(result["outline"][0]["text"], "1 Photons & matter")
        self.assertTrue(result["pages"][1]["texts"][0]["bold"])

    def test_font_title_and_outline_survive_unusable_metadata(self):
        result = st.read_structure("book.pdf", 4, TEXT, {"Title": "Microsoft Word - Document1.docx"}, runner=xml_runner)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["title"], "Quantum Optics")
        self.assertEqual(result["title_source"], "font")
        self.assertEqual(result["stats"]["pages_read"], 1)
        self.assertTrue(any(h["text"] == "Interference" and h["page"] == 3 for h in result["headings"]))

    def test_source_file_path_is_not_a_book_title(self):
        for title in ("C:/Rogerio/PEOPLE NEW/criptografia", r"C:\Books\draft.tex", "/tmp/book.pdf", "file:///tmp/book.pdf"):
            self.assertEqual(st.metadata_title({"Title": title}), "", title)
        self.assertEqual(st.metadata_title({"Title": "Microsoft Word - Quantum Optics.docx"}), "Quantum Optics")

    def test_timeout_preserves_text_toc_and_is_retryable(self):
        def timeout(*args, **kwargs):
            raise TimeoutError("tempo esgotado")
        text = "Contents\n" + "\n".join(f"{i} {name} . . . . . . . {i * 2}" for i, name in enumerate(
            ("Photons", "Interference", "Optical experiments", "Quantum states", "Measurements"), 1)) + "\n" + TEXT
        result = st.read_structure("book.pdf", 100, text, {}, runner=timeout)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["transient"])
        self.assertTrue(any(entry.endswith("Photons") for entry in result["toc"]))

    def test_empty_or_broken_pdf_is_unavailable(self):
        def broken(*args, **kwargs):
            return 1, "PDF inválido"
        result = st.read_structure("broken.pdf", 0, "", {}, runner=broken)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["headings"], [])

    def test_scan_cancellation_is_not_swallowed(self):
        def cancel(*args, **kwargs):
            raise Cancelled()
        with self.assertRaises(Cancelled):
            st.read_structure("book.pdf", 1, TEXT, {}, runner=cancel, passthrough=(Cancelled,))

    def test_long_book_sampling_is_bounded_and_distributed(self):
        windows = st.page_windows(1000)
        self.assertEqual(windows[0], (1, st.FRONT_PAGES))
        self.assertLessEqual(sum(b - a + 1 for a, b in windows), 36)
        self.assertGreater(windows[-1][0], 750)
        self.assertTrue(all(a <= b <= 1000 for a, b in windows))

    def test_exam_type_uses_opening_evidence(self):
        text = "Prova de Física\nNome: __________\nQuestão 1 (2 pontos)\nQuestão 2 (3 pontos)\n" + TEXT
        def no_xml(*args, **kwargs):
            return 1, "sem estrutura"
        result = st.read_structure("exam.pdf", 2, text, {"Title": "Prova de Física"}, runner=no_xml)
        self.assertEqual(result["kind"], "prova")
        self.assertTrue(result["kind_evidence"])

    def test_structure_cache_reuses_content_and_retries_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            transient = dict(st.empty_result("partial"), transient=True)
            ready = dict(st.empty_result("ready"), title="Quantum Optics", transient=False)
            with patch("indexer.read_structure", side_effect=[transient, ready]) as reader:
                cached_structure("a.pdf", 1, TEXT, {}, "same-content", folder)
                cached_structure("a.pdf", 1, TEXT, {}, "same-content", folder)
                other = cached_structure("copy.pdf", 1, TEXT, {}, "same-content", folder)
            self.assertEqual(reader.call_count, 2)
            self.assertEqual(other["title"], "Quantum Optics")

    @unittest.skipUnless(shutil.which("pdftohtml"), "Poppler required")
    def test_real_pdf_is_read_by_packaged_structure_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "physics.pdf"
            write_pdf(pdf, TEXT)
            result = st.read_structure(str(pdf), 1, TEXT, {})
            self.assertEqual(result["status"], "ready", result)
            self.assertEqual(result["stats"]["pages_read"], 1)


if __name__ == "__main__":
    unittest.main()
