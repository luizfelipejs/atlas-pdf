#!/usr/bin/env python3
"""Build a single ELF containing the GUI, interpreters, scripts and libraries."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sysconfig
import tarfile

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build" / "payload"
DIST = ROOT / "dist"
VERSION = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
SYSTEM_LIBS = re.compile(r"^(?:ld-linux|libc\.so|libm\.so|libpthread\.so|libdl\.so|librt\.so|libresolv\.so|libutil\.so|libanl\.so|libnss_)")


def copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source.resolve(), destination)


def main() -> None:
    # Only this generated staging directory is replaced; user data lives elsewhere.
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    DIST.mkdir(exist_ok=True)
    for folder in ("backend", "scripts", "assets", "desktop"):
        shutil.copytree(ROOT / folder, STAGE / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.cpp"))
    for filename in ("README.md", "THIRD_PARTY.md"):
        if (ROOT / filename).exists():
            copy(ROOT / filename, STAGE / filename)
    shutil.copytree(ROOT / "build/web", STAGE / "build/web")
    electron = ROOT / "node_modules/electron/dist"
    if not (electron / "electron").exists():
        raise RuntimeError("Execute npm ci antes de empacotar o Atlas.")
    shutil.copytree(electron, STAGE / "electron", ignore=shutil.ignore_patterns("chrome-sandbox"))
    (STAGE / "package.json").write_text(json.dumps({"name": "atlas-pdf", "version": VERSION, "main": "desktop/main.cjs"}), encoding="utf-8")
    native = [STAGE / "electron/electron"]
    native.extend((STAGE / "electron").glob("*.so*"))
    for executable in ("bash", "find", "pdfinfo", "pdftotext", "pdftoppm", "pdftohtml"):
        source = shutil.which(executable)
        if not source:
            raise RuntimeError(f"Dependência ausente: {executable}")
        target = STAGE / "bin" / executable
        copy(Path(source), target)
        native.append(target)
    python = Path("/usr/bin/python3")
    copy(python, STAGE / "python/bin/python3")
    native.append(STAGE / "python/bin/python3")
    version = subprocess.check_output([str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], text=True).strip()
    stdlib = Path("/usr/lib") / f"python{version}"
    if not stdlib.is_dir():
        stdlib = Path(sysconfig.get_path("stdlib"))
    shutil.copytree(stdlib, STAGE / "python/lib" / f"python{version}", ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "test", "tests", "idlelib", "tkinter", "turtledemo", "ensurepip", "dist-packages", "site-packages", "config-*", "*.a"))
    semantic_modules = ROOT / "build/semantic-python"
    if not all((semantic_modules / name).is_dir() for name in ("numpy", "onnxruntime", "tokenizers")):
        raise RuntimeError("Execute tools/prepare_semantic.py antes de empacotar.")
    shutil.copytree(semantic_modules, STAGE / "python/lib" / f"python{version}" / "dist-packages",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "test", "bin"))
    native.extend(path for path in (STAGE / "python").rglob("*.so*") if path.is_file())
    font_dir = Path("/usr/share/fonts/truetype/dejavu")
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"):
        if (font_dir / name).exists():
            copy(font_dir / name, STAGE / "assets/fonts" / name)
    # ldd resolves transitive DT_NEEDED dependencies. Keep glibc and the loader from
    # the operating system; bundling them with an unrelated loader is unsafe.
    dependencies: dict[str, str] = {}
    for executable in native:
        result = subprocess.run(["ldd", str(executable)], capture_output=True, text=True, check=False)
        for line in result.stdout.splitlines():
            if "not found" in line:
                raise RuntimeError(f"Biblioteca ausente em {executable.name}: {line.strip()}")
            match = re.match(r"\s*(\S+)\s+=>\s+(/.+?)\s+\(0x[0-9a-fA-F]+\)", line)
            if match and not SYSTEM_LIBS.match(match[1]):
                dependencies[match[1]] = match[2]
    for name, location in dependencies.items():
        copy(Path(location), STAGE / "lib" / name)
        # Electron's RPATH is $ORIGIN. Keep its libraries available there while
        # leaving the parent's environment clean for external PDF readers.
        runtime_library = STAGE / "electron" / name
        if not runtime_library.exists():
            runtime_library.symlink_to(Path("../lib") / name)
    notices = STAGE / "licenses"
    lockfile = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    for folder, package in lockfile["packages"].items():
        if not folder or package.get("dev"):
            continue
        source = ROOT / folder
        for pattern in ("LICENSE*", "NOTICE*", "COPYRIGHT*"):
            for notice in source.glob(pattern):
                if notice.is_file():
                    copy(notice, notices / "javascript" / folder.replace("node_modules/", "").replace("/", "__") / notice.name)
    for component in ("poppler-utils", "libpoppler134", "python3.12", "bash", "findutils", "fonts-dejavu-core"):
        notice = Path("/usr/share/doc") / component / "copyright"
        if notice.exists():
            copy(notice, notices / f"{component}.txt")
    for notice in semantic_modules.glob("*.dist-info/*LICENSE*"):
        copy(notice, notices / "python" / notice.parent.name / notice.name)
    for notice in semantic_modules.glob("*.dist-info/licenses/*"):
        if notice.is_file():
            copy(notice, notices / "python" / notice.parent.parent.name / notice.name)
    for license_name in ("GPL-2", "GPL-3", "LGPL-2.1", "LGPL-3"):
        license_file = Path("/usr/share/common-licenses") / license_name
        if license_file.exists():
            copy(license_file, notices / f"{license_name}.txt")
    (STAGE / "manifest.json").write_text(json.dumps({
        "application": "Atlas PDF", "version": VERSION, "platform": "Linux x86_64",
        "python": version, "electron": json.loads((ROOT / "node_modules/electron/package.json").read_text())["version"], "libraries": sorted(dependencies),
        "semantic_model": json.loads((ROOT / "assets/models/minilm/manifest.json").read_text()),
        "system_requirements": ["glibc >= 2.39", "Linux", "tar", "gzip", "X11 or Wayland", "xdg-open for external PDF readers"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = ROOT / "build/payload.tar.gz"
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as tar:
                for entry in sorted(STAGE.iterdir()):
                    tar.add(entry, arcname=entry.name)
    target = DIST / "AtlasPDF"
    pending = DIST / "AtlasPDF.new"
    with pending.open("wb") as result:
        with (ROOT / "build/launcher").open("rb") as bootstrap:
            shutil.copyfileobj(bootstrap, result)
        with archive.open("rb") as payload:
            shutil.copyfileobj(payload, result)
        result.write(struct.pack("<Q16s", archive.stat().st_size, b"ATLASPDFARCHIVE1"))
    pending.chmod(0o755)
    os.replace(pending, target)
    digest = hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
    (DIST / "AtlasPDF.sha256").write_text(f"{digest}  AtlasPDF\n", encoding="ascii")
    print(f"Executável único: {target} ({target.stat().st_size / 1024**2:.1f} MiB; {len(dependencies)} bibliotecas incluídas)")


if __name__ == "__main__":
    main()
