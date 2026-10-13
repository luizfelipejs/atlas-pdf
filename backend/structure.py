"""Document structure: outline, typographic headings, contents, title and kind.

Only a bounded set of pages is read with Poppler's ``pdftohtml -xml`` (front
matter plus a few distributed windows), inside the same sandbox used for text
extraction. Every score here is a ranking heuristic, not a probability.
The folder that contains a PDF is never used.
"""
from __future__ import annotations

from collections import Counter
import contextlib
import html
import os
import re
import resource
import signal
import subprocess
import tempfile
import time
import unicodedata
from typing import Any, Callable

STRUCTURE_VERSION = "atlas-structure-2:outline-fonts-toc-kind-title"
KINDS = ("livro", "apostila", "lista_exercicios", "prova", "artigo", "tese",
         "slides", "formulario", "manual", "documento")
KIND_LABELS = {"livro": "Livro", "apostila": "Apostila / notas de aula",
               "lista_exercicios": "Lista de exercícios", "prova": "Prova / exame",
               "artigo": "Artigo", "tese": "Tese / dissertação", "slides": "Slides",
               "formulario": "Formulário", "manual": "Manual", "documento": "Documento"}
MAX_HEADINGS = 80
MAX_TOC = 120
FRONT_PAGES = 24
WINDOW_PAGES = 4
BUDGET_SECONDS = 3.0
MAX_XML_BYTES = 6 * 1024 * 1024
MAX_OUTLINE_ITEMS = 4000
KIND_THRESHOLD = .4

Runner = Callable[..., tuple[int, str]]


# ---------------------------------------------------------------- utilities

def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))


SPACING_ACCENTS = {"\u00b4": "\u0301", "\u02dd": "\u030b", "\u02dc": "\u0303", "\u00a8": "\u0308",
                   "\u02c6": "\u0302", "\u00b8": "\u0327", "\u02c7": "\u030c", "\u02d8": "\u0306",
                   "\u02d9": "\u0307", "\u02da": "\u030a", "\u02db": "\u0328"}
ACCENT_CLASS = "\u00b4\u02dd\u02dc\u00a8\u02c6\u00b8\u02c7\u02d8\u02d9\u02da\u02db\u0300-\u036f"
ACCENT_ONLY = re.compile(f"^[{ACCENT_CLASS}]+$")
ACCENT_BEFORE_LETTER = re.compile("([\u00b4\u02dd\u02dc\u00a8\u02c6\u02c7\u02d8\u02d9\u02da])([A-Za-z\u0131])")


def combining(accent: str) -> str:
    return "".join(SPACING_ACCENTS.get(char, char) for char in accent)


def clean_line(text: str) -> str:
    """Normalize whitespace and accents written before the letter ("Introdu¸c˜ao")."""
    text = text.replace("\u00ad", "")
    # TeX's cedilla is drawn after its "c" ("ac¸˜ao"), pdftotext may put it before ("¸c").
    text = re.sub("[\u00b8\u02db]([cC])", lambda m: m.group(1) + "\u0327", text)
    text = re.sub("([cCsStT])[\u00b8\u02db]", lambda m: m.group(1) + "\u0327", text)
    text = ACCENT_BEFORE_LETTER.sub(
        lambda m: ("i" if m.group(2) == "\u0131" else m.group(2)) + combining(m.group(1)), text)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


def default_runner(command: list[str], *, timeout: float, output: Any,
                   limited: bool = False) -> tuple[int, str]:
    """Standalone equivalent of indexer.run_process (the indexer passes its own)."""
    with tempfile.TemporaryFile() as error:
        process = subprocess.Popen(command, stdout=output, stderr=error, start_new_session=True,
                                   preexec_fn=_limits if limited else None)
        try:
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                raise TimeoutError(f"Tempo limite de {timeout:g}s excedido")
            error.seek(0)
            return code, error.read(16384).decode("utf-8", "replace").strip()
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def empty_result(status: str = "unavailable", warning: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": STRUCTURE_VERSION, "status": status, "kind": "documento",
        "kind_label": KIND_LABELS["documento"], "kind_scores": {}, "kind_evidence": [],
        "title": "", "title_source": "", "headings": [], "toc": [], "language": "",
        "stats": {}}
    if warning:
        result["warning"] = warning
    return result


# ------------------------------------------------------------- XML parsing

TOKEN = re.compile(r'<page\b[^>]*>|<fontspec\b[^>]*/>|<text\b[^>]*>.*?</text>|<outline>|</outline>|<item\b[^>]*>.*?</item>',
                   re.S)
ATTR = re.compile(r'(\w+)="([^"]*)"')
TAG = re.compile(r"<[^>]+>")
BOLD_FAMILY = re.compile(r"(?i)bold|black|heavy|semibold|demi|cmbx|cmb\d|sfbx|bx\d|-medi\b|medi$|-bd\b|,bd\b|bd$|extrab|ultrab")
CAPS_FAMILY = re.compile(r"(?i)cmcsc|sfcc|smallcaps|-sc\b|caps")
MATH_FAMILY = re.compile(r"(?i)cmmi|cmsy|cmex|msam|msbm|math|symbol|stix|eufm|rsfs|wasy|esint|txsy|pxsy|cmbsy|cmmib|lmmath|asana")


