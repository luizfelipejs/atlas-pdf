"""Semantic shelves and open-set categories persisted alongside Atlas's SQLite catalog."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import re

try:
    from .semantic import ENCODER, DIMENSIONS, MODEL_NAME, MODEL_VERSION, SemanticUnavailable, normalize, numpy_module, vector_key
    from .taxonomy import TAXONOMY
    from .topics import TOPIC_VERSION, discover_topic, fold
    from . import categories as cat
except ImportError:
    from semantic import ENCODER, DIMENSIONS, MODEL_NAME, MODEL_VERSION, SemanticUnavailable, normalize, numpy_module, vector_key
    from taxonomy import TAXONOMY
    from topics import TOPIC_VERSION, discover_topic, fold
    import categories as cat

THRESHOLD = .68          # raw cosine, used only while the collection is too small to calibrate
NOT_SUBJECTS = ("Revisar", "Outros assuntos")
SHELF_FAMILIES = {name: family for name, (family, _msc, _aliases) in TAXONOMY.items()}


def migrate(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS semantic_vectors (
            key TEXT PRIMARY KEY, model TEXT NOT NULL, vector BLOB NOT NULL, chunks INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_members (
            book_id TEXT PRIMARY KEY, vector_key TEXT NOT NULL, category TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS semantic_members_category ON semantic_members(category);
        CREATE TABLE IF NOT EXISTS semantic_centroids (
            category TEXT NOT NULL, model TEXT NOT NULL, vector_sum BLOB NOT NULL,
            quantity INTEGER NOT NULL, PRIMARY KEY(category, model)
        );
        CREATE TABLE IF NOT EXISTS semantic_themes (number INTEGER PRIMARY KEY AUTOINCREMENT);
        CREATE TABLE IF NOT EXISTS semantic_topics (
            vector_key TEXT NOT NULL, version TEXT NOT NULL, profile TEXT NOT NULL,
            PRIMARY KEY(vector_key, version)
        );
        CREATE TABLE IF NOT EXISTS semantic_profiles (
            book_id TEXT PRIMARY KEY, version TEXT NOT NULL, vector_key TEXT NOT NULL,
            structure_key TEXT NOT NULL DEFAULT '', keyphrase_key TEXT NOT NULL DEFAULT '',
            lexical TEXT NOT NULL DEFAULT '', anchor TEXT NOT NULL DEFAULT '',
            candidates TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL DEFAULT '',
            terms TEXT NOT NULL DEFAULT '{}'
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(semantic_themes)")}
    for name, definition in (("label", "TEXT NOT NULL DEFAULT ''"), ("label_version", "TEXT NOT NULL DEFAULT ''"),
                             ("family", "TEXT NOT NULL DEFAULT ''"), ("retired", "INTEGER NOT NULL DEFAULT 0")):
        if name not in columns:
            connection.execute(f"ALTER TABLE semantic_themes ADD COLUMN {name} {definition}")
            connection.commit()


def themes(connection):
    return [f"Tema {row[0]}" for row in connection.execute("SELECT number FROM semantic_themes WHERE retired=0 ORDER BY number")]


def theme_subjects(connection):
    # IDs stay stable across label improvements, merges and manual assignments.
    return [{"name": f"Tema {row[0]}", "label": row[1] or f"Tema {row[0]}",
             "family": row[2] or "Temas descobertos", "msc": ""}
            for row in connection.execute("SELECT number,label,family FROM semantic_themes WHERE retired=0 ORDER BY number")]


def rebuild_centroids(connection):
    """Exact sums of unit vectors, counting identical content once per shelf.

    Rebuilding from members avoids rounding drift and stale contributions after
    manual corrections, missing files, content changes or interrupted scans.
    Call in the same transaction as the corresponding catalog mutation.
    """
    rows = connection.execute("""
        SELECT DISTINCT m.category, v.key, v.model, v.vector
        FROM semantic_members m JOIN semantic_vectors v ON v.key=m.vector_key
        JOIN books b ON b.id=m.book_id
        WHERE b.missing=0 AND b.status!='error' AND m.category NOT IN ('Revisar','Outros assuntos')
        ORDER BY m.category,v.key
    """).fetchall()
    connection.execute("DELETE FROM semantic_centroids")
    if not rows:
        return
    np = numpy_module()
    groups = {}
    for row in rows:
        key = (row[0], row[2])
        total, count = groups.get(key, (np.zeros(DIMENSIONS, dtype=np.float32), 0))
        groups[key] = (total + np.frombuffer(row[3], dtype="<f4"), count + 1)
    connection.executemany("INSERT INTO semantic_centroids VALUES (?,?,?,?)",
                           [(category, model, total.astype("<f4").tobytes(), count)
                            for (category, model), (total, count) in groups.items()])


def sync_member(connection, book_id, analysis=None):
    book = connection.execute("SELECT category,analysis,status,missing FROM books WHERE id=?", (book_id,)).fetchone()
    old = connection.execute("SELECT vector_key FROM semantic_members WHERE book_id=?", (book_id,)).fetchone()
    connection.execute("DELETE FROM semantic_members WHERE book_id=?", (book_id,))
    if not book or book[2] == "error" or book[3]:
        return
    semantic = (analysis if analysis is not None else json.loads(book[1])).get("semantic", {})
    key = semantic.get("vector_key") or (old[0] if old and analysis is None else None)
    if key and connection.execute("SELECT 1 FROM semantic_vectors WHERE key=?", (key,)).fetchone():
        connection.execute("INSERT INTO semantic_members VALUES (?,?,?)", (book_id, key, book[0]))


def _theme_number(name):
    match = re.fullmatch(r"Tema (\d+)", name or "")
    return int(match.group(1)) if match else None


class SemanticCatalog:
    def __init__(self, connection, encoder=None, threshold=THRESHOLD):
        self.connection = connection
        self.encoder = encoder or ENCODER
        self.threshold = threshold
        self.unavailable = ""
        self._vectors = {}

    # ------------------------------------------------------------ vectors --
    def _vector(self, key):
        if not key:
            return None
        if key not in self._vectors:
            row = self.connection.execute("SELECT vector FROM semantic_vectors WHERE key=? AND model=?",
                                          (key, MODEL_VERSION)).fetchone()
            if not row:
                return None
            self._vectors[key] = numpy_module().frombuffer(row[0], dtype="<f4")
        return self._vectors[key]

    def _auxiliary(self, text):
        """Cached embedding of a short structural text (title/outline/keyphrases)."""
        if not text or not hasattr(self.encoder, "encode_texts"):
            return ""
        key = hashlib.sha256(("aux\0" + MODEL_VERSION + "\0" + text).encode("utf-8")).hexdigest()
        if self._vector(key) is not None:
            return key
        try:
            vector = normalize(self.encoder.encode_texts([text])[0])
        except (SemanticUnavailable, ValueError, OSError, IndexError, TypeError):
            return ""
        if vector.shape != (DIMENSIONS,):
            return ""
        self.connection.execute("INSERT OR REPLACE INTO semantic_vectors VALUES (?,?,?,0)",
                                (key, MODEL_VERSION, vector.astype("<f4").tobytes()))
        self._vectors[key] = vector
        return key

    def _members(self, exclude_book=None, exclude_keys=()):
        """Active members with their profile; identical content counted once per category."""
        rows = self.connection.execute("""
            SELECT m.book_id, m.category, m.vector_key, b.manual_category,
                   COALESCE(p.structure_key,''), COALESCE(p.keyphrase_key,''), COALESCE(p.lexical,''),
                   COALESCE(p.anchor,''), COALESCE(p.candidates,'[]'), COALESCE(p.kind,'')
            FROM semantic_members m JOIN books b ON b.id=m.book_id
            JOIN semantic_vectors v ON v.key=m.vector_key AND v.model=?
            LEFT JOIN semantic_profiles p ON p.book_id=m.book_id
            WHERE b.missing=0 AND b.status!='error'
            ORDER BY m.book_id
        """, (MODEL_VERSION,)).fetchall()
        return [row for row in rows if row[0] != exclude_book and row[2] not in exclude_keys]

    def _components(self, keys, extra=None):
        components = {"body": [], "structure": [], "keyphrases": []}
        for body, structure, keyphrases in list(keys) + ([extra] if extra else []):
            components["body"].append(self._vector(body))
            components["structure"].append(self._vector(structure))
            components["keyphrases"].append(self._vector(keyphrases))
        return components

    def _space(self, keys, extra=None):
        """Centered, structure-weighted vectors for distinct contents (+ an optional new one)."""
        return cat.combine(numpy_module(), self._components(keys, extra))

    @staticmethod
    def _core(row):
        """Category a member helps to define, independent of later regrouping."""
        category, manual, lexical, anchor = row[1], row[3], row[6], row[7]
        if manual:
            return manual
        if lexical in TAXONOMY and anchor in ("title", "lexical"):
            return lexical
        if not lexical and category in TAXONOMY:
            # Older profiles lack lexical details: their shelves came from the lexicon.
            return category
        return category if _theme_number(category) else None

    def nearest(self, vector, key, book_id):
        np = numpy_module()
        # Exclude both the book's previous content and identical copies. A book
        # must never obtain a perfect score merely by matching its own vector.
        excluded = self.connection.execute("""
            SELECT DISTINCT m.category,v.key,v.vector FROM semantic_members m
            JOIN semantic_vectors v ON v.key=m.vector_key
            JOIN books b ON b.id=m.book_id
            WHERE v.model=? AND b.missing=0 AND b.status!='error' AND
            (v.key=? OR v.key IN (SELECT vector_key FROM semantic_members WHERE book_id=?))
        """, (MODEL_VERSION, key, book_id)).fetchall()
        candidates = []
        for row in self.connection.execute("SELECT category,vector_sum,quantity FROM semantic_centroids WHERE model=? ORDER BY category", (MODEL_VERSION,)):
            total = np.frombuffer(row[1], dtype="<f4").copy()
            count = row[2]
            for category, _key, contribution in excluded:
                if category == row[0]:
                    total -= np.frombuffer(contribution, dtype="<f4")
                    count -= 1
            if count < 1 or np.linalg.norm(total) < 1e-8:
                continue
            score = float(np.clip(np.dot(vector, normalize(total)), -1, 1))
            candidates.append({"category": row[0], "similarity": score, "quantity": count})
        return sorted(candidates, key=lambda item: (-item["similarity"], item["category"]))

    def calibrated(self, key, structure_key, keyphrase_key, book_id):
        """Average similarity and per-category threshold in the centered space.

        Returns None while the collection is too small to estimate its mean.
        """
        np = numpy_module()
        previous = self.connection.execute("SELECT vector_key FROM semantic_members WHERE book_id=?", (book_id,)).fetchone()
        members = self._members(book_id, {key, previous[0] if previous else ""})
        contents = {}
        for row in members:
            contents.setdefault(row[2], (row[2], row[4], row[5]))
        if len(contents) < cat.MIN_CALIBRATION:
            return None
        order = sorted(contents)
        space = self._space([contents[item] for item in order], (key, structure_key, keyphrase_key))
        index = {item: i for i, item in enumerate(order)}
        document = space[-1]
        space = space[:-1]
        similarity = space @ document
        groups = defaultdict(set)
        for row in members:
            core = self._core(row)
            if core and core not in NOT_SUBJECTS:
                groups[core].add(index[row[2]])
        sums = {name: space[sorted(items)].sum(axis=0) for name, items in groups.items()}
        stats = {}
        for name, items in groups.items():
            items = sorted(items)
            if len(items) >= 2:
                values = (space[items] @ sums[name] - 1) / (len(items) - 1)
                stats[name] = (float(values.mean()), float(values.std()), len(items))
        prior = cat.prior(np, list(stats.values()))
        candidates = []
        for name, items in groups.items():
            items = sorted(items)
            floor = cat.THEME_FLOOR if _theme_number(name) else cat.MIN_THRESHOLD
            limit = cat.threshold(stats.get(name), prior, floor)
            score = float(similarity[items].mean())
            candidates.append({"category": name, "similarity": round(score, 4), "threshold": round(limit, 4),
                               "margin": round(score - limit, 4), "quantity": len(items)})
        candidates.sort(key=lambda item: (-item["margin"], item["category"]))
        return candidates

    # ------------------------------------------------------------- profile --
    def _profile(self, book_id, text, structure, topic, decision, key):
        book = self.connection.execute("SELECT title,author FROM books WHERE id=?", (book_id,)).fetchone()
        title = (structure or {}).get("title") or (book[0] if book else "")
        lines = cat.structural_lines(structure, text, title)
        analysis = decision.get("analysis", {})
        candidates = [item.get("category") for item in analysis.get("candidates", [])[:2]]
        lexical = decision.get("category", "Revisar")
        title_match = bool(analysis.get("candidates") and analysis["candidates"][0].get("title_match"))
        anchor = ("title" if title_match else "lexical") if lexical in TAXONOMY else ""
        structure_key = self._auxiliary(cat.structure_text(lines))
        keyphrase_key = self._auxiliary(cat.keyphrase_text(topic))
        terms = cat.profile_terms(lines, topic, text)
        kind = (structure or {}).get("kind", "") if (structure or {}).get("status") != "unavailable" else ""
        self.connection.execute("INSERT OR REPLACE INTO semantic_profiles VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (book_id, cat.CATEGORIES_VERSION, key, structure_key, keyphrase_key, lexical, anchor,
                                 json.dumps(candidates, ensure_ascii=False), kind, json.dumps(terms, ensure_ascii=False)))
        return {"lexical": lexical, "anchor": anchor, "candidates": candidates, "kind": kind,
                "structure_key": structure_key, "keyphrase_key": keyphrase_key, "terms": terms,
                "structure_source": "pdf" if structure and structure.get("status") != "unavailable" else "texto",
                "structure_lines": len(lines), "author": book[1] if book else ""}

    def _single_label(self, topic, profile):
        blocked = {word for word in fold(profile.get("author", "")).split() if len(word) > 2}
        label = (topic or {}).get("label", "")
        proper = set(profile["terms"].get("proper", []))
        if label and not cat.is_noise(fold(label), label, blocked, proper) and len(label.split()) <= 6:
            return label
        named = cat.name_groups({"self": ["self"]}, {"self": profile["terms"]}, blocked=blocked)["self"]
        return named["label"]

    # ------------------------------------------------------------ classify --
    def classify(self, text, decision, book_id, structure=None):
        analysis = decision.setdefault("analysis", {})
        semantic = {"model": MODEL_NAME, "version": MODEL_VERSION, "threshold": self.threshold,
                    "status": "insufficient", "candidates": []}
        analysis["semantic"] = semantic
        words = re.findall(r"[^\W\d_]{3,}", text.lower())
        if len(words) < 24 or len(set(words)) < 9:
            return decision
        try:
            if self.unavailable:
                raise SemanticUnavailable(self.unavailable)
            key = vector_key(text)
            cached = self.connection.execute("SELECT vector,chunks FROM semantic_vectors WHERE key=? AND model=?", (key, MODEL_VERSION)).fetchone()
            if cached:
                vector = numpy_module().frombuffer(cached[0], dtype="<f4")
                chunks = cached[1]
            else:
                vector, chunks = self.encoder.embedding_livro(text)
                vector = normalize(vector)
                if vector.shape != (DIMENSIONS,):
                    raise ValueError("Dimensão inesperada do embedding")
                self.connection.execute("INSERT OR REPLACE INTO semantic_vectors VALUES (?,?,?,?)",
                                        (key, MODEL_VERSION, vector.astype("<f4").tobytes(), chunks))
                # Persist reusable inference even if cancellation precedes book save.
                self.connection.commit()
            self._vectors[key] = vector
            cached_topic = self.connection.execute(
                "SELECT profile FROM semantic_topics WHERE vector_key=? AND version=?", (key, TOPIC_VERSION)).fetchone()
            if cached_topic:
                topic = json.loads(cached_topic[0])
            else:
                topic = discover_topic(text, self.encoder)
                # Retry a statistical fallback on the next scan if the model
                # becomes available; do not make transient failures permanent.
                if topic["status"] == "ready":
                    self.connection.execute("INSERT OR REPLACE INTO semantic_topics VALUES (?,?,?)",
                                            (key, TOPIC_VERSION, json.dumps(topic, ensure_ascii=False)))
                    self.connection.commit()
            analysis["topic"] = topic
            profile = self._profile(book_id, text, structure, topic, decision, key)
            lexical = decision["category"]
            duplicate = self.connection.execute("""
                SELECT m.category FROM semantic_members m JOIN books b ON b.id=m.book_id
                WHERE m.vector_key=? AND m.book_id!=? AND b.missing=0 AND b.status!='error'
                AND m.category NOT IN ('Revisar','Outros assuntos') ORDER BY b.manual_category='',m.book_id LIMIT 1
            """, (key, book_id)).fetchone()
            calibrated = self.calibrated(key, profile["structure_key"], profile["keyphrase_key"], book_id)
            if calibrated is None:
                chosen, source, best, candidates = self._legacy(vector, key, book_id, lexical, duplicate, decision)
                semantic["mode"] = "inicial"
            else:
                chosen, source, best, candidates = self._open_set(calibrated, profile, lexical, duplicate, book_id, key, decision)
                semantic["mode"] = "calibrado"
                if best:
                    semantic["threshold"] = best["threshold"]
            if _theme_number(chosen) and chosen in themes(self.connection):
                label = self._single_label(topic, profile)
                if label:
                    self.connection.execute(
                        "UPDATE semantic_themes SET label=?,label_version=? WHERE number=? AND (label='' OR label_version NOT IN (?,?))",
                        (label, cat.CATEGORIES_VERSION, _theme_number(chosen), cat.CATEGORIES_VERSION, cat.CATEGORIES_VERSION + ":group"))
            semantic.update(status="ready", source=source, category=chosen, vector_key=key,
                            chunks=chunks, candidates=candidates[:4],
                            similarity=best["similarity"] if best else None,
                            lexical=lexical, anchor=profile["anchor"], engine=cat.CATEGORIES_VERSION,
                            structure={"source": profile["structure_source"], "lines": profile["structure_lines"],
                                       "kind": profile["kind"]})
            decision["category"] = chosen
            analysis["method"] = "Embeddings multilíngues centralizados + estrutura + conceitos + extração de tema"
            semantic["reason"] = self._reason(source, chosen, best, lexical, profile, semantic)
            decision["reason"] = semantic["reason"] + decision["reason"]
        except (SemanticUnavailable, ValueError) as error:
            if isinstance(error, SemanticUnavailable):
                self.unavailable = str(error)
            semantic.update(status="unavailable", warning=str(error))
            if "topic" not in analysis:
                analysis["topic"] = discover_topic(text, None)
            decision["reason"] += " Classificação semântica indisponível; usados conceitos do conteúdo."
        return decision

    def _new_theme(self, book_id, key):
        old = self.connection.execute("SELECT m.category,m.vector_key FROM semantic_members m WHERE m.book_id=?", (book_id,)).fetchone()
        # Stable singleton theme on reclassification of unchanged content.
        if old and old[1] == key and old[0] in themes(self.connection):
            return old[0], "retained_theme"
        number = self.connection.execute("INSERT INTO semantic_themes DEFAULT VALUES").lastrowid
        return f"Tema {number}", "new_theme"

    def _legacy(self, vector, key, book_id, lexical, duplicate, decision):
        """Small collections: raw cosine with the initial 0.68 threshold."""
        candidates = self.nearest(vector, key, book_id)
        best = candidates[0] if candidates else None
        matched = best is not None and best["similarity"] >= self.threshold
        # Well-supported named subjects anchor shelves, preserving the
        # subject/tool distinction that sentence similarity alone can miss.
        if lexical in TAXONOMY:
            return lexical, "confirmed" if matched and best["category"] == lexical else "subject_anchor", best, candidates
        if duplicate:
            decision["confidence"] = 0.0
            return duplicate[0], "identical_content", best, candidates
        if matched:
            decision["confidence"] = best["similarity"]
            return best["category"], "centroid", best, candidates
        decision["confidence"] = 0.0
        return (*self._new_theme(book_id, key), best, candidates)

    def _open_set(self, candidates, profile, lexical, duplicate, book_id, key, decision):
        by_name = {item["category"]: item for item in candidates}
        discovered = [item for item in candidates if _theme_number(item["category"])]
        if lexical in TAXONOMY:
            anchor = by_name.get(lexical)
            if profile["anchor"] == "title":
                return lexical, "confirmed" if anchor and anchor["margin"] >= 0 else "subject_anchor", anchor, candidates
            theme = discovered[0] if discovered and discovered[0]["margin"] >= 0 else None
            # The shelf stays when the content lies within the shelf's own spread.
            if anchor and anchor["margin"] >= 0:
                return lexical, "confirmed", anchor, candidates
            if theme:
                # The lexicon found tools of a shelf, but the content and outline
                # sit inside a discovered group and outside the shelf's spread.
                decision["confidence"] = max(0.0, min(1.0, theme["similarity"]))
                return theme["category"], "centroid", theme, candidates
            return lexical, "subject_anchor", anchor, candidates
        if duplicate:
            decision["confidence"] = 0.0
            return duplicate[0], "identical_content", by_name.get(duplicate[0]), candidates
        top = candidates[0] if candidates else None
        if top and top["margin"] >= 0 and (_theme_number(top["category"]) or top["category"] in profile["candidates"]):
            decision["confidence"] = max(0.0, min(1.0, top["similarity"]))
            return top["category"], "centroid", top, candidates
        if discovered and discovered[0]["margin"] >= 0:
            decision["confidence"] = max(0.0, min(1.0, discovered[0]["similarity"]))
            return discovered[0]["category"], "centroid", discovered[0], candidates
        decision["confidence"] = 0.0
        return (*self._new_theme(book_id, key), top, candidates)

    def _reason(self, source, chosen, best, lexical, profile, semantic):
        name = chosen
        structure = (" Título, cabeçalhos e sumário foram comparados junto ao corpo do texto."
                     if profile["structure_lines"] > 1 else "")
        if semantic.get("mode") == "calibrado" and best:
            measure = (f"similaridade média {best['similarity']:.2f} com {best['quantity']} conteúdos, "
                       f"limiar próprio do grupo {best['threshold']:.2f}")
        elif best:
            measure = f"semelhança de {best['similarity']:.1%} (limiar inicial {self.threshold:.0%}, {best['quantity']} conteúdos)"
        else:
            measure = ""
        if source == "centroid":
            moved = f" Os conceitos indicavam {lexical}, mas o conteúdo fica fora da dispersão dessa ala." if lexical in TAXONOMY and lexical != chosen else ""
            return f"Agrupado em {name} por conteúdo e estrutura ({measure}).{moved}{structure} "
        if source in ("new_theme", "retained_theme"):
            return (f"{name}: categoria criada pelo conteúdo, pois nenhuma ala ou tema existente atingiu o próprio limiar."
                    f"{structure} ")
        if source == "identical_content":
            return f"Conteúdo idêntico a um PDF já agrupado em {name}. "
        if source == "confirmed":
            return f"Ala confirmada pela comparação com livros semelhantes ({measure}). " if measure else ""
        return "Embedding local registrado para comparação com livros semelhantes. " if not measure else \
            f"Ala definida pelos conceitos; a comparação semântica não a confirmou ({measure}). "

    # --------------------------------------------------------- consolidate --
    def consolidate(self):
        """Regroup discovered categories over the whole collection.

        Order-independent given the books' lexical decisions and vectors:
        shelves keep books whose title anchors them or whose content lies within
        the shelf's own spread; the remaining books are clustered by average
        linkage. Surviving groups keep the theme ID they share with most members;
        absorbed themes are retired. Manual choices never move. Returns the
        books whose category changed.
        """
        if self.unavailable:
            return []
        try:
            np = numpy_module()
        except SemanticUnavailable:
            return []
        rows = self._members()
        contents = {}
        for row in rows:
            contents.setdefault(row[2], (row[2], row[4], row[5]))
        if len(contents) < cat.MIN_CALIBRATION:
            return []
        order = sorted(contents)
        index = {item: i for i, item in enumerate(order)}
        components = self._components([contents[item] for item in order])
        space = cat.combine(np, components)
        similarity = space @ space.T
        theme_similarity = cat.discovery_similarity(np, components, space)
        books = {row[0]: row for row in rows}
        shelves = defaultdict(set)
        fixed_themes = defaultdict(set)
        for row in rows:
            core = self._core(row)
            if row[3] and _theme_number(row[3]):
                fixed_themes[row[3]].add(index[row[2]])
            elif core in TAXONOMY:
                shelves[core].add(index[row[2]])
        stats = {name: cat.cohesion(np, similarity, items) for name, items in shelves.items()}
        prior = cat.prior(np, list(stats.values()))
        limits = {name: cat.threshold(stats[name], prior) for name in shelves}

        def average(position, items, matrix=similarity):
            items = [item for item in items if item != position]
            return float(matrix[position, items].mean()) if items else -1.0

        assignment, unexplained = {}, []
        for book_id, row in sorted(books.items()):
            if row[3]:
                continue
            position = index[row[2]]
            lexical, anchor = row[6], row[7]
            if not lexical:
                # Profiles from older versions: keep their shelf, regroup themes.
                if row[1] in TAXONOMY:
                    assignment[book_id] = (row[1], "subject_anchor")
                else:
                    unexplained.append(book_id)
                continue
            if anchor == "title":
                assignment[book_id] = (lexical, "subject_anchor")
                continue
            margins = sorted(((average(position, items) - limits[name], name) for name, items in shelves.items()),
                             key=lambda item: (-item[0], item[1]))
            if anchor == "lexical" and lexical in shelves and average(position, shelves[lexical]) >= limits[lexical]:
                assignment[book_id] = (lexical, "confirmed")
                continue
            if not anchor and margins and margins[0][0] >= 0 and margins[0][1] in json.loads(row[8] or "[]"):
                # Two independent signals agree: the closest shelf is also a lexical candidate.
                assignment[book_id] = (margins[0][1], "centroid")
                continue
            unexplained.append(book_id)
        positions = sorted({index[books[book_id][2]] for book_id in unexplained})
        clusters = defaultdict(list)
        if positions:
            local = theme_similarity[np.ix_(positions, positions)]
            labels = cat.cut(len(positions), cat.average_linkage(np, local), cat.LINKAGE_CUT)
            for local_index, label in enumerate(labels):
                clusters[label].append(positions[local_index])
        groups = sorted(sorted(items) for items in clusters.values() if len(items) >= 2)
        fixed_index = {}
        for theme, items in sorted(fixed_themes.items()):
            fixed_index[len(groups)] = theme
            groups.append(sorted(items))
        group_of = {}
        for number, items in enumerate(groups):
            for item in items:
                group_of.setdefault(item, number)
        group_stats = [cat.cohesion(np, theme_similarity, items) for items in groups]
        group_prior = cat.prior(np, group_stats)
        group_limits = [cat.threshold(stat, group_prior, cat.THEME_FLOOR) for stat in group_stats]
        provisional = {}
        for book_id in unexplained:
            position = index[books[book_id][2]]
            if position in group_of:
                provisional[book_id] = group_of[position]
                continue
            options = [(average(position, items, theme_similarity) - group_limits[number], number)
                       for number, items in enumerate(groups)]
            options = [item for item in options if item[0] >= 0]
            if options:
                provisional[book_id] = max(options)[1]
            elif books[book_id][6] in TAXONOMY:
                assignment[book_id] = (books[book_id][6], "subject_anchor")
            else:
                provisional[book_id] = ("single", position)
        # Stable IDs: each group keeps the theme shared by most of its members.
        members_of = defaultdict(list)
        for book_id, group in provisional.items():
            members_of[group].append(book_id)
        names = dict(fixed_index)
        taken = set(fixed_index.values())
        for group, group_books in sorted(members_of.items(), key=lambda item: (-len(item[1]), str(item[0]))):
            if group in names:
                continue
            votes = Counter(books[book_id][1] for book_id in group_books
                            if _theme_number(books[book_id][1]) and books[book_id][1] not in taken)
            if votes:
                names[group] = max(votes.items(), key=lambda item: (item[1], -_theme_number(item[0])))[0]
            else:
                number = self.connection.execute("INSERT INTO semantic_themes DEFAULT VALUES").lastrowid
                names[group] = f"Tema {number}"
            taken.add(names[group])
        for book_id, group in provisional.items():
            size = len({books[item][2] for item in members_of[group]})
            assignment[book_id] = (names[group], "centroid" if size > 1 or group in fixed_index else "retained_theme")
        # Apply before renaming: retirement checks the categories stored in books.
        changes = self._apply(assignment, books)
        self._rename(rows, assignment, names, members_of, books, fixed_themes, contents)
        rebuild_centroids(self.connection)
        self.connection.commit()
        return changes

    def _apply(self, assignment, books):
        changes = []
        for book_id, (category, source) in sorted(assignment.items()):
            if books[book_id][1] == category:
                continue
            row = self.connection.execute("SELECT title,category,reason,analysis,manual_category FROM books WHERE id=?", (book_id,)).fetchone()
            if not row or row[4]:
                continue
            analysis = json.loads(row[3] or "{}")
            semantic = analysis.setdefault("semantic", {})
            old_prefix = semantic.get("reason", "")
            semantic.update(category=category, source=source, stage="consolidação")
            if source == "subject_anchor":
                reason = f"Mantido em {category} pelos conceitos do conteúdo; nenhum grupo descoberto o explica melhor. "
            elif source == "confirmed":
                reason = f"{category} confirmada: o conteúdo fica dentro da dispersão típica dessa ala. "
            elif category in TAXONOMY:
                reason = (f"Reagrupado em {category} ao revisar todo o acervo: a ala mais próxima pelo conteúdo e pela "
                          f"estrutura coincide com um assunto sugerido pelos conceitos (antes: {row[1]}). ")
            else:
                reason = (f"Reagrupado em {category} ao revisar todo o acervo: livros semelhantes por conteúdo e estrutura "
                          f"ficaram juntos, independentemente da ordem de leitura (antes: {row[1]}). ")
            text = row[2]
            if old_prefix and text.startswith(old_prefix):
                text = text[len(old_prefix):]
            semantic["reason"] = reason
            self.connection.execute("UPDATE books SET category=?,reason=?,analysis=?,status=CASE WHEN status='error' THEN status ELSE 'indexed' END WHERE id=?",
                                    (category, reason + text, json.dumps(analysis, ensure_ascii=True), book_id))
            self.connection.execute("UPDATE semantic_members SET category=? WHERE book_id=?", (category, book_id))
            changes.append({"id": book_id, "title": row[0], "from": row[1], "to": category, "reason": reason.strip()})
        return changes

    def _rename(self, rows, assignment, names, members_of, books, fixed_themes, contents):
        """Group names by c-TF-IDF over shared structural n-grams; families by subject."""
        final = defaultdict(list)
        seen_contents = defaultdict(set)
        for book_id, row in sorted(books.items()):
            category = row[3] or assignment.get(book_id, (row[1], ""))[0]
            if row[2] in seen_contents[category]:
                continue
            seen_contents[category].add(row[2])
            final[category].append(book_id)
        profiles, blocked, kinds = {}, set(), defaultdict(Counter)
        for book_id, terms, author, topic_json, kind, title in self.connection.execute("""
                SELECT b.id, COALESCE(p.terms,''), b.author, b.analysis, COALESCE(p.kind,''), b.title
                FROM books b LEFT JOIN semantic_profiles p ON p.book_id=b.id WHERE b.missing=0 AND b.status!='error'"""):
            if terms and terms != "{}":
                profiles[book_id] = json.loads(terms)
                profiles[book_id].setdefault("title", json.loads(topic_json or "{}").get("structure", {}).get("title") or title)
            else:
                topic = json.loads(topic_json or "{}").get("topic", {})
                profiles[book_id] = cat.profile_terms([topic.get("label", "")], topic, "")
            blocked.update(word for word in fold(author or "").split() if len(word) > 2)
            kinds[book_id] = kind
        theme_groups = {name: ids for name, ids in final.items() if _theme_number(name)}
        background = {name: ids for name, ids in final.items() if not _theme_number(name) and name not in NOT_SUBJECTS}
        named = cat.name_groups(theme_groups, profiles, background, blocked)
        if hasattr(self.encoder, "encode_texts"):
            np = numpy_module()
            centroids = {}
            for name, ids in theme_groups.items():
                vectors = [self._vector(books[book_id][2]) for book_id in ids if book_id in books]
                vectors = [vector for vector in vectors if vector is not None]
                if vectors:
                    centroids[name] = normalize(np.mean(np.stack(vectors), axis=0))
            try:
                named = cat.rerank(np, named, centroids, self.encoder.encode_texts)
            except (SemanticUnavailable, ValueError, OSError):
                pass  # keep the c-TF-IDF names
        used = Counter()
        for name in sorted(theme_groups, key=lambda item: (-len(theme_groups[item]), _theme_number(item))):
            info = named[name]
            label = info["label"]
            if not label:
                topics = Counter()
                for book_id in theme_groups[name]:
                    row = self.connection.execute("SELECT analysis FROM books WHERE id=?", (book_id,)).fetchone()
                    topic_label = json.loads(row[0] or "{}").get("topic", {}).get("label", "")
                    if topic_label and not cat.is_noise(fold(topic_label), topic_label, blocked):
                        topics[topic_label] += 1
                label = topics.most_common(1)[0][0] if topics else ""
            if label and used[fold(label)]:
                extra = next((term for term in info["terms"] if term not in fold(label)), "")
                label = f"{label} ({extra})" if extra else label
            used[fold(label)] += 1
            votes = Counter(books[book_id][6] for book_id in theme_groups[name] if book_id in books and books[book_id][6] in TAXONOMY)
            family = cat.family_for(label, info["terms"], votes, Counter(kinds[book_id] for book_id in theme_groups[name]),
                                    SHELF_FAMILIES)
            self.connection.execute("UPDATE semantic_themes SET label=?,label_version=?,family=?,retired=0 WHERE number=?",
                                    (label, cat.CATEGORIES_VERSION + ":group", family, _theme_number(name)))
        referenced = {row[0] for row in self.connection.execute("SELECT category FROM books UNION SELECT manual_category FROM books")}
        for name in themes(self.connection):
            if name not in referenced:
                self.connection.execute("UPDATE semantic_themes SET retired=1 WHERE number=?", (_theme_number(name),))
