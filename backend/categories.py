"""Atlas Categorias: open-set categories from words and document structure.

Pure functions used by ``semantic_catalog``. Everything runs offline with
NumPy: no training, no API, no generative naming. Thresholds are heuristic and
were tuned on one personal library; they are not calibrated probabilities.

Representation
    MiniLM sentence vectors are strongly anisotropic: every academic text has a
    cosine of about 0.75 with every other one, so a global 0.68 threshold accepts
    almost anything. Each component (body, structure, keyphrases) is centered by
    the collection mean before cosine, then combined with weights that favor
    the title/outline/table of contents over the body.

Decision
    A category accepts a document when the average similarity to its members
    reaches a threshold derived from the category's own dispersion (shrunk
    towards a collection prior for small groups). Named mathematical shelves
    stay anchored when the lexicon and the title agree.

Induction
    Documents not explained by any shelf are grouped with average linkage
    (nearest-neighbour chain, O(n²) time and memory). Groups are named with a
    class-based TF-IDF over n-grams that occur in the titles, headings and
    keyphrases of several members.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re

try:
    from .topics import CONNECTORS, GENERIC, STOP, TOKEN, fold
except ImportError:
    from topics import CONNECTORS, GENERIC, STOP, TOKEN, fold

CATEGORIES_VERSION = "atlas-categories-2:centered:consensus:distinct:complete-labels"
WEIGHTS = {"body": .5, "structure": .3, "keyphrases": .2}
MIN_CALIBRATION = 24     # distinct contents needed to estimate the collection mean
K_SIGMA = 1.5            # accepted distance below a category's typical cohesion
PRIOR_WEIGHT = 3         # pseudo-members pulling small groups towards the prior
LINKAGE_CUT = .30        # average similarity needed to merge discovered groups
THEME_FLOOR = LINKAGE_CUT  # joining a theme requires the same floor as creating one
MIN_THRESHOLD = .05
MAX_STRUCTURE_WORDS = 400

# Words that describe the document's layout, packaging or administration and
# therefore never name a subject on their own.
LABEL_NOISE = set("""
form forms formulario formularios formulaire ficha cadastro inscricao trial free license licence
licenca terms conditions termos condicoes copyright draft untitled microsoft word pdf dvi tex latex
docx pptx editor inserted arxiv doi issn isbn edition edicao university universidade universidad
instituto institute department departamento faculdade semester semestre turma syllabus preprint
cambridge springer wiley elsevier oxford princeton siam impa sbm ams press verlag birkhauser dover
pearson mcgraw crc routledge publishing publishers editora editorial imprensa
federal estadual nacional campus reitoria graduacao
conclusion conclusions conclusao conclusoes acknowledgements acknowledgments agradecimentos
""".split())
# Generic words: acceptable inside a name ("Teoria dos números"), never alone.
LABEL_GENERIC = set("""
day days page pages volume school escola lecture lectures notes note notas nota aula aulas course
curso cursos apostila lista listas exercicio exercicios exercises exercise prova provas exam exams
homework tarefa tarefas resumo abstract chapter capitulo parte part introduction introducao preface
prefacio contents sumario indice index appendix apendice plano ensino disciplina project projeto
relatorio report thesis tese dissertacao dissertation monografia tcc paper article artigo
mathematics mathematical matematica matematicas math maths theory teoria theorems applications
aplicacoes introductory elementary advanced basic basico basica fundamentos fundamentals foundations
principles principios guide guia handbook manual topics topicos pesquisa research
function functions funcao funcoes input output entrada saida example examples exemplo exemplos
result results resultado resultados property properties propriedade propriedades
development desenvolvimento method methods metodo metodos methodology metodologia
""".split())
VERBISH = set("""
estava estavam esta estao foi foram era eram sendo seja sejam temos vamos podemos
calcular calcule calcula resolver resolva resolve determine determinar mostre mostrar
prove provar demonstre demonstrar encontre encontrar verifique verificar considere
seguintes seguinte entregar atencao respostas resposta devem deve find show compute
solve given let using denote suppose assume consider defined define obtain obtained
contendo completas completa devido aceitas aceitos entregue entregues tais tal such
tinha tinham disse disseram dizer said says would could should
""".split())
FAMILIES = {
    "Física": "physics fisica quantum quantica quantico quantum mechanics mecanica photon photons foton fotons "
              "particle particles particula particulas slit slits fenda fendas interference interferencia "
              "decoherence decoerencia relativity relatividade optics optica electromagnetism eletromagnetismo "
              "thermodynamics termodinamica born rule feynman spin qubit qubits entanglement emaranhamento",
    "Finanças e economia": "finance financas financial financeiro financeira option options opcao opcoes pricing "
                            "precificacao volatility volatilidade market markets mercado portfolio portfolios "
                            "stock stocks acoes derivatives derivativos heston trading investment investimento "
                            "economics economia economic interest rate juros credit risk hedge hedging",
    "Computação e dados": "computer computing computacao software programming programacao python java code "
                          "security seguranca encryption criptografia cryptography cryptographic cipher "
                          "data dados database network networks protocol internet agent agents agente agentes "
                          "machine learning neural gpu rust compiler",
    "Ciências da vida e saúde": "biology biologia cell cells celula celulas cancer tumor medicine medicina clinical "
                                "clinico patient patients paciente epidemiology epidemiologia genome genoma protein "
                                "proteina disease doenca health saude ecology ecologia species especies",
    "Química": "chemistry quimica molecule molecules molecula moleculas molecular reaction reacao chemical",
    "Humanidades e sociedade": "history historia philosophy filosofia law direito politics politica society "
                               "sociedade culture cultura literature literatura religion religiao education educacao",
    "Idiomas e linguagem": "english ingles grammar gramatica vocabulary vocabulario language idioma classroom "
                           "phrases frases pronunciation pronuncia spanish espanhol french frances",
    "Documentos e formulários": "form formulario license licenca terms conditions contrato contract trial "
                                "invoice fatura registration cadastro inscricao signature assinatura",
}
_FAMILY_TERMS = {family: set(words.split()) for family, words in FAMILIES.items()}


# ---------------------------------------------------------------- structure --

_TOC_LINE = re.compile(r"^\s*(?:(?:\d{1,2}(?:\.\d{1,2}){0,3})|[IVXLC]{1,5}\.?|(?:chapter|cap[ií]tulo|parte?|"
                       r"section|se[cç][aã]o)\s+\w+)?[\s.:)-]*([^\d\W][^\n]{2,90}?)\s*(?:\.\s*){3,}\s*\d{1,4}\s*$", re.I)
_NUMBERED = re.compile(r"^\s*(?:(\d{1,2}(?:\.\d{1,2}){0,2})\.?|(?:chapter|cap[ií]tulo)\s+\d{1,2}[.:]?)\s+([A-ZÀ-Ý][^\n]{2,80})$")
_WORD = re.compile(r"[^\W\d_]{2,}")


def structure_proxy(text: str, title: str = "") -> dict:
    """Approximate the structure contract from extracted text alone.

    Used when no font-based structure is available: table-of-contents lines with
    dot leaders, numbered headings and short lines of the first page.
    """
    pages = (text or "").split("\f")
    headings, toc, seen = [], [], set()
    for number, page in enumerate(pages[:40], 1):
        for line in page.splitlines():
            match = _TOC_LINE.match(line)
            if match:
                entry = re.sub(r"\s+", " ", match.group(1)).strip(" .")
                if _WORD.search(entry) and entry.casefold() not in seen:
                    seen.add(entry.casefold())
                    toc.append(entry)
                continue
            match = _NUMBERED.match(line.strip())
            if match:
                entry = match.group(2).strip()
                if (1 <= len(_WORD.findall(entry)) <= 10 and not entry.endswith((".", ",", ";", ":"))
                        and entry.casefold() not in seen):
                    seen.add(entry.casefold())
                    level = 1 + match.group(1).count(".") if match.group(1) else 1
                    headings.append({"text": entry, "level": level, "page": number, "source": "text"})
    front = []
    for line in (pages[0] if pages else "").splitlines()[:40]:
        line = line.strip()
        # Title-like lines only: instructions and running prose have sentences.
        if (2 <= len(_WORD.findall(line)) <= 10 and not re.search(r"@|https?:|www\.|\d{4}|[.;!?]\s+\S", line)):
            front.append(line)
        if len(front) >= 6:
            break
    return {"version": "atlas-text-proxy-1", "status": "partial", "kind": "documento", "title": title,
            "headings": headings[:80], "toc": toc[:120], "front": front}


def structural_lines(structure: dict | None, text: str = "", title: str = "") -> list[str]:
    """Title, outline/headings and table of contents, most important first."""
    if not structure or structure.get("status") == "unavailable":
        structure = structure_proxy(text, title)
    lines = [structure.get("title") or title or ""]
    if structure.get("title") and title and fold(title) != fold(structure["title"]):
        lines.append(title)
    lines += structure.get("front", [])[:3]
    lines += [item.get("text", "") for item in structure.get("headings", []) if item.get("level", 1) <= 2][:40]
    lines += structure.get("toc", [])[:40]
    result, seen = [], set()
    for line in lines:
        line = re.sub(r"\s+", " ", str(line)).strip()
        if line and fold(line) not in seen and re.search(r"[^\W\d_]{3}", line):
            seen.add(fold(line))
            result.append(line)
    return result


def structure_text(lines: list[str]) -> str:
    words = ". ".join(lines).split()
    return " ".join(words[:MAX_STRUCTURE_WORDS])


def keyphrase_text(topic: dict | None) -> str:
    topic = topic or {}
    phrases = [topic.get("label", "")] + [item.get("phrase", "") for item in topic.get("keyphrases", [])]
    return ". ".join(phrase for phrase in phrases if phrase)


# ------------------------------------------------------------------ n-grams --

def _content(word: str) -> bool:
    return len(word) >= 3 and word not in STOP and word not in GENERIC


def phrase_ngrams(line: str, widths=(1, 2, 3, 4)):
    """Yield (key, surface) n-grams that start and end with content words."""
    raw = TOKEN.findall(line)
    words = [fold(word) for word in raw]
    for start, word in enumerate(words):
        if not _content(word):
            continue
        for width in widths:
            end = start + width
            if end > len(words):
                break
            window = words[start:end]
            if not _content(window[-1]):
                continue
            if any(not _content(term) and term not in CONNECTORS for term in window):
                continue
            yield " ".join(window), " ".join(raw[start:end])


def profile_terms(lines: list[str], topic: dict | None, text: str, limit: int = 120) -> dict:
    """Compact naming material persisted per book (no full text is stored)."""
    structural, surfaces = Counter(), {}
    keyphrases = [item.get("phrase", "") for item in (topic or {}).get("keyphrases", [])]
    for line in list(lines) + keyphrases:
        for key, surface in set(phrase_ngrams(line)):
            structural[key] += 1
            surfaces.setdefault(key, surface)
    raw = TOKEN.findall(text[:400000])
    lower, capital = Counter(), Counter()
    for word in raw:
        (capital if word[:1].isupper() else lower)[fold(word)] += 1
    tokens = [fold(word) for word in raw]
    tokens = [word for word in tokens if _content(word)]
    body = Counter(tokens)
    body.update(a + " " + b for a, b in zip(tokens, tokens[1:]))
    # Words never written in lower case are names of people, places or venues.
    proper = sorted(word for word, count in capital.items() if not lower[word] and _content(word))
    return {"title": lines[0] if lines else "", "structure": dict(structural.most_common(limit)),
            "surface": {key: surfaces[key] for key, _ in structural.most_common(limit)},
            "body": dict(body.most_common(limit)), "proper": proper[:300]}


# -------------------------------------------------------------------- vectors --

def _normalize_rows(np, matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-9)


def combine(np, components: dict, weights: dict = WEIGHTS):
    """Center each component by its mean, then weight and renormalize.

    ``components`` maps a name to a list with one vector (or ``None``) per
    document. Missing components are skipped for that document only.
    """
    names = [name for name in weights if name in components]
    count = len(next(iter(components.values())))
    total = np.zeros((count, 0))
    for name in names:
        rows = components[name]
        present = [i for i, row in enumerate(rows) if row is not None]
        if not present:
            continue
        matrix = np.stack([np.asarray(rows[i], dtype=np.float64) for i in present])
        centered = _normalize_rows(np, matrix - matrix.mean(axis=0))
        if total.shape[1] == 0:
            total = np.zeros((count, matrix.shape[1]))
        total[present] += weights[name] * centered
    return _normalize_rows(np, total)


def cohesion(np, similarity, members):
    """Leave-one-out average similarity of each member to the others."""
    members = list(members)
    if len(members) < 2:
        return None
    block = similarity[np.ix_(members, members)]
    values = (block.sum(axis=1) - block.diagonal()) / (len(members) - 1)
    return float(values.mean()), float(values.std()), len(members)


def discovery_similarity(np, components: dict, combined):
    """Require agreement from two available views when discovering categories.

    A body vector can reflect language or document style instead of subject.
    Bound the combined affinity by the second strongest independent view
    (body, structure, keyphrases). Missing/constant views supply no evidence;
    body-only installations retain their original affinity.
    """
    count = len(combined)
    affinity = combined @ combined.T
    views = []
    for rows in components.values():
        present = [i for i, row in enumerate(rows) if row is not None]
        if len(present) < 2:
            continue
        matrix = np.stack([np.asarray(rows[i], dtype=np.float64) for i in present])
        centered = matrix - matrix.mean(axis=0)
        usable = np.linalg.norm(centered, axis=1) > 1e-9
        positions = np.asarray(present)[usable]
        vectors = _normalize_rows(np, centered[usable])
        view = np.full((count, count), -np.inf)
        view[np.ix_(positions, positions)] = vectors @ vectors.T
        views.append(view)
    if len(views) >= 2:
        consensus = np.sort(np.stack(views), axis=0)[-2]
        affinity = np.where(np.isfinite(consensus), np.minimum(affinity, consensus), affinity)
    return np.clip(affinity, -1, 1)


def prior(np, stats: list) -> tuple[float, float]:
    usable = [item for item in stats if item and item[2] >= 3]
    if not usable:
        return LINKAGE_CUT + .05, .1
    return float(np.mean([item[0] for item in usable])), float(np.mean([item[1] for item in usable]))


def threshold(stat, prior_values, floor: float = MIN_THRESHOLD) -> float:
    mean0, deviation0 = prior_values
    if stat:
        mean, deviation, size = stat
        mean = (mean * size + mean0 * PRIOR_WEIGHT) / (size + PRIOR_WEIGHT)
        deviation = (deviation * size + deviation0 * PRIOR_WEIGHT) / (size + PRIOR_WEIGHT)
    else:
        mean, deviation = mean0, deviation0
    return max(floor, mean - K_SIGMA * deviation)


def average_linkage(np, similarity):
    """Average-linkage agglomeration by nearest-neighbour chain.

    Returns merges ``(similarity, a, b)`` in the order performed. Average linkage
    is reducible, so cutting at a similarity gives the exact agglomerative
    result. O(n²) time and memory; ties resolve by index (deterministic).
    """
    count = len(similarity)
    matrix = np.array(similarity, dtype=np.float64, copy=True)
    np.fill_diagonal(matrix, -np.inf)
    size = np.ones(count)
    active = np.ones(count, dtype=bool)
    merges, chain, remaining = [], [], count
    while remaining > 1:
        if not chain:
            chain.append(int(np.flatnonzero(active)[0]))
        a = chain[-1]
        row = np.where(active, matrix[a], -np.inf)
        row[a] = -np.inf
        b = int(np.argmax(row))
        if len(chain) >= 2 and row[chain[-2]] >= row[b]:
            b = chain[-2]
        if len(chain) >= 2 and b == chain[-2]:
            chain.pop()
            chain.pop()
            first, second = min(a, b), max(a, b)
            merges.append((float(matrix[first, second]), first, second))
            merged = (size[first] * matrix[first] + size[second] * matrix[second]) / (size[first] + size[second])
            matrix[first] = merged
            matrix[:, first] = merged
            matrix[first, first] = -np.inf
            size[first] += size[second]
            active[second] = False
            matrix[second] = -np.inf
            matrix[:, second] = -np.inf
            remaining -= 1
        else:
            chain.append(b)
    return merges


def cut(count: int, merges, minimum: float) -> list[int]:
    parent = list(range(count))

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item
    for value, a, b in sorted(merges, key=lambda merge: (-merge[0], merge[1], merge[2])):
        if value < minimum:
            break
        root_a, root_b = find(a), find(b)
        parent[max(root_a, root_b)] = min(root_a, root_b)
    return [find(item) for item in range(count)]


# ------------------------------------------------------------------- naming --

def is_noise(key: str, surface: str = "", blocked: set | None = None, proper: set | None = None) -> bool:
    words = key.split()
    if not words or len(key.replace(" ", "")) < 4:
        return True
    if proper and all(word in proper or word in CONNECTORS for word in words):
        return True
    shown = surface.split()
    if shown and len(shown) == len(words) and all(word.isupper() and len(word) <= 5 for word in shown):
        return True
    # Accents split by LaTeX extraction ("Log ıstica") are not readable names.
    if re.search(r"[ıȷ¸˜´`ˆ¨]", surface):
        return True
    if any(word in VERBISH or word in LABEL_NOISE or word in (blocked or ()) for word in words):
        return True
    if all(word in GENERIC or word in LABEL_GENERIC or word in CONNECTORS for word in words):
        return True
    if len(words) == 1:
        word = words[0]
        # Loose acronyms ("Qkd") and fragments are not names a reader recognizes.
        if len(word) <= 4 or not re.search(r"[aeiouy]", word):
            return True
        if surface and surface.isupper() and len(surface) <= 6:
            return True
    return False


def _class_tfidf(classes: dict) -> dict:
    """BERTopic-style c-TF-IDF: tf within class × log(1 + A / frequency)."""
    totals = {name: sum(counter.values()) or 1 for name, counter in classes.items()}
    frequency = Counter()
    for counter in classes.values():
        frequency.update(counter)
    average = sum(totals.values()) / max(1, len(classes))
    return {name: {term: count / totals[name] * math.log(1 + average / frequency[term])
                   for term, count in counter.items()}
            for name, counter in classes.items()}


def name_groups(groups: dict, profiles: dict, background: dict | None = None, blocked: set | None = None) -> dict:
    """Name each group from n-grams shared by its members' structure.

    ``groups`` maps a group id to member ids; ``profiles`` maps member id to
    ``profile_terms`` output. ``background`` adds other classes (e.g. shelves)
    so that terms common to the whole library are discounted.
    Returns {group: {"label", "terms", "support", "basis"}}.
    """
    classes = {}
    for group, members in {**(background or {}), **groups}.items():
        counter = Counter()
        for member in members:
            profile = profiles.get(member) or {}
            # Each distinct document contributes the same mass. A long book
            # must not choose the group's label just by repeating more words.
            for field, weight in (("body", 1), ("structure", 3)):
                terms = profile.get(field, {})
                total = sum(terms.values()) or 1
                for term, count in terms.items():
                    counter[term] += weight * count / total
        classes[group] = counter
    scores = _class_tfidf(classes)
    result = {}
    for group, members in groups.items():
        members = list(members)
        size = len(members)
        coverage, title_coverage, surfaces = Counter(), Counter(), defaultdict(Counter)
        proper = set()
        for member in members:
            proper.update((profiles.get(member) or {}).get("proper", []))
        for member in members:
            profile = profiles.get(member) or {}
            title_coverage.update({term for term, _ in phrase_ngrams(profile.get("title", ""))})
            for term in profile.get("structure", {}):
                coverage[term] += 1
                surfaces[term][profile.get("surface", {}).get(term, term)] += 1
        needed = 1 if size == 1 else max(2, math.ceil(.3 * size))
        candidates = []
        for term, count in coverage.items():
            surface = surfaces[term].most_common(1)[0][0]
            words = len(term.split())
            # Small heterogeneous groups rarely share a phrase; a distinctive
            # phrase of one member beats a generic shared word ("Matrix").
            partial = count < needed and size <= 3 and words > 1
            if (count < needed and not partial) or is_noise(term, surface, blocked, proper):
                continue
            score = (scores[group].get(term, 0) * (count / size) ** .5 * (1 + .3 * min(words - 1, 2))
                     * (1 + title_coverage[term] / size)
                     * (.4 if words == 1 else 1) * (.6 if partial else 1))
            candidates.append((score, words, term, surface))
        top_terms = [term for term, _ in sorted(scores[group].items(), key=lambda item: -item[1])
                     if not is_noise(term, "", blocked, proper)][:8]
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        # Keep complete expressions when the extension has essentially the
        # same document support ("fully homomorphic encryption", not an
        # adjective fragment). Match whole, contiguous tokens only.
        candidates = [item for item in candidates if not any(
            other[1] > item[1] and f" {item[2]} " in f" {other[2]} "
            and coverage[other[2]] >= .8 * coverage[item[2]]
            and other[0] >= .8 * item[0]
            for other in candidates)]
        if not candidates:
            result[group] = {"label": "", "terms": top_terms, "support": 0, "basis": "none", "candidates": []}
            continue
        result[group] = {"label": display(candidates[0][3]), "terms": top_terms, "support": coverage[candidates[0][2]],
                         "basis": "shared" if size > 1 else "structure",
                         "candidates": [{"key": term, "label": display(surface), "score": score, "support": coverage[term]}
                                        for score, _words, term, surface in candidates[:12]]}
    return result


def display(surface: str) -> str:
    label = re.sub(r"\s+", " ", surface).strip()
    if label.isupper() or label.islower():
        label = label.lower()
    return label[:1].upper() + label[1:]


def rerank(np, named: dict, centroids: dict, encode) -> dict:
    """KeyBERT-style choice: c-TF-IDF rank combined with closeness to the group.

    ``encode`` maps phrases to unit vectors in the same space as ``centroids``
    (raw MiniLM). Boilerplate shared by members (instructions, running heads)
    is lexically frequent but semantically far from what the members discuss.
    """
    phrases = sorted({item["label"] for info in named.values() for item in info.get("candidates", [])})
    if not phrases:
        return named
    vectors = dict(zip(phrases, encode(phrases)))
    for group, info in named.items():
        options = info.get("candidates", [])
        if group not in centroids or len(options) < 2:
            continue
        for rank, item in enumerate(options):
            closeness = float(np.dot(vectors[item["label"]], centroids[group]))
            item["closeness"] = round(closeness, 4)
            words = len(item["key"].split())
            lexical = 1 - rank / len(options)
            item["final"] = (.45 * lexical + .55 * max(0.0, closeness)) * (1 if words > 1 else .85)
        best = max(options, key=lambda item: (item["final"], item["support"], item["key"]))
        info.update(label=best["label"], support=best["support"], basis=info["basis"] + "+semantic")
    return named


def family_for(label: str, terms: list, shelf_votes: Counter | None = None, kinds: Counter | None = None,
               shelf_families: dict | None = None) -> str:
    """Broad family for a discovered category; subject first, document kind second."""
    label_words = set(fold(label).split())
    term_words = {word for term in terms[:8] for word in fold(term).split()}
    best, best_score = "", 0
    for family, vocabulary in _FAMILY_TERMS.items():
        # Distinct words: one repeated term ("data set", "big data") is one signal.
        score = 3 * len(label_words & vocabulary) + len(term_words & vocabulary)
        if score > best_score:
            best, best_score = family, score
    if best and (label_words & _FAMILY_TERMS[best] or best_score >= 3):
        return best
    if kinds and kinds.most_common(1)[0][0] == "formulario" and kinds.most_common(1)[0][1] * 2 > sum(kinds.values()):
        return "Documentos e formulários"
    if shelf_votes and shelf_families:
        families = Counter()
        for shelf, votes in shelf_votes.items():
            if shelf in shelf_families:
                families[shelf_families[shelf]] += votes
        if families:
            family, votes = families.most_common(1)[0]
            if votes * 2 >= sum(shelf_votes.values()) or votes >= 2:
                return family
    return "Temas descobertos"