def _number(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except ValueError:
        return default


def parse_xml(xml: str) -> dict[str, Any]:
    pages: dict[int, dict[str, Any]] = {}
    fonts: dict[str, dict[str, Any]] = {}
    outline: list[dict[str, Any]] = []
    outline_seen = False
    depth = 0
    current = 0
    for match in TOKEN.finditer(xml):
        token = match.group(0)
        if token.startswith("<text"):
            if not current:
                continue
            head, _, rest = token.partition(">")
            attrs = dict(ATTR.findall(head))
            inner = rest[:-len("</text>")]
            plain = html.unescape(TAG.sub("", inner))
            if not plain.strip():
                continue
            bold_chars = sum(len(html.unescape(TAG.sub("", part))) for part in re.findall(r"<b>(.*?)</b>", inner, re.S))
            font = fonts.get(attrs.get("font", ""), {})
            pages[current]["texts"].append({
                "top": _number(attrs.get("top", "0")), "left": _number(attrs.get("left", "0")),
                "width": _number(attrs.get("width", "0")), "height": _number(attrs.get("height", "0")),
                "size": font.get("size", 0.0), "family": font.get("family", ""),
                "bold": font.get("bold", False) or bold_chars >= .7 * len(plain.strip()),
                "italic": "<i>" in inner, "text": plain})
        elif token.startswith("<fontspec"):
            attrs = dict(ATTR.findall(token))
            family = attrs.get("family", "")
            fonts[attrs.get("id", "")] = {"size": _number(attrs.get("size", "0")), "family": family,
                                          "bold": bool(BOLD_FAMILY.search(family.split("+")[-1]))}
        elif token.startswith("<page"):
            attrs = dict(ATTR.findall(token))
            current = int(_number(attrs.get("number", "0")))
            if current and current not in pages:
                pages[current] = {"number": current, "height": _number(attrs.get("height", "0")),
                                  "width": _number(attrs.get("width", "0")), "texts": []}
        elif token == "<outline>":
            depth += 1
        elif token == "</outline>":
            depth -= 1
            if depth <= 0:
                outline_seen = True
                depth = 0
        elif token.startswith("<item") and depth > 0 and not outline_seen and len(outline) < MAX_OUTLINE_ITEMS:
            head, _, rest = token.partition(">")
            attrs = dict(ATTR.findall(head))
            title = clean_line(html.unescape(TAG.sub("", rest[:-len("</item>")])))
            if title:
                outline.append({"text": title, "level": depth,
                                "page": int(_number(attrs.get("page", "0")))})
    return {"pages": pages, "outline": outline}


def _attach_accents(row: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LaTeX fonts draw accents as separate glyphs; put each after the letter beneath it."""
    accents = [run for run in row if ACCENT_ONLY.match(run["text"].strip() or "x")]
    if not accents:
        return row
    accent_ids = {id(run) for run in accents}
    runs = [dict(run) for run in row if id(run) not in accent_ids]
    for accent in accents:
        center = accent["left"] + accent["width"] / 2
        mark = combining(accent["text"].strip())
        tolerance = .6 * max(accent["height"], 1)

        def distance(run: dict[str, Any]) -> float:
            return max(run["left"] - center, center - run["left"] - run["width"], 0.0)

        candidates = [run for run in runs if run["text"].strip() and distance(run) <= tolerance]
        if not candidates:
            continue
        run = min(candidates, key=distance)
        text = run["text"]
        index = min(len(text) - 1, max(0, int((center - run["left"]) / max(run["width"], 1) * len(text))))
        if mark in ("\u0327", "\u0328"):
            # Cedilla/ogonek hang below the letter they follow.
            nearby = [i for i in (index, index - 1, index + 1) if 0 <= i < len(text) and text[i] in "cCsStTaAeE"]
            index = nearby[0] if nearby else index
        elif not text[index].isalpha():
            nearby = [i for i in (index + 1, index - 1) if 0 <= i < len(text) and text[i].isalpha()]
            index = nearby[0] if nearby else index
        run["text"] = text[:index + 1] + mark + text[index + 1:]
    return runs


def _join_runs(runs: list[dict[str, Any]]) -> str:
    text = ""
    previous = None
    for run in runs:
        piece = run["text"]
        if previous is not None and not text.endswith(" ") and not piece.startswith(" "):
            gap = run["left"] - (previous["left"] + previous["width"])
            if gap > .12 * max(previous["height"], run["height"], 1):
                text += " "
        text += piece
        previous = run
    return unicodedata.normalize("NFC", text)


def build_lines(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Group text runs into visual lines, split by wide horizontal gaps."""
    runs = sorted((item for item in page["texts"] if item["width"] > 0 and item["height"] > 0),
                  key=lambda item: (item["top"], item["left"]))
    rows: list[list[dict[str, Any]]] = []
    for run in runs:
        if rows:
            base = rows[-1][0]
            if abs(run["top"] - base["top"]) <= max(2.0, .45 * min(base["height"], run["height"])):
                rows[-1].append(run)
                continue
        rows.append([run])
    lines = []
    for row in rows:
        row = sorted(_attach_accents(row), key=lambda item: item["left"])
        if not row:
            continue
        segments: list[list[dict[str, Any]]] = [[row[0]]]
        for run in row[1:]:
            previous = segments[-1][-1]
            gap = run["left"] - (previous["left"] + previous["width"])
            if gap > max(3.0 * max(previous["height"], run["height"]), .06 * max(page["width"], 1)):
                segments.append([run])
            else:
                segments[-1].append(run)
        parts = []
        for segment in segments:
            text = clean_line(_join_runs(segment))
            if not text:
                continue
            weights = Counter()
            bold = math = 0
            for run in segment:
                length = len(run["text"].strip()) or 1
                weights[round(run["size"] * 2) / 2] += length
                bold += length if run["bold"] else 0
                math += length if MATH_FAMILY.search(run["family"]) else 0
            total = sum(weights.values()) or 1
            families = Counter()
            for run in segment:
                families[run["family"].split("+")[-1]] += len(run["text"].strip()) or 1
            parts.append({"text": text, "size": weights.most_common(1)[0][0], "max_size": max(weights),
                          "bold": bold >= .75 * total, "math": math / total,
                          "family": families.most_common(1)[0][0],
                          "left": segment[0]["left"],
                          "right": segment[-1]["left"] + segment[-1]["width"],
                          "top": min(run["top"] for run in segment),
                          "height": max(run["height"] for run in segment), "chars": total})
        if parts:
            lines.append({"page": page["number"], "top": parts[0]["top"], "segments": parts,
                          "text": clean_line(" ".join(part["text"] for part in parts)),
                          "page_height": page["height"], "page_width": page["width"]})
    return lines


# ------------------------------------------------------------ typography

LABEL_LINE = re.compile(r"(?i)^(?:teorema|theorem|lema|lemma|defini[cç][aã]o|definition|proposi[cç][aã]o|proposition|"
                        r"corol[aá]rio|corollary|exemplos?|examples?|observa[cç][aã]o|remark|nota|note|prova|proof|"
                        r"demonstra[cç][aã]o|exerc[ií]cios?|exercises?|problemas?|problems?|solu[cç][aã]o|solution|"
                        r"resposta|answer|quest[aã]o|question|figura|figure|fig\.|tabela|table|quadro|gr[aá]fico|"
                        r"algoritmo|algorithm|claim|afirma[cç][aã]o|conjectura|conjecture|axioma|axiom|"
                        r"hip[oó]tese|passo|step|caso|case|fato|fact|propriedade|property|nota[cç][aã]o|notation|"
                        r"listing|c[oó]digo|equa[cç][aã]o|equation)\b\s*[\dIVX.:()\-–—]*")
NOISE_LINE = re.compile(r"(?i)https?://|www\.|@[\w-]+\.|\bdoi\b|arxiv:|issn|isbn|copyright|©|"
                        r"all rights reserved|direitos reservados|\bp[aá]g(?:ina)?\.?\s*\d|\bpage \d+")
INSTITUTION = re.compile(r"(?i)\b(?:universidade|university|universit[éey]|universidad|instituto|institute|institut|"
                         r"departamento|department|faculdade|faculty|college|school|escola|centro de|center for|"
                         r"minist[eé]rio|secretaria|laborat[oó]rio|laboratory|program[a]? de|campus|"
                         r"unicamp|usp|ufrj|ufmg|unesp|impa|mit|stanford)\b")
JUNK_TITLE = re.compile(r"(?i)^(?:microsoft (?:word|powerpoint|excel)\b.*|untitled.*|sem t[ií]tulo.*|"
                        r"documento?\s*\d*|doc\d*|slide\s*\d*|apresenta[cç][aã]o\s*\d*|presentation\s*\d*|"
                        r"powerpoint presentation|apresenta[cç][aã]o do powerpoint|layout\s*\d*|template|"
                        r"title|t[ií]tulo|book|livro|main|paper|article|thesis|report|draft|output|print|"
                        r"camscanner.*|scanned.*|scan\s*\d*|img[_\- ]?\d+.*|image\s*\d*|pdf|adobe.*|"
                        r"iso 15930.*|lncs.*|\\title.*|none|null|default|\d+|[\W_]+|\d{4,}\b.*|.*\bprint\b.*\d.*|"
                        r".*\.(?:pdf|docx?|tex|dvi|indd|djvu)\b.*|[a-z]{1,3}\d[\w\-]*|"
                        r"(?:cover|capa|front matter|frontmatter|title page|half title|contents|sum[aá]rio))$")
FILE_EXTENSION = re.compile(r"(?i)\.(?:pdf|docx?|odt|rtf|tex|dvi|ps|djvu|indd|qxd|pptx?|xlsx?|txt|html?|cdr|ai|p65|fm|pm\d)\s*$")


def _letters(text: str) -> int:
    return sum(char.isalpha() for char in text)


def _repeated(lines: list[dict[str, Any]]) -> set[str]:
    """Running headers/footers: the same normalized text on several pages."""
    pages: dict[str, set[int]] = {}
    for line in lines:
        key = re.sub(r"[\d\W_]+", " ", fold(line["text"])).strip()
        if key:
            pages.setdefault(key, set()).add(line["page"])
    sampled = len({line["page"] for line in lines})
    minimum = 3 if sampled >= 5 else 99
    return {key for key, found in pages.items() if len(found) >= max(minimum, .2 * sampled)}


def body_size(lines: list[dict[str, Any]]) -> float:
    weights = Counter()
    for line in lines:
        for segment in line["segments"]:
            weights[segment["size"]] += segment["chars"]
    return weights.most_common(1)[0][0] if weights else 0.0


def _heading_candidate(segment: dict[str, Any], line: dict[str, Any], body: float) -> str:
    text = segment["text"]
    letters = _letters(text)
    if letters < 3 or len(text) > 120 or len(text.split()) > 16 or segment["math"] > .3:
        return ""
    if text[0].islower() or text.endswith((",", ";", ":")) and len(text) > 40:
        return ""
    if LABEL_LINE.match(text) or NOISE_LINE.search(text):
        return ""
    if letters < .5 * len(text.replace(" ", "")):
        return ""
    ratio = segment["size"] / body if body else 1.0
    alone = len(line["segments"]) == 1
    if ratio >= 1.15:
        return "size"
    if alone and ratio >= .95 and (segment["bold"] or CAPS_FAMILY.search(segment["family"])):
        if text.endswith(".") and len(text) > 40:
            return ""
        return "weight"
    return ""


def font_headings(lines: list[dict[str, Any]], body: float, skip_pages: set[int],
                  title: str) -> list[dict[str, Any]]:
    repeated = _repeated(lines)
    folded_title = fold(title)
    found: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if line["page"] in skip_pages:
            continue
        key = re.sub(r"[\d\W_]+", " ", fold(line["text"])).strip()
        if key in repeated:
            continue
        for segment in line["segments"]:
            reason = _heading_candidate(segment, line, body)
            if not reason:
                continue
            item = {"text": segment["text"], "size": segment["size"], "bold": segment["bold"],
                    "reason": reason, "page": line["page"], "top": segment["top"],
                    "height": segment["height"], "index": index}
            previous = found[-1] if found else None
            # Join headings that wrap onto a second or third line.
            if (previous and previous["page"] == item["page"] and previous["size"] == item["size"]
                    and previous["bold"] == item["bold"] and previous["index"] == index - 1
                    and 0 < item["top"] - previous["top"] <= 1.9 * max(previous["height"], 1)
                    and previous.get("lines", 1) < 3 and len(previous["text"]) + len(item["text"]) < 160):
                previous["text"] += " " + item["text"]
                previous["lines"] = previous.get("lines", 1) + 1
                previous["index"] = index
                previous["top"] = item["top"]
                continue
            found.append(item)
    total = sum(len(line["segments"]) for line in lines) or 1
    weight_only = [item for item in found if item["reason"] == "weight"]
    if len(weight_only) > .25 * total:
        found = [item for item in found if item["reason"] != "weight"]
    found = [item for item in found if fold(item["text"]) != folded_title]
    sizes = sorted({item["size"] for item in found if item["reason"] == "size"}, reverse=True)
    clusters: list[float] = []
    for size in sizes:
        if not clusters or size < clusters[-1] * .94:
            clusters.append(size)

    def level(item: dict[str, Any]) -> int:
        if item["reason"] == "weight":
            return min(len(clusters) + 1, 6)
        for number, size in enumerate(clusters, 1):
            if item["size"] >= size * .94:
                return min(number, 6)
        return min(len(clusters), 6)

    result = []
    for item in found:
        text, depth = strip_numbering(item["text"])
        if _letters(text) < 3:
            continue
        numbered = depth if re.match(r"\d", item["text"]) else 0
        result.append({"text": text[:200], "level": max(1, min(numbered or level(item), 6)),
                       "page": item["page"], "source": "font"})
    return result


GENERIC_TITLE = re.compile(r"(?i)(?:pref[aá]cio|preface|foreword|pr[oó]logo|contents?|table of contents|sum[aá]rio|"
                           r"introdu[cç][aã]o|introduction|(?:frequently used )?notation|nota[cç][aã]o|lista de s[ií]mbolos|"
                           r"list of (?:symbols|figures|tables)|acknowledg(?:e)?ments?|agradecimentos|dedicat[oó]ria|"
                           r"dedication|abstract|resumo|index|[ií]ndice|copyright|ep[ií]grafe|apresenta[cç][aã]o|"
                           r"(?:\w+ )?edition|\d+\S* edi[cç][aã]o|(?:primeira|segunda|terceira) edi[cç][aã]o|edi[cç][aã]o|"
                           r"volume [\divx]+|vol\. [\divx]+)[\s.:]*")
GENERIC_PREFIX = re.compile(r"(?i)(?:to the (?:student|reader|instructor)|ao (?:aluno|leitor|professor)|about the authors?|"
                            r"dedicated to|dedicado|series\b|cole[cç][aã]o\b)")


def _title_block_score(block: dict[str, Any], body: float, page_height: float) -> float:
    text = block["text"]
    letters = _letters(text)
    if letters < 3 or len(text) > 240 or block["lines"] > 6:
        return 0.0
    if GENERIC_TITLE.fullmatch(text.strip()) or GENERIC_PREFIX.match(text) or re.search(r"\b(?:i\.e|e\.g|cf)\.|;", text):
        return 0.0
    if text[:1].islower() and len(text.split()) > 3:
        return 0.0
    if NOISE_LINE.search(text) or LABEL_LINE.match(text) or re.match(r"(?i)arxiv|preprint|vol\.|volume \d+, |journal", text):
        return 0.0
    if letters < .55 * len(text.replace(" ", "")):
        return 0.0
    ratio = block["size"] / body if body else 1.0
    score = ratio
    if MATH_CHARS.search(text) or re.search(r"[\[\]{}=<>|]", text) or block.get("math", 0) > .2:
        score *= .4
    if block["bold"]:
        score *= 1.15
    if page_height:
        score *= 1 + .5 * max(0.0, 1 - block["top"] / page_height)
    if INSTITUTION.search(text):
        score *= .55
    if len(text.split()) < 2:
        score *= .8
    if re.search(r"(?i)\b(?:prof(?:essor)?\.?|dr\.|autor|author|por|by)\b", text):
        score *= .8
    return score


def prose_vocabulary(text: str) -> Counter:
    """Words of running prose after the opening pages (names in headers excluded)."""
    vocabulary: Counter = Counter()
    for line in text[8000:400000].splitlines():
        words = re.findall(r"[^\W\d_]{4,}", fold(line))
        if len(line.split()) >= 8:
            vocabulary.update(set(words))
    return vocabulary


def font_title(lines: list[dict[str, Any]], body: float, vocabulary: Counter | None = None) -> tuple[str, int]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        by_page.setdefault(line["page"], []).append(line)
    vocabulary = vocabulary or Counter()
    choices = []
    for number in sorted(by_page)[:6]:
        page_lines = by_page[number]
        words = sum(len(line["text"].split()) for line in page_lines)
        if words < 3:
            continue
        blocks: list[dict[str, Any]] = []
        for line in page_lines:
            segment = max(line["segments"], key=lambda part: (part["size"], part["chars"]))
            if len(line["segments"]) > 2:
                continue
            previous = blocks[-1] if blocks else None
            if (previous and abs(previous["size"] - segment["size"]) <= .05 * max(segment["size"], 1)
                    and previous["bold"] == segment["bold"]
                    and 0 < segment["top"] - previous["last_top"] <= 1.8 * max(segment["height"], 1)):
                previous["text"] += " " + line["text"]
                previous["lines"] += 1
                previous["last_top"] = segment["top"]
                continue
            blocks.append({"text": line["text"], "size": segment["size"], "bold": segment["bold"],
                           "top": segment["top"], "last_top": segment["top"], "lines": 1,
                           "math": max(part["math"] for part in line["segments"])})
        page_body = body or body_size(page_lines)
        height = page_lines[0]["page_height"]
        scored = []
        for block in blocks:
            score = _title_block_score(block, page_body, height)
            tokens = set(re.findall(r"[^\W\d_]{4,}", fold(block["text"])))
            if score and tokens and sum(vocabulary.values()) >= 200:
                # Title words recur in the body; author names and imprints do not.
                support = sum(vocabulary[token] >= 2 for token in tokens) / len(tokens)
                score *= .6 + .6 * support
            scored.append((score, -block["top"], block))
        scored = [item for item in scored if item[0] > 0]
        if not scored:
            continue
        best = max(scored, key=lambda item: (item[0], item[1]))
        # Later pages (title page after a cover or notation page) need more emphasis.
        choices.append((best[0] * (.88 ** len(choices)), number, best[2]["text"]))
    choices = [choice for choice in choices if choice[0] >= 1.0]
    if not choices:
        return "", 0
    score, number, text = max(choices, key=lambda choice: (choice[0], -choice[1]))
    return clean_line(text).strip(" .:-–—"), number


def metadata_title(info: dict[str, str]) -> str:
    title = clean_line(info.get("Title", "") or "")
    if not title:
        return ""
    # Exporters sometimes store the author's local source path as the title.
    if re.match(r"(?i)(?:[a-z]:[\\/]|[/~\\]|(?:file|https?)://)", title):
        return ""
    title = re.sub(r"(?i)^microsoft (?:word|powerpoint|excel)\s*-\s*", "", title)
    title = FILE_EXTENSION.sub("", title).strip()
    if "_" in title and " " not in title:
        title = re.sub(r"_+", " ", title).strip()
    if JUNK_TITLE.match(title) or _letters(title) < 3:
        return ""
    if re.fullmatch(r"(?i)[a-f0-9\-]{16,}|[\w\-]*\d{5,}[\w\-]*", title):
        return ""
    creator = fold(info.get("Creator", "") + " " + info.get("Producer", ""))
    if fold(title) and fold(title) in creator:
        return ""
    return title


def _content_tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[^\W\d_]{3,}", fold(text))}


# --------------------------------------------------------- contents (TOC)

TOC_COMPACT = re.compile(r"(?:sumario|conteudos?|indice(?:geral)?|contents?|tableofcontents|inhalt(?:sverzeichnis)?|"
                         r"tabledesmatieres|sommaire|contenido|indicegeneral|indicedeconteudos?)")
PAGE_NUMBER = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,7}|[IVXLCDM]{1,7})$")
LEADERS = re.compile(r"[.·…_](?:\s*[.·…_]){2,}")
TRAILING_PAGE = re.compile(r"(?:^|(?<=[\s.·…_]))(\d{1,4}|[ivxlcdm]{1,7})\s*$", re.I)


