"""Integration tests exercise actual Bash, Poppler and the JSON-lines CLI."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

PROJECT = Path(__file__).resolve().parents[1]
INDEXER = PROJECT / "backend" / "indexer.py"


def write_pdf(path: Path, text: str = "Calculus derivatives integrals limits functions " * 12) -> None:
    """Build a valid, tiny PDF using the documented xref format, without extra libs."""
    lines = textwrap.wrap(text, width=78)[:48] or [""]
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = ("BT /F1 12 Tf 14 TL 40 750 Td " + " T* ".join(f"({line}) Tj" for line in escaped) + " ET").encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + item + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@unittest.skipUnless(all(shutil.which(name) for name in ("bash", "pdfinfo", "pdftotext")), "Poppler and Bash are required")
class IndexerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="atlas-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "biblioteca"
        self.root.mkdir()
        self.data = self.base / "dados"

    def cli(self, *arguments: str, expected: int = 0) -> list[dict]:
        process = subprocess.run([sys.executable, str(INDEXER), "--data-dir", str(self.data), *arguments],
                                 cwd=self.base, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=35)
        self.assertEqual(process.returncode, expected, process.stderr + process.stdout)
        return [json.loads(line) for line in process.stdout.splitlines()]

    def scan(self, *extra: str) -> list[dict]:
        return self.cli("scan", "--root", str(self.root), *extra)

    def books(self) -> list[dict]:
        return self.cli("list")[0]["books"]

    def test_reclassification_preserves_manual_categories_and_originals(self) -> None:
        source = self.root / "Graph Theory.pdf"
        write_pdf(source, "Graph theory vertices edges adjacency matrix spanning tree graph coloring. " * 4)
        original = source.read_bytes()
        self.scan()
        book = self.books()[0]
        self.cli("set-category", "--id", book["id"], "--category", "Topologia")
        final = self.cli("reclassify", "--force")[-1]
        updated = self.books()[0]
        self.assertEqual(updated["category"], "Topologia")
        self.assertTrue(updated["manual"])
        self.assertEqual(final["manual_preserved"], 1)
        self.assertEqual(final["indexed"], 1)
        self.assertTrue(Path(final["backup"]).is_file())
        self.assertTrue(Path(final["report"]).is_file())
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(updated["analysis"]["candidates"][0]["category"], "Teoria dos grafos")
        self.assertTrue(list((self.data / "text-cache").glob("*.gz")))

    @unittest.skipUnless((PROJECT / "assets/models/minilm/model.onnx").is_file(), "Local embedding model required")
    def test_discovered_themes_are_persistent_and_available_in_category_picker(self) -> None:
        history = "The history of ancient Rome explores the Roman empire, emperors and political institutions. It studies military campaigns, everyday life, ancient culture, archaeology, historic documents, cities and society in classical antiquity. This textbook describes historical events and explains how civilizations evolved over centuries."
        similar = "This book studies ancient Roman history and the society of the Roman empire. It explains the lives of emperors, military campaigns, political institutions and everyday customs. Historical documents and archaeological evidence reveal how cities, classical civilization and culture changed over centuries."
        cooking = "This cooking book teaches delicious recipes for everyday meals. Fresh vegetables are chopped, seasoned and cooked in a kitchen. The baker mixes flour, yeast and water to prepare bread dough. Detailed instructions explain baking temperatures, ingredient measurements and techniques for preparing tasty food."
        write_pdf(self.root / "a-history.pdf", history)
        self.scan()
        original = self.books()[0]
        self.assertEqual(original["category"], "Tema 1")
        write_pdf(self.root / "b-roman-history.pdf", similar)
        write_pdf(self.root / "c-cooking.pdf", cooking)
        self.scan()
        books = {Path(book["path"]).stem: book for book in self.books()}
        self.assertEqual(books["b-roman-history"]["category"], "Tema 1")
        self.assertEqual(books["c-cooking"]["category"], "Tema 2")
        self.assertIn("Tema 2", self.cli("categories")[0]["categories"])
        self.assertEqual(self.cli("reclassify", "--force")[-1]["changed"], 0)
        self.cli("set-category", "--id", original["id"], "--category", "Tema 2")
        changed = next(book for book in self.books() if book["id"] == original["id"])
        self.assertTrue(changed["manual"])
        self.assertEqual(changed["category"], "Tema 2")

    def test_legacy_catalog_migrates_without_losing_books(self) -> None:
        write_pdf(self.root / "Livro.pdf")
        self.scan()
        before = self.books()[0]
        with sqlite3.connect(self.data / "catalog.sqlite3") as connection:
            connection.execute("ALTER TABLE books DROP COLUMN analysis")
        after = self.books()[0]
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["category"], after["category"])
        self.assertEqual(after["analysis"], {})

    def test_manual_choice_during_analysis_wins_over_stale_result(self) -> None:
        write_pdf(self.root / "Livro.pdf")
        self.scan()
        sys.path.insert(0, str(PROJECT / "backend"))
        from indexer import connect, save_book
        connection = connect(self.data)
        try:
            stale = dict(connection.execute("SELECT * FROM books").fetchone())
            self.cli("set-category", "--id", stale["id"], "--category", "Topologia")
            stale["category"] = "Álgebra linear"
            save_book(connection, stale)
        finally:
            connection.close()
        self.assertEqual(self.books()[0]["category"], "Topologia")

    def test_reclassification_obeys_scan_lock(self) -> None:
        self.data.mkdir()
        with open(self.data / "scan.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events = self.cli("reclassify", expected=1)
        self.assertEqual(events[-1]["type"], "error")

    def test_hostile_utf8_paths_duplicates_overlap_and_exclusions(self) -> None:
        hostile = self.root / '-$(touch OWNED); " cálculo\n📘.PDF'
        write_pdf(hostile)
        copy = self.root / "subpasta" / "copia.pdf"
        copy.parent.mkdir()
        shutil.copyfile(hostile, copy)
        write_pdf(self.root / ".cache" / "ignorar.pdf")
        write_pdf(self.root / ".local" / "share" / "Trash" / "files" / "ignorar.pdf")
        (self.root / "link.pdf").symlink_to(hostile)
        events = self.scan("--root", str(copy.parent))
        books = self.books()
        self.assertEqual(len(books), 2)
        self.assertEqual({book["path"] for book in books}, {str(hostile), str(copy)})
        self.assertNotEqual(books[0]["id"], books[1]["id"])
        self.assertEqual(books[0]["sha256"], books[1]["sha256"])
        self.assertFalse((self.base / "OWNED").exists())
        self.assertEqual(events[-1]["total"], 2)
        self.assertTrue(all(book["pages"] == 1 for book in books))
        paths = [json.loads(line) for line in (self.data / "paths.txt").read_text().splitlines()]
        self.assertEqual(set(paths), {str(hostile), str(copy)})
        self.assertTrue(Path(events[-1]["log"]).is_file())
        self.assertIn('"type":"complete"', Path(events[-1]["log"]).read_text())

    def test_incremental_manual_force_and_export(self) -> None:
        write_pdf(self.root / "calculus.pdf")
        self.assertEqual(self.scan()[-1]["indexed"], 1)
        original = self.books()[0]
        second = self.scan()[-1]
        self.assertEqual((second["indexed"], second["unchanged"]), (0, 1))
        self.assertEqual(self.books()[0]["scanned_at"], original["scanned_at"])
        categories = self.cli("categories")[0]["categories"]
        manual = next(category for category in categories if category not in ("Revisar", "Outros assuntos", original["category"]))
        self.cli("set-category", "--id", original["id"], "--category", manual)
        self.assertEqual(self.scan("--force")[-1]["indexed"], 1)
        updated = self.books()[0]
        self.assertEqual(updated["category"], manual)
        self.assertTrue(updated["manual"])
        output = self.base / "exportação.txt"
        self.cli("export", "--output", str(output))
        self.assertIn(str(self.root / "calculus.pdf"), output.read_text())
        self.assertIn(manual, output.read_text())

    def test_corrupt_and_image_only_pdfs_remain_visible_for_review(self) -> None:
        (self.root / "corrompido.pdf").write_bytes(b"%PDF-invalid broken file")
        write_pdf(self.root / "digitalizado.pdf", "")
        self.scan()
        books = {Path(book["path"]).name: book for book in self.books()}
        self.assertEqual(len(books), 2)
        self.assertEqual(books["corrompido.pdf"]["category"], "Revisar")
        self.assertEqual(books["corrompido.pdf"]["status"], "error")
        self.assertIn("ilegível", books["corrompido.pdf"]["reason"])
        self.assertEqual(books["digitalizado.pdf"]["category"], "Revisar")
        self.assertIn("OCR", books["digitalizado.pdf"]["reason"])
        manual = self.cli("categories")[0]["categories"][0]
        self.cli("set-category", "--id", books["corrompido.pdf"]["id"], "--category", manual)
        corrupted = next(book for book in self.books() if book["path"].endswith("corrompido.pdf"))
        self.assertIn("ilegível", corrupted["reason"])
        self.assertEqual(corrupted["status"], "error")

    def test_missing_only_in_successful_roots_and_reappearance(self) -> None:
        root_b = self.base / "outra-biblioteca"
        pdf_a, pdf_b = self.root / "a.pdf", root_b / "b.pdf"
        write_pdf(pdf_a)
        write_pdf(pdf_b)
        self.scan("--root", str(root_b))
        pdf_b.unlink()
        self.scan()
        self.assertFalse(any(book["missing"] for book in self.books()))
        root_b.rename(self.base / "indisponivel")
        self.cli("scan", "--root", str(root_b))
        self.assertFalse(any(book["missing"] for book in self.books()))
        root_b.mkdir()
        self.cli("scan", "--root", str(root_b))
        self.assertTrue(next(book for book in self.books() if book["path"] == str(pdf_b))["missing"])
        write_pdf(pdf_b)
        self.cli("scan", "--root", str(root_b))
        self.assertFalse(any(book["missing"] for book in self.books()))

    def test_scan_lock_rejects_a_second_writer(self) -> None:
        self.data.mkdir()
        with open(self.data / "scan.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events = self.cli("scan", "--root", str(self.root), expected=1)
            self.assertEqual(events[-1]["type"], "error")
            self.assertIn("andamento", events[-1]["message"])

    def test_sigterm_preserves_commits_and_kills_poppler_process_group(self) -> None:
        write_pdf(self.root / "a-first.pdf")
        self.scan()
        write_pdf(self.root / "z-slow.pdf")
        binaries = self.base / "bin"
        binaries.mkdir()
        marker = self.base / "child-entered"
        script = binaries / "pdfinfo"
        script.write_text(f"#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\nprint('Pages: 1')\n")
        script.chmod(0o755)
        environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ.get("PATH", ""))
        process = subprocess.Popen([sys.executable, str(INDEXER), "--data-dir", str(self.data),
                                    "scan", "--root", str(self.root)], cwd=self.base, env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("Scan stopped before entering the Poppler fixture: " + process.communicate()[1])
            time.sleep(0.03)
        self.assertTrue(marker.exists(), "Poppler subprocess was not started")
        child_pid = int(marker.read_text())
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130, error)
        self.assertTrue(json.loads(output.splitlines()[-1])["cancelled"])
        self.assertEqual(len(self.books()), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)


if __name__ == "__main__":
    unittest.main()
