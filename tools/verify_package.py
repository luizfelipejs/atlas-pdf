#!/usr/bin/env python3
"""Verify the packaged application with no system Python/Node/Poppler on PATH."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_indexer import write_pdf


def main() -> None:
    application = ROOT / "dist/AtlasPDF"
    version = json.loads((ROOT / "package.json").read_text())["version"]
    assert f"Atlas PDF {version} " in subprocess.check_output([str(application), "--version"], text=True)
    with tempfile.TemporaryDirectory(prefix="atlas-package-test-") as directory:
        temporary = Path(directory)
        tools = temporary / "system-tools"
        tools.mkdir()
        # These two programs are the documented host prerequisites. All other
        # command-line tools and interpreters must come from the application.
        for name in ("tar", "gzip"):
            location = shutil.which(name)
            if not location:
                raise RuntimeError(f"Pré-requisito ausente: {name}")
            (tools / name).symlink_to(location)
        environment = os.environ.copy()
        environment.update(PATH=str(tools), PYTHONHOME="/nonexistent-atlas-python")
        library, data = temporary / "livros com acentos", temporary / "atlas-pdf/data"
        library.mkdir()
        write_pdf(library / "Cálculo — teste.PDF",
                  "Calculus derivatives integrals limits functions continuous convergence series "
                  "theorems proofs real analysis mathematics " * 4)
        (library / "PDF ilegível.pdf").write_bytes(b"not a PDF")

        def cli(*arguments: str) -> list[dict]:
            result = subprocess.run([str(application), "--backend", "--data-dir", str(data), *arguments],
                                    env=environment, capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(f"Pacote falhou ({result.returncode}): {result.stderr[-2500:]} {result.stdout[-1500:]}")
            return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

        first = cli("scan", "--root", str(library))[-1]
        assert first["total"] == 2 and first["indexed"] == 2, first
        books = cli("list")[0]["books"]
        valid = next(book for book in books if book["pages"] == 1)
        assert valid["keywords"] and Path(valid["thumbnail"]).is_file(), valid
        assert any(book["status"] == "error" for book in books), books
        analysis = valid.get("analysis") or {}
        semantic = analysis.get("semantic", {}) if isinstance(analysis, dict) else {}
        assert semantic.get("status") == "ready" and semantic.get("vector_key"), semantic
        topic = analysis.get("topic", {})
        assert topic.get("status") == "ready" and topic.get("label") and topic.get("keyphrases"), topic
        structure = analysis.get("structure", {})
        assert structure.get("status") == "ready" and structure.get("kind_label"), structure
        assert structure.get("stats", {}).get("pages_read") == 1, structure
        warm = cli("scan", "--root", str(library))[-1]
        assert warm["unchanged"] == 2 and warm["indexed"] == 0, warm
        cli("set-category", "--id", valid["id"], "--category", "Topologia")
        installed = temporary / "AtlasPDF"
        try:
            os.link(application, installed)
        except OSError:
            shutil.copy2(application, installed)
        direct = subprocess.check_output([str(installed), "--backend", "list"],
                                         env=environment, text=True, timeout=90)
        assert any(book["category"] == "Topologia" for book in json.loads(direct)["books"]), direct[:500]
        export = temporary / "catálogo.txt"
        cli("export", "--output", str(export))
        assert "Topologia" in export.read_text() and str(library) in export.read_text()
        assert (data / "paths.txt").is_file() and list((data / "logs").glob("*.txt"))
        screenshot = ROOT / "build/package-preview.png"
        result = subprocess.run([str(application), "--data-dir", str(data), "--smoke", "--screenshot", str(screenshot)],
                                env=environment, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise RuntimeError(f"Interface empacotada falhou ({result.returncode}): {result.stderr[-3000:]}")
        assert screenshot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and screenshot.stat().st_size > 20000
        print("Pacote validado: Electron/Python/Bash/Poppler incluídos; indexação, leitura, miniatura, incremental, ajuste manual, TXT e interface funcionando.")
        print(f"Captura da verificação: {screenshot}")


if __name__ == "__main__":
    main()