STRUCTURAL_WORD = re.compile(r"(?i)(?:cap[ií]tulo|chapter|parte|part|se[cç][aã]o|section|aula|lecture|lista|unidade|"
                             r"unit|volume|vol\.?|quest[aã]o|question|exerc[ií]cio|exercise|problema|problem|figura|"
                             r"figure|tabela|table|p[aá]gina|page|p\.|n[º°o]\.?)")


def split_entry(text: str, leaders_required: bool) -> tuple[str, str] | None:
    """Split "1.2 Title ....... 14" into label and page label, in linear time."""
    if len(text) > 240:
        return None
    match = TRAILING_PAGE.search(text)
    if not match:
        return None
    head = text[:match.start(1)]
    label = head.rstrip(" .·…_\t")
    tail = head[len(label):]
    leaders = sum(tail.count(char) for char in ".·_") + 3 * tail.count("…")
    if leaders_required and leaders < 3:
        return None
    if STRUCTURAL_WORD.fullmatch(label.strip()):
        return None
    if not leaders and not head.endswith(" "):
        return None
    return label, match.group(1)


NUMBERING = re.compile(r"^(?:(?P<word>cap[ií]tulo|chapter|parte|part|se[cç][aã]o|section|unidade|unit|"
                       r"m[oó]dulo|module|lecture|aula|appendix|ap[eê]ndice|anexo|li[cç][aã]o|lesson)\s+)?"
                       r"(?P<num>(?:\d+(?:\.\d+)*|[IVXLC]+|[A-Z])(?![\w]))?\s*[.\-–—:)]?\s*", re.I)


