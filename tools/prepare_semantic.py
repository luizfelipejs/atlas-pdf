#!/usr/bin/env python3
"""Prepare pinned local inference assets; never called by the running app."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from semantic import MODEL_NAME, MODEL_REVISION, MODEL_VERSION

FILES = {
    "model.onnx": ("onnx/model_quint8_avx2.onnx", "98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f"),
    "tokenizer.json": ("tokenizer.json", "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"),
}


def checksum(path):
    if not path.is_file():
        return ""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    folder = ROOT / "assets/models/minilm"
    folder.mkdir(parents=True, exist_ok=True)
    for name, (remote, digest) in FILES.items():
        target = folder / name
        if checksum(target) == digest:
            continue
        if "--check" in sys.argv:
            raise RuntimeError(f"Modelo ausente ou inválido: {name}")
        url = f"https://huggingface.co/{MODEL_NAME}/resolve/{MODEL_REVISION}/{remote}"
        print(f"Baixando {name} de {MODEL_NAME}…", flush=True)
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
            pending = Path(output.name)
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    shutil.copyfileobj(response, output)
                output.close()
                if checksum(pending) != digest:
                    raise RuntimeError(f"SHA-256 inesperado: {name}")
                os.replace(pending, target)
            finally:
                pending.unlink(missing_ok=True)
    if "--check" not in sys.argv:
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("Instale uv para preparar as dependências Python isoladas.")
        subprocess.run([uv, "pip", "install", "--python", "/usr/bin/python3", "--target", str(ROOT / "build/semantic-python"),
                        "--no-deps", "--quiet", "-r", str(ROOT / "backend/requirements-semantic.txt")], check=True)
        license_file = Path("/usr/share/common-licenses/Apache-2.0")
        if license_file.is_file():
            shutil.copyfile(license_file, folder / "LICENSE")
        (folder / "manifest.json").write_text(json.dumps({
            "model": MODEL_NAME, "revision": MODEL_REVISION, "version": MODEL_VERSION,
            "license": "Apache-2.0", "files": {name: digest for name, (_remote, digest) in FILES.items()},
        }, indent=2) + "\n", encoding="utf-8")
    print("Modelo e tokenizer locais validados (SHA-256).")


if __name__ == "__main__":
    main()
