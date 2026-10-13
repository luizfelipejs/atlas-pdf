#!/usr/bin/env python3
"""Local PDF catalog. stdout is an UTF-8 JSON-lines protocol for the desktop UI."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import gzip
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any

try:
    from .classifier import CATEGORIES, classify
    from .taxonomy import subjects
    from .semantic_catalog import SemanticCatalog, migrate, themes, theme_subjects, sync_member, rebuild_centroids
    from .structure import KIND_LABELS, STRUCTURE_VERSION, metadata_title, read_structure
except ImportError:
    from classifier import CATEGORIES, classify
    from taxonomy import subjects
    from semantic_catalog import SemanticCatalog, migrate, themes, theme_subjects, sync_member, rebuild_centroids
    from structure import KIND_LABELS, STRUCTURE_VERSION, metadata_title, read_structure

BASE_DIR = Path(__file__).resolve().parent.parent
DISCOVER_SCRIPT = BASE_DIR / "scripts" / "discover_pdfs.sh"
MAX_PAGES = 60
MAX_TEXT_BYTES = 8 * 1024 * 1024
EXTRACTION_VERSION = "stratified-pages-v1"
_active_process: subprocess.Popen | None = None
_log_file: Any = None


class Cancelled(Exception):
    pass


def emit(event: dict[str, Any]) -> None:
    line = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
    print(line, flush=True)
    if _log_file is not None:
        _log_file.write(dt.datetime.now().isoformat(timespec="seconds") + " " + line + "\n")
        _log_file.flush()


def warn(message: str, path: str = "") -> None:
    emit({"type": "warning", "message": message, "path": path})


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def cancel_handler(_signum: int, _frame: Any) -> None:
    if _active_process is not None:
        stop_process(_active_process)
    raise Cancelled()


def limits() -> None:
    """Constrain untrusted Poppler input without buffering its output in RAM."""
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_TEXT_BYTES,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (28, 28))


def run_process(command: list[str], *, timeout: float, output: Any,
                limited: bool = False) -> tuple[int, str]:
    global _active_process
    with tempfile.TemporaryFile() as error:
        process = subprocess.Popen(command, stdout=output, stderr=error,
                                   start_new_session=True,
                                   preexec_fn=limits if limited else None)
        _active_process = process
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                stop_process(process)
                raise TimeoutError(f"Tempo limite de {timeout:g}s excedido")
            error.seek(0)
            detail = error.read(16384).decode("utf-8", "replace").strip()
            return code, detail
        finally:
            stop_process(process)
            _active_process = None


def normal_path(path: str | Path) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def unique_roots(paths: list[str]) -> list[str]:
    roots: list[str] = []
    for candidate in sorted({normal_path(path) for path in paths}, key=lambda p: (len(Path(p).parts), p)):
        if not any(is_within(candidate, root) for root in roots):
            roots.append(candidate)
    return roots


def is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def default_roots() -> list[str]:
    home = Path.home()
    names = ("Downloads", "Download", "Desktop", "Área de trabalho", "Área de Trabalho",
             "Documents", "Documentos", "Pictures", "Imagens", "Fotos", "Photos",
             "Videos", "Vídeos", "Music", "Música", "Músicas")
    candidates = [str(home / name) for name in names]
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r'XDG_(?:DESKTOP|DOWNLOAD|DOCUMENTS|PICTURES|VIDEOS|MUSIC)_DIR="(.*)"', line.strip())
            if match:
                value = match.group(1).replace("${HOME}", str(home)).replace("$HOME", str(home))
                value = value.replace('\\"', '"').replace("\\\\", "\\")
                if "$" not in value and os.path.isabs(value):
                    candidates.append(value)
    except OSError:
        pass
    existing = [path for path in candidates if Path(path).is_dir() and not Path(path).is_symlink()]
    return unique_roots(existing or [str(home)])


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / "catalog.sqlite3", timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '', category TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0, keywords TEXT NOT NULL DEFAULT '[]',
            pages INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0,
            mtime_ns INTEGER NOT NULL DEFAULT 0, modified TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', missing INTEGER NOT NULL DEFAULT 0,
            thumbnail TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '',
            pipeline TEXT NOT NULL DEFAULT '', manual_category TEXT NOT NULL DEFAULT '',
            scanned_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS books_category ON books(category);
        CREATE INDEX IF NOT EXISTS books_sha256 ON books(sha256);
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(books)")}
    if "analysis" not in columns:
        connection.execute("ALTER TABLE books ADD COLUMN analysis TEXT NOT NULL DEFAULT '{}'")
        connection.commit()
    migrate(connection)
    return connection


def pipeline_version() -> str:
    digest = hashlib.sha256()
    digest.update(EXTRACTION_VERSION.encode())
    for name in ("classifier.py", "taxonomy.py", "semantic.py", "semantic_catalog.py", "topics.py",
                 "structure.py", "categories.py"):
        with contextlib.suppress(OSError):
            digest.update((BASE_DIR / "backend" / name).read_bytes())
    # A catalog created without the local model must be retried after setup.
    for name in ("model.onnx", "tokenizer.json"):
        path = BASE_DIR / "assets/models/minilm" / name
        digest.update(str(path.stat().st_size if path.is_file() else 0).encode())
    return digest.hexdigest()[:24]


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def poppler_info(path: str) -> tuple[dict[str, str], str]:
    with tempfile.TemporaryFile() as output:
        code, error = run_process(["pdfinfo", path], timeout=10, output=output, limited=True)
        if code:
            return {}, error or "pdfinfo não conseguiu ler o PDF"
        output.seek(0)
        result = {}
        for line in output.read(1024 * 1024).decode("utf-8", "replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip()
        return result, ""


def sampling_ranges(pages: int) -> list[tuple[int, int]]:
    """At most 60 pages: front matter and three samples across the book."""
    if pages <= MAX_PAGES:
        return [(1, max(1, pages or MAX_PAGES))]
    selected = set(range(1, 19))
    for fraction in (.30, .55, .80):
        start = max(19, min(pages - 13, round(pages * fraction)))
        selected.update(range(start, min(pages, start + 13) + 1))
    ranges: list[tuple[int, int]] = []
    for page in sorted(selected):
        if ranges and page == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], page)
        else:
            ranges.append((page, page))
    return ranges


def extract_text(path: str, pages: int = 0) -> tuple[str, str]:
    ranges = sampling_ranges(pages)
    chunks, warnings = [], []
    deadline = time.monotonic() + 25
    budget = MAX_TEXT_BYTES
    for number, (first, last) in enumerate(ranges):
        if time.monotonic() >= deadline:
            warnings.append("Tempo limite da amostragem atingido")
            break
        with tempfile.TemporaryFile() as output:
            try:
                code, error = run_process(["pdftotext", "-enc", "UTF-8", "-f", str(first),
                                           "-l", str(last), path, "-"],
                                          timeout=max(.1, deadline - time.monotonic()), output=output, limited=True)
                if code:
                    warnings.append(error or "Não foi possível extrair um intervalo de páginas")
                    continue
                output.seek(0)
                limit = budget // (len(ranges) - number)
                chunk = output.read(limit)
                budget -= len(chunk)
                chunks.append(chunk.decode("utf-8", "replace"))
            except (OSError, TimeoutError) as error:
                warnings.append(str(error))
    text = "\f".join(chunks)
    if not re.search(r"[^\W\d_]", text, re.UNICODE):
        return "", "; ".join(warnings) or "PDF sem texto extraível; pode conter apenas imagens. OCR não está incluído."
    return text, "; ".join(warnings)


def cached_text(path: str, pages: int, digest: str, data_dir: Path) -> tuple[str, str]:
    """Share extracted samples between identical PDFs; never reuse classifications."""
    cache = data_dir / "text-cache"
    destination = cache / f"{digest}-{EXTRACTION_VERSION}.txt.gz"
    try:
        with gzip.open(destination, "rb") as source:
            encoded = source.read(MAX_TEXT_BYTES + 1)
        if len(encoded) <= MAX_TEXT_BYTES:
            return encoded.decode("utf-8", "replace"), ""
    except (OSError, EOFError):
        pass
    text, error = extract_text(path, pages)
    if text and not error:
        cache.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as output:
            temporary = Path(output.name)
            output.write(gzip.compress(text.encode("utf-8"), compresslevel=3))
        temporary.replace(destination)
    return text, error


def cached_structure(path: str, pages: int, text: str, info: dict[str, str], digest: str,
                     data_dir: Path, cacheable: bool = True) -> dict[str, Any]:
    """Outline, headings, contents, title and kind; shared by identical PDFs.

    The result depends only on the PDF bytes (text samples and pdfinfo are
    keyed by the same SHA-256), so reclassification never rereads the file.
    """
    cache = data_dir / "structure-cache"
    destination = cache / f"{digest}-{hashlib.sha256(STRUCTURE_VERSION.encode()).hexdigest()[:16]}.json.gz"
    try:
        with gzip.open(destination, "rb") as source:
            cached = json.loads(source.read(4 * 1024 * 1024).decode("utf-8"))
        if isinstance(cached, dict) and cached.get("version") == STRUCTURE_VERSION:
            return cached
    except (OSError, EOFError, ValueError):
        pass
    result = read_structure(path, pages, text, info, runner=run_process, passthrough=(Cancelled,))
    # Timeouts and failures are retried on the next scan instead of being cached.
    if cacheable and digest and result.get("status") != "unavailable" and not result.get("transient"):
        with contextlib.suppress(OSError):
            cache.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=cache, prefix=".atlas-", delete=False) as output:
                temporary = Path(output.name)
                output.write(gzip.compress(json.dumps(result, ensure_ascii=False).encode("utf-8"), compresslevel=6))
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
    return result


def compact_structure(structure: dict[str, Any]) -> dict[str, Any]:
    """A bounded copy for the catalog row; the full result stays in structure-cache."""
    result = {key: value for key, value in structure.items() if key not in ("headings", "toc", "transient")}
    result["headings"] = [dict(item, text=str(item.get("text", ""))[:120]) for item in structure.get("headings", [])[:40]]
    result["toc"] = [str(item)[:120] for item in structure.get("toc", [])[:40]]
    return result


def classify_semantic(semantic: SemanticCatalog, text: str, decision: dict[str, Any], book_id: str,
                      structure: dict[str, Any]) -> dict[str, Any]:
    try:
        accepts = "structure" in inspect.signature(semantic.classify).parameters
    except (TypeError, ValueError):
        accepts = False
    if accepts:
        return semantic.classify(text, decision, book_id, structure=structure)
    return semantic.classify(text, decision, book_id)


def consolidate(semantic: SemanticCatalog) -> list[dict[str, Any]]:
    """Catalog-wide regrouping offered by the semantic catalog, when available."""
    method = getattr(semantic, "consolidate", None)
    return list(method() or []) if callable(method) else []


def make_thumbnail(path: str, digest: str, data_dir: Path) -> str:
    if not digest or not shutil.which("pdftoppm"):
        return ""
    cache = data_dir / "thumbnails"
    cache.mkdir(exist_ok=True)
    destination = cache / f"{digest}.png"
    if destination.is_file() and destination.stat().st_size:
        return str(destination)
    with tempfile.TemporaryDirectory(prefix="cover-", dir=cache) as temporary:
        prefix = str(Path(temporary) / "cover")
        try:
            code, _ = run_process(["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "280",
                                   "-png", path, prefix], timeout=8,
                                  output=subprocess.DEVNULL, limited=True)
            generated = Path(prefix + ".png")
            if code == 0 and generated.is_file() and generated.stat().st_size:
                generated.replace(destination)
                return str(destination)
        except (OSError, TimeoutError):
            pass
    return ""


def index_book(path: str, stat: os.stat_result, data_dir: Path,
               previous: sqlite3.Row | None, version: str,
               semantic: SemanticCatalog | None = None) -> dict[str, Any]:
    title = Path(path).stem
    book: dict[str, Any] = {
        "id": hashlib.sha256(os.fsencode(path)).hexdigest(), "path": path,
        "title": title, "author": "", "category": "Revisar", "confidence": 0,
        "keywords": "[]", "pages": 0, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "modified": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
        "status": "review", "reason": "", "missing": 0, "thumbnail": "", "sha256": "",
        "pipeline": version, "manual_category": previous["manual_category"] if previous else "",
        "scanned_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "analysis": "{}",
    }
    try:
        book["sha256"] = file_sha256(path)
        info, info_error = poppler_info(path)
        if info_error:
            book.update(status="error", reason="PDF ilegível: " + info_error[:1000])
        else:
            # Placeholder metadata ("Microsoft Word - doc1.docx", "untitled") is no title.
            book["title"] = metadata_title(info) or title
            book["author"] = info.get("Author", "")
            book["pages"] = int(info.get("Pages", "0"))
            text, error = cached_text(path, book["pages"], book["sha256"], data_dir)
            structure = cached_structure(path, book["pages"], text, info, book["sha256"], data_dir,
                                         cacheable=bool(text) and not error)
            if not text:
                book["reason"] = error[:1000]
                if structure.get("status") != "unavailable":
                    book["analysis"] = json.dumps({"structure": compact_structure(structure)}, ensure_ascii=True)
            else:
                decision = classify(text, title=book["title"], filename=Path(path).stem)
                if semantic is not None:
                    decision = classify_semantic(semantic, text, decision, book["id"], structure)
                category = decision.get("category", "Revisar")
                if category not in CATEGORIES and (semantic is None or category not in themes(semantic.connection)):
                    category = "Revisar"
                book.update(category=category,
                            confidence=max(0.0, min(1.0, float(decision.get("confidence", 0)))),
                            keywords=json.dumps(decision.get("keywords", []), ensure_ascii=True),
                            reason=str(decision.get("reason", "")),
                            status="review" if category == "Revisar" else "indexed")
                analysis = decision.get("analysis", {})
                analysis["structure"] = compact_structure(structure)
                ranges = sampling_ranges(book["pages"])
                analysis["sample_ranges"] = ranges
                analysis["sample_pages"] = sum(last - first + 1 for first, last in ranges)
                analysis["total_pages"] = book["pages"]
                analysis["sample_warning"] = error
                book["analysis"] = json.dumps(analysis, ensure_ascii=True)
                if book["pages"] > MAX_PAGES:
                    book["reason"] += f" Amostragem distribuída: {analysis['sample_pages']} de {book['pages']} páginas."
                if error:
                    book["reason"] += " Amostragem parcial: " + error[:500]
            book["thumbnail"] = make_thumbnail(path, book["sha256"], data_dir)
    except (OSError, ValueError, TimeoutError) as error:
        book.update(status="error", reason=f"Não foi possível analisar o PDF: {error}")
    if book["manual_category"]:
        book["category"] = book["manual_category"]
        book["confidence"] = 1.0
        book["reason"] = "Categoria escolhida manualmente. " + book["reason"]
        if book["status"] != "error":
            book["status"] = "review" if book["category"] == "Revisar" else "indexed"
    return book


def save_book(connection: sqlite3.Connection, book: dict[str, Any]) -> None:
    # A manual choice made while Poppler was reading the file takes precedence.
    with connection:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT manual_category FROM books WHERE id=?", (book["id"],)).fetchone()
        if current and current["manual_category"]:
            book["manual_category"] = book["category"] = current["manual_category"]
            book["confidence"] = 1.0
            if not book["reason"].startswith("Categoria escolhida manualmente."):
                book["reason"] = "Categoria escolhida manualmente. " + book["reason"]
            if book["status"] != "error":
                book["status"] = "review" if book["category"] == "Revisar" else "indexed"
        columns = list(book)
        query = (f"INSERT INTO books ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                 f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{key}=excluded.{key}' for key in columns if key != 'id')}")
        connection.execute(query, [book[key] for key in columns])
        sync_member(connection, book["id"], json.loads(book.get("analysis", "{}")))
        rebuild_centroids(connection)


def catalog(connection: sqlite3.Connection) -> dict[str, Any]:
    books = []
    labels = {subject["name"]: subject["label"] for subject in theme_subjects(connection)}
    for row in connection.execute("SELECT * FROM books ORDER BY category COLLATE NOCASE,title COLLATE NOCASE,path"):
        book = dict(row)
        book["keywords"] = json.loads(book["keywords"])
        book["analysis"] = json.loads(book["analysis"])
        book["category_label"] = labels.get(book["category"], book["category"])
        book["missing"] = bool(book["missing"])
        book["manual"] = bool(book.pop("manual_category"))
        book.pop("pipeline")
        books.append(book)
    stats = {"total": len(books),
             "categorized": sum(book["category"] != "Revisar" for book in books),
             "review": sum(book["category"] == "Revisar" for book in books),
             "missing": sum(book["missing"] for book in books)}
    return {"type": "catalog", "books": books, "stats": stats}


def atomic_text(output: Path, content: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                     prefix=".atlas-", delete=False) as temporary:
        name = temporary.name
        try:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    try:
        os.replace(name, output)
    finally:
        Path(name).unlink(missing_ok=True)


def write_paths(connection: sqlite3.Connection, data_dir: Path) -> None:
    content = "".join(json.dumps(row[0], ensure_ascii=False) + "\n"
                      for row in connection.execute("SELECT path FROM books ORDER BY path"))
    atomic_text(data_dir / "paths.txt", content)


def discover(root: str) -> tuple[list[str], bool, str]:
    if not os.path.isdir(root) or os.path.islink(root):
        return [], False, "Pasta não existe, não é acessível ou é um link simbólico."
    with tempfile.TemporaryFile() as output:
        code, error = run_process(["bash", str(DISCOVER_SCRIPT), root], timeout=600, output=output)
        output.seek(0)
        found: list[str] = []
        remainder = b""
        while chunk := output.read(1024 * 1024):
            parts = (remainder + chunk).split(b"\0")
            remainder = parts.pop()
            for value in parts:
                path = os.fsdecode(value)
                # SQLite stores UTF-8; invalid byte sequences are not valid UTF-8 paths.
                if any(0xD800 <= ord(char) <= 0xDFFF for char in path):
                    warn("Nome de arquivo não é UTF-8 válido; caminho ignorado.", repr(path))
                    continue
                found.append(path)
        return found, code == 0, error


def scan(connection: sqlite3.Connection, data_dir: Path, arguments: argparse.Namespace) -> int:
    global _log_file
    roots = list(arguments.root or [])
    if arguments.all_home:
        roots.append(str(Path.home()))
    if arguments.computer:
        roots.extend(path for path in ("/home", "/media", "/mnt", "/run/media", "/opt", "/srv")
                     if os.path.isdir(path))
    roots = unique_roots(roots or default_roots())
    log_dir = data_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"scan-{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{os.getpid()}.txt"
    counts = {"total": 0, "indexed": 0, "unchanged": 0, "warnings": 0, "missing": 0,
              "cancelled": False, "log": str(log_path)}
    successful_roots: list[str] = []
    found: set[str] = set()
    with open(data_dir / "scan.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Outra indexação já está em andamento neste catálogo.")
        with open(log_path, "a", encoding="utf-8") as log:
            _log_file = log
            try:
                emit({"type": "progress", "phase": "discover", "current": 0, "total": len(roots),
                      "path": "", "message": "Localizando PDFs com Bash…"})
                for number, root in enumerate(roots, 1):
                    try:
                        paths, success, error = discover(root)
                        found.update(paths)
                        if success:
                            successful_roots.append(root)
                        else:
                            counts["warnings"] += 1
                            warn("Busca incompleta nesta pasta: " + (error[:1500] or "erro de leitura"), root)
                    except (OSError, TimeoutError) as error:
                        counts["warnings"] += 1
                        warn(str(error), root)
                    emit({"type": "progress", "phase": "discover", "current": number,
                          "total": len(roots), "path": root, "message": f"{len(found)} PDFs encontrados"})
                counts["total"] = len(found)
                version = pipeline_version()
                rebuild_centroids(connection)
                connection.commit()
                semantic = SemanticCatalog(connection)
                semantic_warning_reported = False
                for number, path in enumerate(sorted(found), 1):
                    emit({"type": "progress", "phase": "index", "current": number,
                          "total": len(found), "path": path, "message": "Analisando PDF"})
                    try:
                        stat = os.stat(path, follow_symlinks=False)
                        if not os.path.isfile(path) or os.path.islink(path):
                            raise OSError("O arquivo não é mais um PDF regular disponível")
                        previous = connection.execute("SELECT * FROM books WHERE path=?", (path,)).fetchone()
                        if (previous and not arguments.force and previous["mtime_ns"] == stat.st_mtime_ns
                                and previous["size"] == stat.st_size and previous["pipeline"] == version
                                and json.loads(previous["analysis"]).get("semantic", {}).get("status") != "unavailable"
                                and json.loads(previous["analysis"]).get("topic", {}).get("status") != "suggested"):
                            connection.execute("UPDATE books SET missing=0 WHERE path=?", (path,))
                            sync_member(connection, previous["id"])
                            rebuild_centroids(connection)
                            connection.commit()
                            counts["unchanged"] += 1
                            continue
                        book = index_book(path, stat, data_dir, previous, version, semantic)
                        save_book(connection, book)
                        if semantic.unavailable and not semantic_warning_reported:
                            counts["warnings"] += 1
                            warn(semantic.unavailable)
                            semantic_warning_reported = True
                        counts["indexed"] += 1
                        if book["status"] == "error":
                            counts["warnings"] += 1
                            warn(book["reason"], path)
                    except OSError as error:
                        counts["warnings"] += 1
                        warn(str(error), path)
                for row in connection.execute("SELECT path,missing FROM books").fetchall():
                    if (row["path"] not in found and any(is_within(row["path"], root) for root in successful_roots)):
                        connection.execute("UPDATE books SET missing=1 WHERE path=?", (row["path"],))
                connection.commit()
                for change in consolidate(semantic):
                    emit({"type": "reclassified", **change})
                rebuild_centroids(connection)
                connection.commit()
            except Cancelled:
                counts["cancelled"] = True
                warn("Indexação cancelada; resultados já concluídos foram preservados.")
            finally:
                write_paths(connection, data_dir)
                counts["missing"] = connection.execute("SELECT count(*) FROM books WHERE missing=1").fetchone()[0]
                emit({"type": "complete", **counts})
                _log_file = None
    return 130 if counts["cancelled"] else 0


def reclassify(connection: sqlite3.Connection, data_dir: Path, arguments: argparse.Namespace) -> int:
    """Reevaluate known PDFs without discovering or moving any original files."""
    global _log_file
    rows = connection.execute("SELECT * FROM books ORDER BY path").fetchall()
    if arguments.id:
        requested = set(arguments.id)
        rows = [row for row in rows if row["id"] in requested]
        if len(rows) != len(requested):
            raise ValueError("Um dos PDFs solicitados não está no catálogo")
    log_dir = data_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    log_path = log_dir / f"reclassify-{stamp}.txt"
    counts = {"total": len(rows), "indexed": 0, "unchanged": 0, "warnings": 0,
              "missing": 0, "changed": 0, "manual_preserved": 0, "cancelled": False, "log": str(log_path)}
    changes = []
    with open(data_dir / "scan.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Outra indexação já está em andamento neste catálogo.")
        backups = data_dir / "backups"
        backups.mkdir(exist_ok=True)
        backup = backups / f"catalog-before-reclassify-{stamp}.sqlite3"
        with sqlite3.connect(backup) as target:
            connection.backup(target)
        counts["backup"] = str(backup)
        with open(log_path, "a", encoding="utf-8") as log:
            _log_file = log
            try:
                version = pipeline_version()
                rebuild_centroids(connection)
                connection.commit()
                semantic = SemanticCatalog(connection)
                semantic_warning_reported = False
                for number, row in enumerate(rows, 1):
                    emit({"type": "progress", "phase": "classify", "current": number,
                          "total": len(rows), "path": row["path"], "message": "Analisando tema e assuntos de apoio"})
                    try:
                        stat = os.stat(row["path"], follow_symlinks=False)
                        if not os.path.isfile(row["path"]) or os.path.islink(row["path"]):
                            raise OSError("PDF original não está disponível")
                        if (not arguments.force and row["pipeline"] == version and row["size"] == stat.st_size
                                and row["mtime_ns"] == stat.st_mtime_ns
                                and json.loads(row["analysis"]).get("semantic", {}).get("status") != "unavailable"
                                and json.loads(row["analysis"]).get("topic", {}).get("status") != "suggested"):
                            counts["unchanged"] += 1
                            continue
                        # Fetch the latest manual choice, rather than a choice
                        # captured before a potentially long reclassification.
                        previous = connection.execute("SELECT * FROM books WHERE id=?", (row["id"],)).fetchone()
                        book = index_book(row["path"], stat, data_dir, previous, version, semantic)
                        save_book(connection, book)
                        if semantic.unavailable and not semantic_warning_reported:
                            counts["warnings"] += 1
                            warn(semantic.unavailable)
                            semantic_warning_reported = True
                        counts["indexed"] += 1
                        counts["manual_preserved"] += bool(book["manual_category"])
                        if book["category"] != row["category"]:
                            counts["changed"] += 1
                            change = {"id": book["id"], "title": book["title"], "from": row["category"],
                                      "to": book["category"], "reason": book["reason"]}
                            changes.append(change)
                            emit({"type": "reclassified", **change})
                        if book["status"] == "error":
                            counts["warnings"] += 1
                            warn(book["reason"], row["path"])
                    except OSError as error:
                        counts["warnings"] += 1
                        warn(str(error), row["path"])
                        if not os.path.exists(row["path"]):
                            connection.execute("UPDATE books SET missing=1 WHERE id=?", (row["id"],))
                            rebuild_centroids(connection)
                            connection.commit()
                for change in consolidate(semantic):
                    emit({"type": "reclassified", **change})
                    existing = next((item for item in changes if item["id"] == change["id"]), None)
                    if existing is None:
                        if change.get("from") != change.get("to"):
                            changes.append(change)
                            counts["changed"] += 1
                    else:
                        existing.update(to=change.get("to"), reason=change.get("reason", existing["reason"]))
                        if existing["from"] == existing["to"]:
                            changes.remove(existing)
                            counts["changed"] -= 1
                rebuild_centroids(connection)
                connection.commit()
            except Cancelled:
                counts["cancelled"] = True
                warn("Reclassificação cancelada; resultados concluídos foram preservados.")
            finally:
                write_paths(connection, data_dir)
                counts["missing"] = connection.execute("SELECT count(*) FROM books WHERE missing=1").fetchone()[0]
                report = data_dir / "reclassification-latest.json"
                atomic_text(report, json.dumps({**counts, "changes": changes}, ensure_ascii=False, indent=2) + "\n")
                emit({"type": "complete", **counts, "report": str(report)})
                _log_file = None
    return 130 if counts["cancelled"] else 0


def export_catalog(connection: sqlite3.Connection, output: Path) -> None:
    data = catalog(connection)
    lines = ["ATLAS PDF — CATÁLOGO LOCAL", f"Gerado em: {dt.datetime.now().isoformat(timespec='seconds')}",
             f"Total: {data['stats']['total']} PDFs", ""]
    for book in data["books"]:
        lines.extend([f"[{book['category_label']}] {json.dumps(book['title'], ensure_ascii=False)}",
                      "Caminho: " + json.dumps(book["path"], ensure_ascii=False),
                      f"Páginas: {book['pages']} | Estado: {'ausente' if book['missing'] else book['status']}",
                      "Palavras: " + ", ".join(f"{item['word']} ({item['count']})" for item in book["keywords"]),
                      "Tema sugerido: " + json.dumps(book["analysis"].get("topic", {}).get("label", ""), ensure_ascii=False),
                      "Tipo: " + KIND_LABELS.get(book["analysis"].get("structure", {}).get("kind", ""), "não identificado")
                      + " | Título lido: " + json.dumps(book["analysis"].get("structure", {}).get("title", ""), ensure_ascii=False),
                      "Motivo: " + json.dumps(book["reason"], ensure_ascii=False), ""])
    atomic_text(output, "\n".join(lines) + "\n")
    emit({"type": "exported", "output": str(output), "total": data["stats"]["total"]})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Indexação local de PDFs com Bash, Python e Poppler")
    default_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "atlas-pdf"
    result.add_argument("--data-dir", type=Path, default=default_data)
    commands = result.add_subparsers(dest="command", required=True)
    scanning = commands.add_parser("scan")
    scanning.add_argument("--root", action="append")
    scanning.add_argument("--all-home", action="store_true")
    scanning.add_argument("--computer", action="store_true")
    scanning.add_argument("--force", action="store_true")
    reclassification = commands.add_parser("reclassify")
    reclassification.add_argument("--id", action="append")
    reclassification.add_argument("--force", action="store_true")
    commands.add_parser("list")
    commands.add_parser("categories")
    commands.add_parser("defaults")
    manual = commands.add_parser("set-category")
    manual.add_argument("--id", required=True)
    manual.add_argument("--category", required=True)
    exporting = commands.add_parser("export")
    exporting.add_argument("--output", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    signal.signal(signal.SIGTERM, cancel_handler)
    signal.signal(signal.SIGINT, cancel_handler)
    data_dir = Path(normal_path(arguments.data_dir))
    try:
        if arguments.command == "defaults":
            emit({"type": "defaults", "roots": default_roots(), "home": str(Path.home())})
            return 0
        connection = connect(data_dir)
        try:
            if arguments.command == "categories":
                discovered = themes(connection)
                emit({"type": "categories", "categories": [*CATEGORIES, *discovered],
                      "subjects": [*subjects(), *theme_subjects(connection)]})
                return 0
            if arguments.command == "scan":
                for dependency in ("bash", "pdfinfo", "pdftotext"):
                    if not shutil.which(dependency):
                        raise RuntimeError(f"Dependência ausente: {dependency}. Instale Bash e poppler-utils.")
                return scan(connection, data_dir, arguments)
            if arguments.command == "reclassify":
                return reclassify(connection, data_dir, arguments)
            if arguments.command == "list":
                emit(catalog(connection))
            elif arguments.command == "set-category":
                if arguments.category not in CATEGORIES and arguments.category not in themes(connection):
                    raise ValueError("Categoria desconhecida")
                cursor = connection.execute("UPDATE books SET category=?,manual_category=?,confidence=1.0,"
                                            "reason=CASE WHEN reason LIKE 'Categoria escolhida manualmente.%' "
                                            "THEN reason ELSE 'Categoria escolhida manualmente. ' || reason END,"
                                            "status=CASE WHEN status='error' THEN status WHEN ?='Revisar' "
                                            "THEN 'review' ELSE 'indexed' END WHERE id=?",
                                            (arguments.category, arguments.category, arguments.category, arguments.id))
                if cursor.rowcount != 1:
                    raise ValueError("PDF não encontrado no catálogo")
                sync_member(connection, arguments.id)
                rebuild_centroids(connection)
                connection.commit()
                emit({"type": "updated", "id": arguments.id, "category": arguments.category})
            elif arguments.command == "export":
                export_catalog(connection, Path(normal_path(arguments.output)))
        finally:
            connection.close()
    except Cancelled:
        return 130
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr, flush=True)
        emit({"type": "error", "message": str(error)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