def _toc_entry(line: dict[str, Any]) -> tuple[str, str] | None:
    segments = line["segments"]
    text = line["text"]
    if len(segments) >= 2 and PAGE_NUMBER.match(segments[-1]["text"].strip(" .")):
        label = clean_line(" ".join(part["text"] for part in segments[:-1]))
        page = segments[-1]["text"].strip(" .")
    else:
        split = split_entry(text, leaders_required=False)
        if not split:
            return None
        label, page = split
    label = LEADERS.sub(" ", label).strip(" .·…_-–—")
    if len(label) > 160 or not re.search(r"[^\W\d_]{3,}", label):
        return None
    if _letters(label) < .5 * len(label.replace(" ", "")) or len(re.findall(r"\d", label)) > 8:
        return None
    return clean_line(label), page


def detect_toc(lines: list[dict[str, Any]], front_limit: int) -> tuple[list[dict[str, Any]], set[int]]:
    by_page: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        if line["page"] <= front_limit:
            by_page.setdefault(line["page"], []).append(line)
    entries: list[dict[str, Any]] = []
    toc_pages: set[int] = set()
    active = False
    for number in sorted(by_page):
        page_lines = by_page[number]
        heading = any(TOC_COMPACT.fullmatch(re.sub(r"[\W\d_]+", "", fold(line["text"])))
                      for line in page_lines[:8] if len(line["text"]) <= 40)
        parsed = []
        for line in page_lines:
            entry = _toc_entry(line)
            if entry:
                parsed.append((entry, line))
        textual = [line for line in page_lines if _letters(line["text"]) >= 2]
        ratio = len(parsed) / max(1, len(textual))
        # Without a "Contents" heading, demand dot leaders (tables also end in numbers).
        leadered = sum(bool(LEADERS.search(line["text"])) for _, line in parsed)
        dense = len(parsed) >= 6 and ratio >= .35 and leadered >= .5 * len(parsed)
        if heading and len(parsed) >= 3 or dense or active and len(parsed) >= 4 and ratio >= .3:
            toc_pages.add(number)
            active = True
            for (label, page), line in parsed:
                entries.append({"text": label, "page_label": page, "left": line["segments"][0]["left"],
                                "pdf_page": number})
        elif active:
            active = False
    # A table of contents lists non-decreasing page numbers.
    arabic = [int(entry["page_label"]) for entry in entries if entry["page_label"].isdigit()]
    if len(arabic) >= 4:
        ordered = sum(b >= a for a, b in zip(arabic, arabic[1:])) / (len(arabic) - 1)
        if ordered < .6:
            return [], set()
    elif len(entries) < 3:
        return [], set()
    return entries, toc_pages


