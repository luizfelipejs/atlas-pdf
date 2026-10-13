"""Evidence-grounded topic names: graph salience + multilingual embeddings + MMR.

Extractive labels always occur in the sampled book. No API, generative model or
training on the model's own predictions. Scores are ranking heuristics.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
import unicodedata

try:
    from .semantic import clean_text, text_chunks, select_chunks, normalize, numpy_module, SemanticUnavailable
except ImportError:
    from semantic import clean_text, text_chunks, select_chunks, normalize, numpy_module, SemanticUnavailable

TOPIC_VERSION = "atlas-topics-1:graph:passage-consensus:mmr:headings-v3"
MAX_CANDIDATES = 64
MAX_PASSAGES = 24
TOKEN = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


STOP = set("""a o as os um uma uns umas de da do das dos em no na nos nas por para
com sem sob sobre e ou que se ao aos a pela pelo pelas pelos entre ate mais menos
muito muitos toda todo todas todos sua seu suas seus esta este isto isso esse essa
esses essas aquele aquela como quando onde porque qual quais quem ha ser estar foi
sao eram era sera tem ter pode podem cada tambem apenas ja ainda assim tanto quanto
the a an and or of in on to from by for with without into through about as at is are
was were be been being this that these those it its their them they we our you your
he she his her not but if then than such which who what how when where can could
would should may might will shall have has had do does did also all any some each
other both more most many much very only using used use based via within between
el la los las un una unos unas y del en con sin hacia como es son fue sus su al
le les des du et une un dans sur pour avec sans est sont au aux ce cette ces
nao sim bem mal somente alguns algumas outro outros outra outras mesmo mesma
antes depois agora aqui ali la alem logo entao portanto porem contudo quase
acima abaixo pois embora sendo sido eram seja sejam fosse fossem tambem
ele ela eles elas voce voces vos lhe lhes deles delas nele nela nisso aquilo
above below over under after before because while until during again further
once here there too same own rather together either neither nor yet just
und der die das ein eine einer eines einem einen den dem des ist sind war waren
mit ohne von zum zur auf fur aus bei nach vor oder aber auch nicht nur als sich
il lo gli le uno una un dei degli delle della dello del che non per con da nel
nella nelle sono essere come anche questo questa questi queste
ellos ellas nosotros vosotros usted ustedes nous vous ils elles leur leurs
sie wir ihr euch ihnen lei lui noi voi loro
что это как для при кто или его она они был были быть ещё уже все так нет
""".split())
FORM_WORDS = {"form", "formulario", "formulaire", "formular", "ficha", "cadastro"}
CONNECTORS = set("de da do das dos of del di du des e and y et".split())
GENERIC = set("""book books livro livros textbook chapter chapters capitulo capitulos
section sections secao secoes introduction introducao contents conteudo sumario index
indice references bibliography bibliografia referencia referencias copyright rights
reserved edition edicao publisher press isbn http https www page pages pagina paginas
figure figures figura figuras table tabela exercise exercises exercicio exercicios
example examples exemplo exemplos solution solutions solucao solucoes theorem proof
definition definitions definicao teorema prova respectively therefore however thus
explain explains explained explaining describe describes described discuss discusses
present presents presented consider considers shown show shows see following first
second third next previous new well known result results method methods approach
study studies studied research investigation investigates provides provide including
include includes detailed details explains study demonstrate demonstrates
teaches teach explores explore covers cover mixes mix prepare prepares preparing
chopped chop cooked cook seasoned season analyze analyzes analyse analyses analisa
estuda depende depends encompass encompasses reveals reveal evolved evolves
instructions techniques measurements information result results describes studies
write writes read reads lesson lessons authors author autores autor editors editor
department
apostila apostilas curso cursos course courses
ensina explica apresenta mostra exemplo seguinte forma parte maneira maneira
information introduction overview general particular important different several
""".split())
ADJECTIVES = set("""finite infinite linear nonlinear continuous common uniform real complex
small large general compact convex bounded closed complete open positive negative
mathematical numerical different particular basic modern ancient new important
finito infinito linear continua continuo comum uniforme pequeno grande geral
compacto convexo limitada limitado fechado completo aberto positivo negativo
addresses address telephone email institution questions question sentences sentence
""".split())


def content(word):
    return len(word) >= 3 and word not in STOP and word not in GENERIC


def _excerpt(passage, phrase):
    match = re.search(r"\b" + r"\s+".join(re.escape(word) for word in phrase.split()) + r"\b",
                      passage, re.IGNORECASE)
    start = max(0, match.start() - 180) if match else 0
    return passage[start:start + 600]


def _graph_rank(sentences):
    """Bounded, weighted TextRank over content-word co-occurrence."""
    frequency = Counter(word for sentence in sentences for word in sentence if content(word))
    vocabulary = set(word for word, _ in frequency.most_common(256))
    edges = defaultdict(Counter)
    for sentence in sentences:
        words = [word for word in sentence if word in vocabulary]
        for i, word in enumerate(words):
            for neighbour in words[i + 1:i + 5]:
                if neighbour != word:
                    edges[word][neighbour] += 1
                    edges[neighbour][word] += 1
    if not vocabulary:
        return {}
    size = len(vocabulary)
    ranks = dict.fromkeys(sorted(vocabulary), 1 / size)
    degrees = {word: sum(edges[word].values()) for word in ranks}
    for _ in range(24):
        dangling = sum(ranks[word] for word in ranks if not degrees[word]) / size
        updated = {word: .15 / size + .85 * (dangling + sum(
            ranks[other] * weight / degrees[other] for other, weight in edges[word].items()))
            for word in ranks}
        if sum(abs(updated[word] - ranks[word]) for word in ranks) < 1e-7:
            ranks = updated
            break
        ranks = updated
    highest = max(ranks.values())
    return {word: value / highest for word, value in ranks.items()}


def candidates_from(text):
    # Reuse the embedding budget: candidate generation is bounded even for huge
    # books, while both ends and interior samples remain represented.
    text = clean_text(text)
    lines = text.splitlines()
    signatures = [re.sub(r"\d+", "#", fold(line.strip())) for line in lines]
    repeated = Counter(signatures)
    is_form = sum(bool(re.search(r"_{6,}", line)) for line in lines) >= 3
    # Running page headers often contain author/journal names. Their repeated
    # occurrence is layout evidence, not independent support for a topic.
    seen = set()
    kept = []
    credits = 0
    for line, signature in zip(lines, signatures):
        # Blank form fields describe a document's layout, not its subject.
        if re.search(r"_{6,}", line):
            continue
        if re.fullmatch(r"(?:autor(?:es)?|authors?|editor(?:es|s)?|organizador(?:es)?|auteurs?)\s*:?", signature):
            credits = 8
            continue
        if credits:
            if not signature or len(TOKEN.findall(line)) > 10:
                credits = 0
            else:
                credits -= 1
                continue
        running = (repeated[signature] >= 3 and len(signature) <= 180
                   and len(TOKEN.findall(line)) <= 18)
        if not running or signature not in seen:
            kept.append(line)
        seen.add(signature)
    text = "\n".join(kept)
    acronyms = {fold(word) for word in TOKEN.findall(text) if word.isupper() and 2 <= len(word) <= 6}
    text = re.sub(r"(?i)\b(?:copyright|all rights reserved|todos os direitos reservados)\b[^.!?\n]{0,180}[.!?]", " ", text)
    if not text.strip():
        return [], []
    passages = select_chunks(text_chunks(text), MAX_PASSAGES)
    sentences, occurrences, forms = [], defaultdict(list), {}
    headings = set()
    front_headings = set()
    for line in text[:1200].splitlines():
        words = [fold(word) for word in TOKEN.findall(line)]
        if 2 <= len(words) <= 9:
            front_headings.add(" ".join(words))
    for line in clean_text(text)[:16000].splitlines():
        words = [fold(word) for word in TOKEN.findall(line)]
        if 1 <= len(words) <= 9:
            headings.add(" ".join(words))
    for index, passage in enumerate(passages):
        # Original sentence punctuation is retained by text_chunks.
        for sentence in re.split(r"[^\w\s'-]+|\d+|_+", passage):
            raw = TOKEN.findall(sentence)
            words = [fold(word) for word in raw]
            sentences.append(words)
            for start, word in enumerate(words):
                if not content(word):
                    continue
                for width in range(1, 5):
                    end = start + width
                    if end > len(words):
                        break
                    window = words[start:end]
                    if not content(window[-1]):
                        continue
                    if any(not content(term) and term not in CONNECTORS for term in window):
                        continue
                    if width > 1 and sum(content(term) for term in window) < 2:
                        continue
                    phrase = " ".join(window)
                    occurrences[phrase].append(index)
                    forms.setdefault(phrase, " ".join(raw[start:end]).lower())
    graph = _graph_rank(sentences)
    candidates = []
    for phrase, indexes in occurrences.items():
        words = phrase.split()
        if len(words) == 1 and phrase in ADJECTIVES:
            continue
        count = len(indexes)
        heading = phrase in headings
        if len(words) > 2 and count < 2 and not heading:
            continue
        lexical = (.32 * count / (count + 2) + .28 * len(set(indexes)) / len(passages)
                   + .25 * sum(graph.get(word, 0) for word in words) / len(words)
                   + .15 * heading)
        # Prefer recurring phrases over their generic component words.
        lexical *= 1 + (.14 if count >= 2 else .04) * (len(words) - 1)
        acronym = "".join(word[0] for word in words if content(word)) if len(words) > 1 else ""
        expansion = acronym in acronyms and len(occurrences.get(acronym, [])) >= 2
        if expansion:
            lexical *= 1.4
        candidates.append({"phrase": forms[phrase], "key": phrase, "count": count,
                           "coverage": len(set(indexes)) / len(passages),
                           "lexical": lexical, "heading": heading, "front_heading": phrase in front_headings,
                           "form_title": is_form and phrase in front_headings and bool(set(words) & FORM_WORDS),
                           "acronym": acronym if expansion else "",
                           "indexes": sorted(set(indexes))})
    candidates.sort(key=lambda item: (-item["lexical"], -len(item["key"].split()), item["key"]))
    return passages, candidates[:MAX_CANDIDATES]


def discover_topic(text, encoder):
    words = [fold(word) for word in TOKEN.findall(text)]
    if len(words) < 24 or len(set(word for word in words if content(word))) < 9:
        return {"version": TOPIC_VERSION, "status": "insufficient", "label": "", "keyphrases": [], "needs_review": True}
    passages, candidates = candidates_from(text)
    if not candidates:
        return {"version": TOPIC_VERSION, "status": "insufficient", "label": "", "keyphrases": [], "needs_review": True}
    result = {"version": TOPIC_VERSION, "status": "suggested", "method": "TextRank + frequência e cobertura",
              "passages": len(passages), "needs_review": True}
    vectors = None
    if hasattr(encoder, "encode_texts"):
        try:
            np = numpy_module()
            encoded = encoder.encode_texts(passages + [item["phrase"] for item in candidates])
            encoded = np.stack([normalize(vector) for vector in encoded])
            passage_vectors, vectors = encoded[:len(passages)], encoded[len(passages):]
            document = normalize(passage_vectors.mean(axis=0))
            local = vectors @ passage_vectors.T
            similarities = vectors @ document
            # Mean of the best half of passages discounts isolated mentions but
            # lets a substantial subtopic survive a panoramic book.
            support = np.sort(local, axis=1)[:, -max(1, math.ceil(len(passages) / 2)):].mean(axis=1)
            for i, item in enumerate(candidates):
                item["similarity"] = float(similarities[i])
                item["consensus"] = float(support[i])
            result.update(status="ready", method="TextRank + embeddings multilíngues + consenso entre trechos + MMR")
        except (SemanticUnavailable, ValueError, OSError) as error:
            result["warning"] = str(error)
    maximum = max(item["lexical"] for item in candidates)
    for item in candidates:
        lexical = item["lexical"] / maximum
        item["score"] = (.30 * lexical + .45 * max(0, item["similarity"]) + .25 * max(0, item["consensus"])) if vectors is not None else lexical
    best = max(range(len(candidates)), key=lambda i: candidates[i]["score"])
    expansions = [i for i, item in enumerate(candidates) if item["acronym"] == candidates[best]["key"]]
    if expansions:
        best = max(expansions, key=lambda i: candidates[i]["score"])
    front = [i for i, item in enumerate(candidates) if item["front_heading"]
             and item["score"] >= candidates[best]["score"] - .07
             and item.get("similarity", 1) >= .35]
    if front:
        best = max(front, key=lambda i: candidates[i]["score"])
    form_titles = [i for i, item in enumerate(candidates) if item["form_title"] and item.get("similarity", 1) >= .25]
    if form_titles:
        best = max(form_titles, key=lambda i: candidates[i]["score"])
    selected = []
    remaining = set(range(len(candidates)))
    while remaining and len(selected) < 5:
        def mmr(index):
            if not selected:
                return 2 if index == best else candidates[index]["score"]
            redundancy = max(float(vectors[index] @ vectors[other]) if vectors is not None else
                             len(set(candidates[index]["key"].split()) & set(candidates[other]["key"].split())) /
                             len(set(candidates[index]["key"].split()) | set(candidates[other]["key"].split()))
                             for other in selected)
            return .72 * candidates[index]["score"] - .28 * max(0, redundancy)
        index = max(sorted(remaining), key=mmr)
        remaining.remove(index)
        # Nested variants are not independent related themes.
        key = candidates[index]["key"]
        if any(f" {key} " in f" {candidates[other]['key']} " or
               f" {candidates[other]['key']} " in f" {key} " for other in selected):
            continue
        selected.append(index)
    phrases = []
    for index in selected:
        item = candidates[index]
        evidence = [_excerpt(passages[i], item["phrase"]) for i in item["indexes"][:3]]
        phrases.append({key: round(item[key], 4) if isinstance(item[key], float) else item[key]
                        for key in ("phrase", "count", "coverage", "score", "similarity", "consensus") if key in item})
        phrases[-1]["evidence"] = evidence
    primary = phrases[0]
    result.update(label=primary["phrase"][0].upper() + primary["phrase"][1:], keyphrases=phrases)
    # A label remains a suggestion; weak or localized support is explicit.
    result["needs_review"] = (vectors is None or primary.get("similarity", 0) < .35 or
                              (len(passages) > 2 and primary["coverage"] < .2))
    if candidates[best]["form_title"]:
        result.update(document_kind="form", needs_review=True)
    return result