def toc_from_text(text: str) -> list[dict[str, Any]]:
    entries = []
    for line in text[:200000].splitlines():
        split = split_entry(clean_line(line), leaders_required=True)
        if split and _letters(split[0]) >= 3:
            label = LEADERS.sub(" ", split[0]).strip(" .·…_-–—")
            entries.append({"text": clean_line(label), "page_label": split[1], "left": 0.0,
                            "pdf_page": 0})
    return entries if len(entries) >= 5 else []


def strip_numbering(text: str) -> tuple[str, int]:
    match = NUMBERING.match(text)
    depth = 0
    rest = text
    if match and (match.group("word") or match.group("num")):
        number = match.group("num") or ""
        depth = number.count(".") + 1 if re.match(r"\d", number) else 1 if number else 0
        if match.group("word") and fold(match.group("word")) in ("capitulo", "chapter", "parte", "part", "unidade", "unit"):
            depth = 1
        candidate = text[match.end():].strip()
        if _letters(candidate) >= 3:
            rest = candidate
    return rest, depth


def toc_headings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lefts = sorted({round(entry["left"] / 6) for entry in entries})
    result = []
    for entry in entries:
        text, depth = strip_numbering(entry["text"])
        if not depth:
            depth = lefts.index(round(entry["left"] / 6)) + 1 if len(lefts) > 1 and entry["left"] else 1
        page = int(entry["page_label"]) if entry["page_label"].isdigit() else 0
        result.append({"text": text, "level": max(1, min(depth, 6)), "page": page, "source": "toc"})
    return result


BOILERPLATE = re.compile(r"(?i)^(?:capa|cover|front ?cover|back ?cover|contracapa|folha de rosto|rosto|title ?page|"
                         r"half ?title|copyright|cr[eé]ditos|credits|ficha catalogr[aá]fica|sum[aá]rio(?: geral)?|contents|"
                         r"front ?matter|back ?matter|.*\.(?:pdf|docx?|tex|indd|djvu)|"
                         r"table of contents|[ií]ndice(?: remissivo)?|index|dedica(?:t[oó]ria|tion)|"
                         r"blank page|p[aá]gina em branco|about the authors?|sobre os? autor(?:es)?|"
                         r"bookmarks?|(?:page|p[aá]gina|seite|pg|p)\.?\s?\d+|[\divxlc]+(?:\.\d+)*|untitled|sem t[ií]tulo)$")


def outline_headings(outline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in outline:
        text, _ = strip_numbering(item["text"])
        if BOILERPLATE.match(item["text"].strip(" .")) or _letters(text) < 2:
            continue
        result.append({"text": text[:200], "level": max(1, min(item["level"], 6)), "page": item["page"],
                       "source": "outline"})
    return result


def limit_headings(headings: list[dict[str, Any]], limit: int = MAX_HEADINGS) -> list[dict[str, Any]]:
    """Keep the shallowest levels that fit, in document order."""
    if len(headings) <= limit:
        return headings
    levels = sorted({item["level"] for item in headings})
    chosen: list[dict[str, Any]] = []
    allowed: set[int] = set()
    for level in levels:
        candidate = [item for item in headings if item["level"] in allowed | {level}]
        if len(candidate) > limit:
            if not chosen:
                chosen = candidate[:limit]
            else:
                extra = [item for item in headings if item["level"] == level]
                keep = {id(item) for item in chosen} | {id(item) for item in extra[:limit - len(chosen)]}
                chosen = [item for item in headings if id(item) in keep]
            break
        allowed.add(level)
        chosen = candidate
    return chosen[:limit]


# ------------------------------------------------------- language & stats

STOPWORDS = {
    "pt": "de que não uma para com os das dos em são pelo pela mais como também está isso esse essa entre sobre seja "
          "então ou ao aos às nas nos pode podemos temos onde quando".split(),
    "en": "the and of to is that for with this are as be by which we on from it an can not if then let "
          "where there these when such".split(),
    "es": "el los las del que y en una con por para es se no como más pero sus está son este esta entre "
          "también donde cuando puede tenemos".split(),
    "fr": "le les des du et est une que dans pour pas sur avec par ce qui sont nous cette mais ou au "
          "aux être peut donc".split(),
    "de": "der die das und ist nicht ein eine mit von zu den dem des sich auf für im wir es auch "
          "werden kann wenn dann oder".split(),
    "it": "il di che è la per un una non con del della sono gli le come anche questo questa nel alla "
          "delle dei può quindi".split(),
}
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(text: str) -> tuple[str, dict[str, float]]:
    words = [word.casefold() for word in WORD.findall(text[:300000])]
    if len(words) < 30:
        return "", {}
    counts = Counter(words)
    scores = {language: sum(counts[word] for word in set(stopwords)) / len(words)
              for language, stopwords in STOPWORDS.items()}
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] < .06 or ranked[0] < 1.2 * ranked[1]:
        return "", scores
    return best, scores


MATH_CHARS = re.compile(r"[\u0370-\u03ff\u2200-\u22ff\u2190-\u21ff\u27c0-\u27ef\u2980-\u2aff\U0001d400-\U0001d7ff"
                        r"∑∫∂∇√∞≤≥≠≈±×÷]")
LATEX = re.compile(r"\\(?:frac|int|sum|alpha|beta|gamma|delta|lambda|sigma|mathbb|mathcal|partial|infty|leq|geq|sqrt)\b")


def math_density(text: str) -> float:
    sample = text[:400000]
    letters = sum(char.isalpha() for char in sample) or 1
    symbols = len(MATH_CHARS.findall(sample)) + 3 * len(LATEX.findall(sample)) + sample.count("=")
    return round(min(1.0, symbols / letters * 10), 4)


# ---------------------------------------------------------------- kinds

def _count(pattern: str, text: str, flags: int = re.I | re.M) -> int:
    return len(re.findall(pattern, text, flags))


def classify_kind(features: dict[str, Any]) -> tuple[str, dict[str, float], list[str]]:
    n = features["pages"]
    wpp = features["words_per_page"]
    front = features["front_text"]
    opening = features["opening_text"]
    full = features["text"]
    heads = features["heading_text"]
    title = features["title"]
    # Type words count only in the title or short opening lines, not in prose.
    cues = title + "\n" + features["cue_text"]
    landscape = features["landscape_ratio"]
    scores = {kind: 0.0 for kind in KINDS}
    evidence: dict[str, list[str]] = {kind: [] for kind in KINDS}

    def add(kind: str, value: float, message: str = "") -> None:
        scores[kind] += value
        if message and value > 0:
            evidence[kind].append(message)

    isbn = bool(re.search(r"\bISBN(?:-1[03])?[:\s]*[\dXx][\d\-\sXx]{8,}", front))
    publisher = bool(re.search(r"(?i)all rights reserved|todos os direitos reservados|dados internacionais de cataloga|"
                               r"library of congress|printed in|impresso no brasil|editora\b|springer|cambridge university press|"
                               r"oxford university press|wiley|elsevier|mcgraw|pearson|addison|birkh[aä]user|"
                               r"princeton university press|\bams\b|american mathematical society|sociedade brasileira de matem",
                               front))
    chapters = _count(r"^\s*(?:cap[ií]tulo|chapter|chapitre|kapitel|cap\.)\s+[\dIVXLC]+\b", heads + "\n" + front)
    chapters += _count(r"^\s*(?:cap[ií]tulo|chapter)\s+[\dIVXLC]+", full[:500000]) // 2
    preface = bool(re.search(r"(?im)^\s*(?:pref[aá]cio|preface|pr[oó]logo|foreword|avant-propos|vorwort)\b", heads + "\n" + front))
    abstract = bool(re.search(r"(?im)^\s*(?:abstract|resumo|r[ée]sum[ée]|resumen|zusammenfassung)\b|\babstract[.:]",
                              features["first_pages_text"]))
    references = bool(re.search(r"(?im)^\s*(?:\d+\.?\s*)?(?:references|refer[êe]ncias(?: bibliogr[aá]ficas)?|bibliography|"
                                r"bibliografia|referencias|r[ée]f[ée]rences|literatur(?:verzeichnis)?)\s*$",
                                heads + "\n" + full))
    keywords = bool(re.search(r"(?i)\b(?:keywords|key words|palavras-chave|palabras clave|mots-cl[ée]s)\b", features["first_pages_text"]))
    journal = _count(r"\bdoi\b|arxiv|\bjournal\b|\bvol\.\s*\d|received\b|accepted\b|published online|proceedings|"
                     r"conference|issn|preprint|revista\b|anais\b|\bpp\.\s*\d", features["first_pages_text"])
    emails = _count(r"[\w.+-]+@[\w-]+\.[\w.]+", features["first_pages_text"])
    thesis = re.search(r"(?i)disserta[cç][aã]o\s+(?:de mestrado\s+)?apresentada|tese\s+(?:de doutorado\s+)?apresentada|"
                       r"(?:thesis|dissertation)\s+(?:submitted|presented)|in partial fulfil+ment|submitted in partial|"
                       r"requisitos? parcia(?:l|is)|para (?:a )?obten[cç][aã]o do (?:grau|t[ií]tulo)|"
                       r"trabalho de conclus[aã]o de curso|monografia apresentada|banca examinadora",
                       features["first_pages_text"])
    thesis_mention = re.search(r"(?i)\bdisserta[cç][aã]o de mestrado\b|\btese de doutorado\b|ph\.?\s?d\.? thesis|"
                               r"master'?s thesis|bachelor'?s thesis|doctor of philosophy|master of science|"
                               r"programa de p[oó]s-gradua[cç][aã]o|\bdisserta[cç][aã]o\b|\btese\b|\bthesis\b", cues)
    advisor = bool(re.search(r"(?im)^\s*(?:orientador(?:a)?|co-?orientador(?:a)?|advisor|supervisor|adviser)\s*[:\-]?", front))
    acknowledgments = bool(re.search(r"(?im)^\s*(?:agradecimentos|acknowledg(?:e)?ments|dedicat[oó]ria)\b", heads + "\n" + front))
    notes = re.search(r"(?i)notas de aulas?|lecture notes|apostila|course notes|class notes|notas do curso|notas de curso|"
                      r"\blecture\s+\d+|\baula\s+\d+|\bhandout\b|notas de estudo|study notes|material did[aá]tico|"
                      r"\bnotes\b|\bnotas\b|anota[cç][oõ]es|material de apoio|resumo de aula", cues)
    notes_prose = re.search(r"(?i)\b(?:these|this) (?:lecture )?(?:notes|handout)\b|\bestas notas\b|\bneste texto\b|"
                            r"\bthis course\b|\bneste curso\b|\bthis class\b", opening)
    course = re.search(r"\b[A-Z]{2,5}\s?-?\d{3,4}[A-Z]?\b|\b\d{1,2}\.\d{2,3}[A-Z]?\b|(?i:disciplina|semestre|semester|"
                       r"fall 20\d\d|spring 20\d\d|\bprof(?:essor|\.)\s|\bturma\b|instructor)", opening)
    exercise_title = re.search(r"(?i)lista\s+(?:de\s+)?exerc[ií]cios|\blista\s*\d+|\blista\b|exerc[ií]cios|problem\s+set|"
                               r"exercise\s+sheet|problem\s+sheet|homework|assignment|worksheet|tarefa|list of exercises|"
                               r"\bexercises\b|\bproblemas\b|\bpset\b|\bhw\s*\d+", cues)
    numbered = _count(r"^\s*(?:\d{1,2}\s*[.)]|\(\s*[a-h]\s*\)|[a-h]\))\s+\S", full[:200000])
    exercise_items = _count(r"^\s*(?:exerc[ií]cio|exercise|problem|problema)\s+\d", full[:200000])
    exam = re.search(r"(?i)\bprova\b|\bexame\b|avalia[cç][aã]o|\bP[1-4]\b|midterm|final exam|\bexam(?:ination)?\b|\bquiz\b|"
                     r"\bteste\s+\d|\bsimulado\b|\bvestibular\b|\bgabarito\b", cues)
    questions = _count(r"^\s*(?:quest[aã]o|question)\s*\d", full[:200000])
    points = _count(r"\(\s*\d+(?:[.,]\d+)?\s*(?:pontos?|pts?\.?|points?|valor)\s*\)|valor\s*:\s*\d|\d+(?:[.,]\d+)?\s+pontos\b",
                    full[:200000])
    student = _count(r"(?im)^\s*(?:nome|name|aluno\(?a?\)?|estudante|student|ra|matr[ií]cula|turma|data)\s*[:_]", opening)
    blanks = _count(r"_{5,}|\.{10,}|\[\s\]|☐|□", full[:200000], 0)
    form_word = re.search(r"(?i)formul[aá]rio|\bficha\b|requerimento|inscri[cç][aã]o|cadastro|application form|\bform\b|"
                          r"declara[cç][aã]o|autoriza[cç][aã]o|termo de", cues)
    personal = _count(r"(?i)\bCPF\b|\bRG\b|assinatura|signature|data de nascimento|date of birth|endere[cç]o|address|"
                      r"telefone|phone|e-mail\s*:", full[:100000], re.I)
    manual_word = re.search(r"(?i)\bmanual\b|user'?s? guide|guia do usu[aá]rio|guia de (?:instala|refer|uso|in[ií]cio)|"
                            r"installation guide|reference guide|getting started|primeiros passos|instru[cç][oõ]es de uso|"
                            r"owner'?s manual|quick start|handbook|documenta[cç][aã]o t[eé]cnica", cues)
    instructions = _count(r"(?i)\b(?:clique|click|selecione|select|pressione|press|instale|install|configure|"
                          r"menu|bot[aã]o|button|tela|screen|janela|window|aba|tab)\b", full[:200000], re.I)
    software = fold(features["creator"])

    # Book
    if n >= 150:
        add("livro", .35, f"{n} páginas")
    elif n >= 80:
        add("livro", .2, f"{n} páginas")
    elif n >= 40:
        add("livro", .05)
    if isbn:
        add("livro", .3, "ISBN nas primeiras páginas")
    if publisher:
        add("livro", .1, "editora ou ficha catalográfica")
    if chapters >= 2:
        add("livro", .15, f"{chapters} capítulos identificados")
    if preface:
        add("livro", .1, "prefácio")
    if features["toc_entries"] >= 10 or features["outline_items"] >= 20:
        add("livro", .1, "sumário extenso")
    if n >= 100 and features["top_level_sections"] >= 4:
        add("livro", .1, f"{features['top_level_sections']} partes ou capítulos no sumário")
    # Lecture notes / course handout
    structured = features["toc_entries"] >= 5 or features["outline_items"] >= 5 or features["heading_count"] >= 5
    if notes:
        add("apostila", .45, f"“{clean_line(notes.group(0))}” no título ou na abertura")
    elif notes_prose:
        add("apostila", .2, f"“{clean_line(notes_prose.group(0))}” no texto de abertura")
    if structured and not isbn and not publisher and 8 <= n <= 400:
        add("apostila", .15, "texto estruturado sem ISBN nem editora")
    if 8 <= n <= 400:
        add("apostila", .12)
    if course:
        add("apostila", .1, "disciplina ou curso na abertura")
    if (chapters >= 1 or features["heading_count"] >= 5) and not isbn:
        add("apostila", .1)
    # Exercise list
    if exercise_title:
        add("lista_exercicios", .45, f"“{clean_line(exercise_title.group(0))}” na abertura")
    if (numbered >= 5 or exercise_items >= 3) and n <= 30:
        add("lista_exercicios", .25, f"{max(numbered, exercise_items)} itens numerados")
    if n <= 15:
        add("lista_exercicios", .1)
    # Exam
    if exam:
        add("prova", .45, f"“{clean_line(exam.group(0))}” na abertura")
    if questions >= 2 or points >= 2:
        add("prova", .25, f"{max(questions, points)} questões ou pontuações")
    if student >= 1:
        add("prova", .15, "campos para identificação do aluno")
    if n <= 12:
        add("prova", .1)
    # Article
    if abstract:
        add("artigo", .35, "resumo/abstract no início")
    if references:
        add("artigo", .25, "seção de referências")
    if keywords:
        add("artigo", .1, "palavras-chave")
    if journal:
        add("artigo", min(.15, .05 * journal), "marcas de periódico, DOI ou arXiv")
    if 3 <= n <= 50:
        add("artigo", .1)
    if emails and n <= 60:
        add("artigo", .05)
    # Thesis
    if thesis:
        add("tese", .55, f"“{clean_line(thesis.group(0))}”")
    elif thesis_mention:
        add("tese", .3, f"“{clean_line(thesis_mention.group(0))}” no título ou na abertura")
    if advisor:
        add("tese", .15, "orientador indicado")
    if n >= 40:
        add("tese", .1)
    if acknowledgments:
        add("tese", .1, "agradecimentos")
    # Slides
    if landscape >= .8:
        add("slides", .45, f"{landscape:.0%} das páginas em paisagem")
        if wpp < 140:
            add("slides", .2, f"{wpp:.0f} palavras por página")
    if re.search(r"powerpoint|keynote|impress|beamer|google slides|prezi", software):
        add("slides", .2, "gerado por software de apresentação")
    if features["slide_aspect"]:
        add("slides", .1)
    # Form
    if features["acroform"]:
        add("formulario", .4, "campos de formulário PDF")
    if blanks >= 5:
        add("formulario", .3, f"{blanks} campos para preencher")
    if form_word:
        add("formulario", .2, f"“{clean_line(form_word.group(0))}” na abertura")
    if personal >= 2:
        add("formulario", .1, "dados pessoais solicitados")
    if n <= 10:
        add("formulario", .05)
    # Manual
    if manual_word:
        add("manual", .45, f"“{clean_line(manual_word.group(0))}” na abertura")
    if instructions >= max(8, n * .5):
        add("manual", .2, "instruções de uso frequentes")
    if n >= 10:
        add("manual", .05)

    # Mutual exclusions and size priors.
    if n > 60:
        for kind in ("lista_exercicios", "prova", "formulario"):
            scores[kind] -= .35
        scores["artigo"] -= .25
    if n > 30:
        scores["prova"] -= .2
    if isbn:
        scores["apostila"] -= .2
        scores["artigo"] -= .2
    elif not publisher:
        scores["livro"] -= .1
    if landscape >= .8:
        scores["livro"] -= .3
        scores["artigo"] -= .2
    if wpp > 350:
        scores["slides"] -= .2
    if thesis:
        scores["livro"] -= .15
        scores["artigo"] -= .1
    if n <= 4 and not abstract:
        scores["livro"] -= .2
    best = max((kind for kind in KINDS if kind != "documento"), key=lambda kind: scores[kind])
    rounded = {kind: round(max(0.0, min(1.0, value)), 3) for kind, value in scores.items()}
    rounded["documento"] = round(max(0.0, 1 - rounded[best]) * .6, 3)
    if rounded[best] < KIND_THRESHOLD:
        reasons = ["nenhum padrão estrutural forte"] + evidence[best][:1]
        return "documento", rounded, reasons
    return best, rounded, evidence[best][:5]


# ------------------------------------------------------------ main entry

def page_windows(pages: int) -> list[tuple[int, int]]:
    if pages <= 0:
        return [(1, FRONT_PAGES)]
    windows = [(1, min(pages, FRONT_PAGES))]
    if pages > FRONT_PAGES + 2 * WINDOW_PAGES:
        for fraction in (.30, .55, .80):
            start = max(FRONT_PAGES + 1, min(pages - WINDOW_PAGES + 1, round(pages * fraction)))
            window = (start, min(pages, start + WINDOW_PAGES - 1))
            if window[0] > windows[-1][1]:
                windows.append(window)
    return windows


def _read_xml(path: str, first: int, last: int, runner: Runner, timeout: float) -> tuple[str, str]:
    with tempfile.TemporaryFile() as output:
        code, error = runner(["pdftohtml", "-xml", "-i", "-q", "-hidden", "-nodrm", "-enc", "UTF-8", "-f", str(first),
                              "-l", str(last), "-stdout", path], timeout=timeout, output=output, limited=True)
        output.seek(0)
        data = output.read(MAX_XML_BYTES)
    xml = data.decode("utf-8", "replace")
    if code and "<page" not in xml:
        return "", error or "pdftohtml não conseguiu ler o PDF"
    return xml, ("leitura parcial da estrutura" if code else "")


def _page_size(info: dict[str, str]) -> tuple[float, float]:
    match = re.search(r"([\d.]+)\s*x\s*([\d.]+)", info.get("Page size", ""))
    return (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)


def read_structure(path: str, pages: int, text: str, info: dict, *, runner: Runner | None = None,
                   budget: float = BUDGET_SECONDS,
                   passthrough: tuple[type[BaseException], ...] = ()) -> dict[str, Any]:
    """Return the structure contract dict. Never raises for bad PDFs.

    ``runner`` follows indexer.run_process; ``passthrough`` lists exceptions
    (such as the indexer's cancellation) that must propagate.
    """
    try:
        return _read_structure(path, int(pages or 0), text or "", info or {}, runner or default_runner, budget)
    except passthrough:
        raise
    except Exception as error:  # noqa: BLE001 - malformed PDFs must not stop a scan
        return empty_result("unavailable", f"Estrutura indisponível: {error}"[:300])


def _read_structure(path: str, pages: int, text: str, info: dict, runner: Runner, budget: float) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + budget
    warnings: list[str] = []
    transient = False
    parsed_pages: dict[int, dict[str, Any]] = {}
    outline: list[dict[str, Any]] = []
    for number, (first, last) in enumerate(page_windows(pages)):
        remaining = deadline - time.monotonic()
        if remaining < .15:
            transient = True
            warnings.append("orçamento de tempo da estrutura esgotado")
            break
        try:
            xml, error = _read_xml(path, first, last, runner, remaining if number else max(.3, remaining * .75))
        except TimeoutError as error:
            transient = True
            warnings.append(str(error))
            break
        except OSError as error:
            warnings.append(str(error))
            break
        if error:
            warnings.append(error)
            if not xml:
                break
        parsed = parse_xml(xml)
        parsed_pages.update(parsed["pages"])
        if not outline:
            outline = parsed["outline"]
    lines: list[dict[str, Any]] = []
    for number in sorted(parsed_pages):
        lines.extend(build_lines(parsed_pages[number]))
    has_text = bool(re.search(r"[^\W\d_]", text))
    if not lines and not outline and not has_text:
        result = empty_result("unavailable", "; ".join(warnings) or "PDF sem texto ou estrutura legível")
        title = metadata_title(info)
        if title:
            result.update(status="partial", title=title, title_source="metadata")
        result["stats"] = {"pages": pages}
        result["transient"] = transient
        return result

    body = body_size(lines)
    # Title: useful metadata corroborated by the opening text, else typography.
    typographic, title_page = font_title(lines, body, prose_vocabulary(text))
    meta = metadata_title(info)
    front_lines = [line["text"] for line in lines if line["page"] <= 3]
    opening_source = "\n".join(front_lines) or text[:6000]
    opening_tokens = _content_tokens(opening_source + "\n" + text[:6000])
    title, title_source = "", ""
    if meta:
        tokens = _content_tokens(meta)
        support = len(tokens & opening_tokens) / max(1, len(tokens))
        if support >= .34 or not typographic or not opening_tokens:
            title, title_source = meta, "metadata"
    if not title and typographic:
        title, title_source = typographic, "font"
    if not title and meta:
        title, title_source = meta, "metadata"

    entries, toc_pages = detect_toc(lines, FRONT_PAGES)
    if not entries and not lines:
        entries = toc_from_text(text[:120000])
    toc = [entry["text"] for entry in entries][:MAX_TOC]

    from_outline = outline_headings(outline)
    from_toc = toc_headings(entries) if entries else []
    skip = set(toc_pages)
    if title_page:
        skip.add(title_page)
    from_font = font_headings(lines, body, skip, title) if lines else []
    if len(from_outline) >= 3:
        headings = limit_headings(from_outline)
    elif len(from_toc) >= 3:
        headings = limit_headings(from_toc)
    else:
        headings = limit_headings(from_outline + from_toc + from_font)
    if len(headings) < 15:
        seen = {fold(item["text"]) for item in headings}
        extra = [item for item in from_font if fold(item["text"]) not in seen]
        headings = headings + extra[:MAX_HEADINGS - len(headings)]
    headings = headings[:MAX_HEADINGS]

    # Statistics.
    words = len(WORD.findall(text))
    sampled_pages = min(pages, 60) if pages else max(1, text.count("\f"))
    wpp = words / max(1, sampled_pages)
    sizes = [(page["width"], page["height"]) for page in parsed_pages.values() if page["width"] and page["height"]]
    if not sizes:
        width, height = _page_size(info)
        sizes = [(width, height)] if width and height else []
    landscape = sum(width > height * 1.05 for width, height in sizes) / len(sizes) if sizes else 0.0
    width, height = _page_size(info)
    aspect = width / height if width and height else 0.0
    slide_aspect = bool(aspect and (abs(aspect - 4 / 3) < .06 or abs(aspect - 16 / 9) < .08 or abs(aspect - 16 / 10) < .06))
    language, _ = detect_language(text)
    math_chars = sum(segment["chars"] * segment["math"] for line in lines for segment in line["segments"])
    all_chars = sum(segment["chars"] for line in lines for segment in line["segments"]) or 1
    first_pages_text = "\n".join(line["text"] for line in lines if line["page"] <= 3) or text[:10000]
    front_text = "\n".join(line["text"] for line in lines if line["page"] <= FRONT_PAGES) or text[:60000]
    opening_text = "\n".join(line["text"] for line in lines if line["page"] <= 2)[:3000] or text[:3000]
    heading_text = "\n".join(item["text"] for item in outline[:400]) + "\n" + "\n".join(
        item["text"] for item in from_font[:200]) + "\n" + "\n".join(toc)
    features = {
        "pages": pages, "words_per_page": wpp, "front_text": front_text, "opening_text": opening_text,
        "cue_text": "\n".join(line for line in opening_text.splitlines()
                              if 0 < len(line.split()) <= 10 and not line.rstrip().endswith((".", ",", ";"))
                              and not line.lstrip()[:1].islower()),
        "first_pages_text": first_pages_text, "text": text, "heading_text": heading_text,
        "title": title, "landscape_ratio": landscape, "slide_aspect": slide_aspect,
        "toc_entries": len(entries), "outline_items": len(outline), "heading_count": len(headings),
        "top_level_sections": sum(item["level"] == 1 for item in headings),
        "acroform": info.get("Form", "none").strip().lower() not in ("", "none"),
        "creator": info.get("Creator", "") + " " + info.get("Producer", ""),
    }
    kind, scores, evidence = classify_kind(features)
    stats = {
        "pages": pages, "words_per_page": round(wpp, 1), "math_density": math_density(text),
        "math_font_ratio": round(math_chars / all_chars, 4), "landscape_ratio": round(landscape, 3),
        "body_font_size": round(body / 1.5, 1) if body else 0.0, "pages_read": len(parsed_pages),
        "outline_items": len(outline), "toc_entries": len(entries),
        "font_headings": len(from_font), "seconds": round(time.monotonic() - started, 3),
    }
    status = "ready" if lines and not warnings else "partial"
    result = {
        "version": STRUCTURE_VERSION, "status": status, "kind": kind, "kind_label": KIND_LABELS[kind],
        "kind_scores": scores, "kind_evidence": evidence, "title": title[:300], "title_source": title_source,
        "headings": headings, "toc": toc, "language": language, "stats": stats, "transient": transient,
    }
    if warnings:
        result["warning"] = "; ".join(dict.fromkeys(warnings))[:500]
    return result
