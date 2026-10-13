"""Explainable, offline classification of text extracted from mathematical PDFs.

Confidence is a heuristic estimate, not a calibrated probability. The title may
reinforce evidence in the extracted text but cannot create that evidence. No
document text is sent to a service, and no optional Python package is required.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
import unicodedata

try:
    from .taxonomy import TAXONOMY, EXTRA_CONCEPTS
except ImportError:
    from taxonomy import TAXONOMY, EXTRA_CONCEPTS


CATEGORIES = [*TAXONOMY, "Outros assuntos", "Revisar"]
CLASSIFIER_VERSION = "atlas-subjects-3.0"

# Stopwords from Portuguese, English and Spanish, plus PDF publication noise.
# Mathematical words (including theorem, proof and function) are retained.
_STOPWORDS = set("""
a ao aos aquela aquelas aquele aqueles aquilo as ate com como da das de dela
delas dele deles depois do dos e ela elas ele eles em entre era eram essa essas
esse esses esta estas este estes eu foi foram ha isso isto ja lhe lhes mais mas
me mesmo meu meus minha minhas muito na nas nao nem no nos nossa nossas nosso
nossos num numa o os ou para pela pelas pelo pelos por porque qual quais quando
que quem se sem seu seus sua suas sao ser sera sobre so sua suas tambem te tem
tendo ter teve tinha todo todos toda todas tu um uma umas uns voce voces vos
the a an and are as at be been being but by can could did do does doing don
each for from had has have having he her here hers herself him himself his how
i if in into is it its itself just may might more most much must my myself no
nor not of on once only or other our ours ourselves out over own same she should
so some such than that their theirs them themselves then there these they this
those through to too under until up us very was we were what when where which
while who whom why will with would you your yours yourself yourselves t s ll re
el la los las lo un una unos unas al del de y o u en con sin por para que quien
quienes cual cuales cuando donde porque pero aunque como muy mas menos ya aun
tambien ni si su sus se le les me te nos os mi mis tu tus nuestro nuestros
nuestra nuestras vuestro vuestra este esta estos estas ese esa esos esas aquel
aquella aquellos aquellas esto eso aquello hay ha han es son era eran ser estar
esta estan estaba estaban fue fueron sea sean sera seran tiene tienen tener
todo todos toda todas otro otros otra otras cada desde hasta durante sobre
copyright rights reserved isbn edition editions editora publisher published
pagina paginas page pages chapter chapters capitulo capitulos fig figure figures
www http https org com pdf vol volume contents indice contenido acknowledgments
preface prefacio introduction introducao introduccion university universidade
universidad press author autores author authors appendix apendice table tabla
use used using book libro livro section sections secao secoes example examples
exemplo exemplos ejemplo ejemplos exercise exercises exercicio exercicios
ejercicio ejercicios see given let also following note show thus therefore
here first second new one two three well shall caso tanto ainda pode podem
""".split())

# A concept's aliases count as one distinct signal. Several translations of
# "matrix" therefore do not falsely become several independent math signals.
# Weights favor distinctive phrases over words shared across disciplines.
_LEXICON: dict[str, list[tuple[float, str]]] = {
    "Cálculo": [
        (5, "calculo diferencial|differential calculus|calculo integral|integral calculus"),
        (3, "derivada|derivadas|derivative|derivatives|derivacion|diferenciacao|differentiation"),
        (3, "integral|integrais|integrals|integracion|integration|integracao"),
        (4, "regra da cadeia|chain rule|regla de la cadena"),
        (4, "teorema fundamental do calculo|fundamental theorem of calculus|teorema fundamental del calculo"),
        (4, "integracao por partes|integration by parts|integracion por partes"),
        (3, "limite de uma funcao|limit of a function|limite de una funcion"),
        (4, "serie de taylor|taylor series|series de taylor"),
        (4, "derivada parcial|partial derivative|derivadas parciais|derivadas parciales"),
        (4, "integral multipla|multiple integral|integrales multiples"),
        (3, "antiderivative|antiderivada|primitiva de uma funcao"),
    ],
    "Álgebra linear": [
        (5, "algebra linear|linear algebra|algebra lineal"),
        (3, "matriz|matrizes|matrix|matrices"),
        (4, "espaco vetorial|vector space|espacios vectoriales|espacos vetoriais|espacio vectorial"),
        (5, "autovalor|autovalores|eigenvalue|eigenvalues|valor propio|valores propios"),
        (5, "autovetor|autovetores|eigenvector|eigenvectors|vector propio|vectores propios"),
        (4, "transformacao linear|linear transformation|transformacion lineal"),
        (4, "independencia linear|linear independence|independencia lineal"),
        (4, "produto interno|inner product|producto interno"),
        (3, "determinante|determinantes|determinant|determinants"),
        (4, "diagonalizacao|diagonalization|diagonalizacion"),
        (4, "decomposicao em valores singulares|singular value decomposition"),
        (3, "sistema linear|linear system|sistemas lineares|sistemas lineales"),
    ],
    "Álgebra abstrata": [
        (5, "algebra abstrata|abstract algebra|algebra abstracta"),
        (5, "teoria de grupos|group theory|teoria de los grupos"),
        (4, "homomorfismo|homomorfismos|homomorphism|homomorphisms"),
        (4, "isomorfismo|isomorfismos|isomorphism|isomorphisms"),
        (5, "anel comutativo|commutative ring|anillo conmutativo"),
        (4, "subgrupo|subgrupos|subgroup|subgroups"),
        (5, "teoria de galois|galois theory"),
        (4, "ideal primo|prime ideal|ideal maximal|maximal ideal"),
        (4, "extensao de corpos|field extension|extension de cuerpos"),
        (5, "grupo abeliano|abelian group|grupo ciclico|cyclic group"),
        (4, "anel|aneis|anillo|anillos|rings"),
    ],
    "Geometria": [
        (5, "geometria euclidiana|euclidean geometry|geometria analitica|analytic geometry"),
        (4, "geometria diferencial|differential geometry|geometria diferencial"),
        (3, "triangulo|triangulos|triangle|triangles"),
        (3, "circunferencia|circumference|circunferencias"),
        (3, "poligono|poligonos|polygon|polygons"),
        (4, "curvatura|curvature"),
        (4, "variedade diferenciavel|differentiable manifold|variedad diferenciable"),
        (4, "teorema de pitagoras|pythagorean theorem|teorema de pitagoras"),
        (4, "secoes conicas|conic sections|secciones conicas"),
        (4, "retas paralelas|parallel lines|rectas paralelas"),
        (4, "geodesica|geodesic|geodesicas|geodesics"),
        (3, "angulos|angles|angulos internos|interior angles"),
    ],
    "Topologia": [
        (5, "topologia|topology"),
        (5, "espaco topologico|topological space|espacio topologico"),
        (5, "homeomorfismo|homeomorphism|homeomorfismos|homeomorphisms"),
        (4, "homotopia|homotopy|homotopias"),
        (5, "grupo fundamental|fundamental group"),
        (4, "conjunto aberto|open set|conjunto abierto|conjuntos abertos|open sets"),
        (4, "compactness|compacidade|compacidad"),
        (4, "connectedness|conexidade|conexidad"),
        (5, "homologia|homology|cohomologia|cohomology"),
        (4, "espaco de hausdorff|hausdorff space|espacio de hausdorff"),
    ],
    "Análise real": [
        (5, "analise real|real analysis|analisis real"),
        (5, "teoria da medida|measure theory|teoria de la medida"),
        (5, "integral de lebesgue|lebesgue integral|integracao de lebesgue|lebesgue integration"),
        (4, "sequencia de cauchy|cauchy sequence|sucessao de cauchy|sucesion de cauchy"),
        (4, "convergencia uniforme|uniform convergence"),
        (4, "supremo|supremum|infimo|infimum"),
        (4, "espaco metrico|metric space|espacio metrico"),
        (4, "mensuravel|measurable|medible"),
        (5, "teorema da convergencia dominada|dominated convergence theorem|teorema de convergencia dominada"),
        (4, "epsilon delta|sequencias convergentes|convergent sequences"),
        (4, "analise funcional|functional analysis|analisis funcional"),
    ],
    "Análise complexa": [
        (5, "analise complexa|complex analysis|analisis complejo|analisis compleja"),
        (5, "holomorfa|holomorphic|holomorfas|holomorfo"),
        (5, "teorema dos residuos|residue theorem|teorema de los residuos"),
        (5, "equacoes de cauchy riemann|cauchy riemann equations|ecuaciones de cauchy riemann"),
        (4, "integral de contorno|contour integral|integrales de contorno"),
        (4, "continuacao analitica|analytic continuation|continuacion analitica"),
        (4, "serie de laurent|laurent series|series de laurent"),
        (4, "funcao meromorfa|meromorphic function|funcion meromorfa"),
        (4, "mapeamento conforme|conformal mapping|aplicacion conforme"),
        (3, "plano complexo|complex plane|plano complejo"),
    ],
    "Equações diferenciais": [
        (5, "equacao diferencial|equacoes diferenciais|differential equation|differential equations|ecuacion diferencial|ecuaciones diferenciales"),
        (5, "problema de valor inicial|initial value problem|problema de valor inicial"),
        (4, "condicao de contorno|boundary condition|condiciones de frontera|condicoes de contorno"),
        (4, "separacao de variaveis|separation of variables|separacion de variables"),
        (4, "transformada de laplace|laplace transform"),
        (4, "equacao do calor|heat equation|ecuacion del calor"),
        (4, "equacao da onda|wave equation|ecuacion de onda"),
        (4, "equacao de poisson|poisson equation|ecuacion de poisson"),
        (3, "solucao geral|general solution|solucion general"),
        (4, "sistema dinamico|dynamical system|sistemas dinamicos"),
    ],
    "Probabilidade": [
        (5, "probabilidade|probability|probabilidad"),
        (5, "variavel aleatoria|random variable|variable aleatoria|variaveis aleatorias|random variables|variables aleatorias"),
        (5, "probabilidade condicional|conditional probability|probabilidad condicional"),
        (4, "teorema de bayes|bayes theorem|bayes rule|regra de bayes"),
        (4, "espaco amostral|sample space|espacio muestral"),
        (4, "esperanca matematica|expected value|esperanza matematica"),
        (4, "lei dos grandes numeros|law of large numbers|ley de los grandes numeros"),
        (4, "cadeia de markov|markov chain|cadenas de markov|cadeias de markov"),
        (4, "processo estocastico|stochastic process|proceso estocastico"),
        (3, "distribuicao binomial|binomial distribution|distribucion binomial"),
    ],
    "Estatística": [
        (5, "estatistica|statistics|estadistica"),
        (5, "inferencia estatistica|statistical inference|inferencia estadistica"),
        (5, "teste de hipotese|hypothesis test|prueba de hipotesis|testes de hipoteses|hypothesis testing"),
        (5, "intervalo de confianca|confidence interval|intervalo de confianza"),
        (4, "regressao|regression|regresion"),
        (4, "amostragem|sampling|muestreo"),
        (4, "estimador|estimators|estimator|estimadores"),
        (4, "analise de variancia|analysis of variance|analisis de varianza"),
        (3, "desvio padrao|standard deviation|desviacion estandar"),
        (3, "mediana|median|quartil|quartile|cuartil"),
        (4, "maxima verossimilhanca|maximum likelihood|maxima verosimilitud"),
    ],
    "Matemática discreta": [
        (5, "matematica discreta|discrete mathematics|matematicas discretas"),
        (5, "teoria dos grafos|graph theory|teoria de grafos"),
        (4, "combinatoria|combinatorics|combinatorial"),
        (4, "permutacao|permutacoes|permutation|permutations|permutaciones"),
        (4, "relacao de recorrencia|recurrence relation|relacion de recurrencia"),
        (4, "principio da casa dos pombos|pigeonhole principle|principio del palomar"),
        (4, "algebra booleana|boolean algebra|algebra de boole"),
        (4, "caminho euleriano|eulerian path|camino euleriano"),
        (4, "arvore geradora|spanning tree|arbol generador"),
        (3, "grafos|graphs|vertices|arestas|edges|aristas"),
        (4, "logica proposicional|propositional logic|logica proposicional"),
    ],
    "Teoria dos números": [
        (5, "teoria dos numeros|number theory|teoria de numeros"),
        (4, "numero primo|prime number|numero primo|numeros primos|prime numbers"),
        (4, "aritmetica modular|modular arithmetic"),
        (4, "congruencia|congruencias|congruence|congruences"),
        (4, "equacao diofantina|diophantine equation|ecuacion diofantica"),
        (4, "divisibilidade|divisibility|divisibilidad"),
        (5, "reciprocidade quadratica|quadratic reciprocity|reciprocidad cuadratica"),
        (4, "funcao zeta|zeta function|funcion zeta"),
        (4, "pequeno teorema de fermat|fermat little theorem|fermat s little theorem"),
        (3, "maximo divisor comum|greatest common divisor|maximo comun divisor"),
    ],
    "Otimização": [
        (5, "otimizacao|optimization|optimisation|optimizacion"),
        (5, "programacao linear|linear programming|programacion lineal"),
        (5, "programacao inteira|integer programming|programacion entera"),
        (4, "funcao objetivo|objective function|funcion objetivo"),
        (4, "regiao viavel|feasible region|region factible"),
        (4, "simplex|simplex method|metodo simplex"),
        (4, "descida de gradiente|gradient descent|descenso de gradiente"),
        (5, "karush kuhn tucker|kkt conditions|condicoes kkt"),
        (4, "dualidade|duality|dualidad"),
        (5, "otimizacao convexa|convex optimization|optimizacion convexa"),
    ],
    "Métodos numéricos": [
        (5, "metodos numericos|numerical methods|analise numerica|numerical analysis|analisis numerico"),
        (5, "runge kutta|metodo de runge kutta|runge kutta method"),
        (4, "interpolacao|interpolation|interpolacion"),
        (4, "quadratura|quadrature|cuadratura"),
        (4, "metodo de newton|newton method|newton raphson"),
        (4, "diferencas finitas|finite differences|diferencias finitas"),
        (4, "erro de truncamento|truncation error|error de truncamiento"),
        (4, "ponto flutuante|floating point|coma flotante"),
        (4, "metodo da bissecao|bisection method|metodo de biseccion"),
        (4, "estabilidade numerica|numerical stability|estabilidad numerica"),
    ],
    "Matemática básica": [
        (5, "matematica basica|basic mathematics|matematicas basicas|aritmetica|arithmetic"),
        (3, "fracao|fracoes|fraction|fractions|fracciones"),
        (3, "adicao|addition|suma"),
        (3, "subtracao|subtraction|resta"),
        (3, "multiplicacao|multiplication|multiplicacion"),
        (3, "divisao|division"),
        (3, "porcentagem|percentagem|percentage|porcentaje"),
        (4, "equacao do segundo grau|quadratic equation|ecuacion cuadratica"),
        (4, "numeros naturais|natural numbers|numeros naturales"),
        (3, "potenciacao|exponentiation|potenciacion"),
        (3, "trigonometria|trigonometry"),
    ],
}

# Specialized subjects have their own shelves, instead of inheriting every
# signal of a broad parent. Linear-algebra tools alone are deliberately weak.
_LEXICON.update(EXTRA_CONCEPTS)
_LEXICON["Cálculo"].append((6, "calculus|calculo|calcul infinitesimal"))
_LEXICON["Álgebra linear"] = [
    ({1: 1.8, 3: 4.5, 4: 4.5, 8: 2.5, 11: 3}.get(index, weight), aliases)
    for index, (weight, aliases) in enumerate(_LEXICON["Álgebra linear"])
]
_LEXICON["Geometria"] = [item for index, item in enumerate(_LEXICON["Geometria"]) if index not in (1, 5, 6, 10)]
_LEXICON["Análise real"] = [item for index, item in enumerate(_LEXICON["Análise real"]) if index not in (1, 2, 7, 8, 10)]
_LEXICON["Equações diferenciais"] = [item for index, item in enumerate(_LEXICON["Equações diferenciais"]) if index != 9]
_LEXICON["Matemática discreta"] = [
    (8, "matematica discreta|discrete mathematics|discrete math|matematicas discretas"),
    (4, "recurrence relation|relacao de recorrencia|relacoes de recorrencia"),
    (4, "mathematical induction|inducao matematica|strong induction"),
    (4, "equivalence relation|relacao de equivalencia|binary relation|relacao binaria"),
    (4, "boolean algebra|algebra booleana|boolean functions"),
    (4, "discrete structures|estruturas discretas|finite sets|conjuntos finitos"),
    (4, "partial order|ordem parcial|partially ordered set"),
]
_LEXICON["Métodos numéricos"].extend([
    (7, "calculo numerico|numerical calculus|computacao numerica"),
    (7, "numerical linear algebra|scientific computing"),
    (5, "iterative methods|metodos iterativos|condition number|numero de condicionamento"),
])
_LEXICON["Análise real"].extend([
    (7, "analise matematica|mathematical analysis|curso de analise|introducao a analise|analise no espaco"),
    (5, "sequencias de cauchy|sucessoes de cauchy|cauchy criterion|criterio de cauchy"),
    (5, "bolzano weierstrass|heine borel"),
    (5, "uniform continuity|continuidade uniforme|uniformly continuous"),
    (4, "pointwise convergence|convergencia pontual|convergencia simples"),
    (5, "completeness of the real|complete ordered field|completude dos reais|axioma do supremo"),
    (4, "bounded sequence|monotone sequence|sequencia limitada|sequencia monotona"),
    (4, "real numbers|real line|numeros reais|reta real"),
    (5, "limsup|liminf|limit superior|limite superior|limite inferior"),
    (5, "riemann integrable|integravel a riemann|riemann integrability|darboux sums"),
])

_NONMATH = tuple((
    "receita culinaria|recipe|cozinha|cooking|ingredientes|ingredients|forno|oven|"
    "medicina|medicine|paciente|pacientes|patient|patients|tratamento|treatment|"
    "diagnostico|diagnosis|hospital|doenca|disease|sintomas|symptoms|"
    "romance|poesia|poetry|literatura|literature|personagem|character|"
    "eleicao|election|governo|government|parlamento|parliament|"
    "biologia|biology|celulas|cells|especies|species|"
    "contrato|contract|legislacao|legislation|juridico|judicial|"
    "fotografia|photography|camera|cinema|movie|"
    "turismo|tourism|hotel|viagem|travel|"
    "mercado financeiro|financial market|investimento|investment|acoes|stocks"
).split("|"))
_NONMATH_SET = set(_NONMATH)

_TOKEN = re.compile(r"[a-z]+")
_BROKEN_LINE = re.compile(r"(?<=\w)-\s*\n\s*(?=\w)")
_ALIASES: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
_PHRASES: dict[str, set[int]] = defaultdict(set)
_INFLECTIONS = dict(pair.split(":") for pair in (
    "manifold:manifolds metric:metrics bundle:bundles derivative:derivatives operator:operators "
    "relation:relations coefficient:coefficients function:functions number:numbers theorem:theorems "
    "equation:equations algorithm:algorithms tree:trees graph:graphs network:networks vector:vectors "
    "matrix:matrices transformation:transformations sequence:sequences integral:integrals path:paths "
    "cycle:cycles space:spaces group:groups set:sets form:forms condition:conditions map:maps "
    "attractor:attractors variable:variables axiom:axioms exponent:exponents measure:measures partition:partitions"
).split())
for _category, _concepts in _LEXICON.items():
    for _concept, (_weight, _variants) in enumerate(_concepts):
        _variants_set = set(_variants.split("|"))
        for _alias in list(_variants_set):
            _parts = _alias.split()
            if _parts[-1] in _INFLECTIONS:
                _variants_set.add(" ".join([*_parts[:-1], _INFLECTIONS[_parts[-1]]]))
        for _alias in _variants_set:
            _ALIASES[_alias].append((_category, _concept, _weight))
            _parts = _alias.split()
            if len(_parts) > 1:
                _PHRASES[_parts[0]].add(len(_parts))
for _alias in _NONMATH:
    _parts = _alias.split()
    if len(_parts) > 1:
        _PHRASES[_parts[0]].add(len(_parts))


for _family, _msc, _focus_aliases in TAXONOMY.values():
    for _alias in _focus_aliases.split("|"):
        _parts = _alias.split()
        if len(_parts) > 1:
            _PHRASES[_parts[0]].add(len(_parts))
_FOCUS_ALIASES = {alias for _family, _msc, aliases in TAXONOMY.values() for alias in aliases.split("|")}


def _tokens(text: str) -> list[str]:
    # Repair hyphenation at PDF line boundaries before accent normalization.
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z])(?=[A-Z])", " ", text)
    normalized = unicodedata.normalize("NFKD", _BROKEN_LINE.sub("", text))
    normalized = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return _TOKEN.findall(normalized)


def _counts(tokens: list[str]) -> Counter:
    counts = Counter(tokens)
    # Only expand phrase prefixes found in the vocabulary, not every n-gram.
    for offset, word in enumerate(tokens):
        for size in _PHRASES.get(word, ()):
            if offset + size <= len(tokens):
                phrase = " ".join(tokens[offset:offset + size])
                if phrase in _ALIASES or phrase in _NONMATH_SET or phrase in _FOCUS_ALIASES:
                    counts[phrase] += 1
    return counts


def _evidence(counts: Counter) -> tuple[dict, dict, dict]:
    concept_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    weights: dict[tuple[str, int], float] = {}
    examples: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for term, count in counts.items():
        for category, concept, weight in _ALIASES.get(term, ()):
            concept_counts[category][concept] += count
            weights[(category, concept)] = weight
            examples[category].append((weight, term))
    scores = {}
    for category, concepts in concept_counts.items():
        # BM25-style term-frequency saturation; translations share a concept.
        # Normalize dictionary sizes so adding aliases cannot favor one shelf.
        scores[category] = sum(
            weights[(category, concept)] * (2.2 * count / (count + 1.2))
            for concept, count in concepts.items()
        ) / math.sqrt(max(1, len(_LEXICON[category]) / 12))
    return scores, concept_counts, examples


def _focus(counts: Counter, concepts: dict) -> set[str]:
    matches = []
    for category, (_family, _msc, aliases) in TAXONOMY.items():
        if len(concepts.get(category, {})) < 2:
            continue
        for alias in aliases.split("|"):
            if counts[alias]:
                matches.append((category, alias))
    # A specific title such as "graph neural networks" takes precedence over
    # its embedded generic "neural networks", but unrelated subjects coexist.
    return {category for category, alias in matches
            if not any(category != other and " " + alias + " " in " " + longer + " "
                       and len(longer) > len(alias) for other, longer in matches)}


def _section_support(text: str, tokens: list[str]) -> tuple[Counter, int]:
    pages = [page for page in text.split("\f") if page.strip()]
    if len(pages) > 1:
        units = [_tokens(pages[index]) for index in sorted({round(i * (len(pages) - 1) / min(31, len(pages) - 1))
                                                         for i in range(min(32, len(pages)))})]
    else:
        starts = list(range(0, len(tokens), 900)) or [0]
        chosen = sorted({round(i * (len(starts) - 1) / min(31, len(starts) - 1))
                         for i in range(min(32, len(starts)))}) if len(starts) > 1 else [0]
        units = [tokens[starts[index]:starts[index] + 900] for index in chosen]
    support = Counter()
    for unit in units:
        _scores, concepts, _examples = _evidence(_counts(unit))
        support.update(category for category, matches in concepts.items() if len(matches) >= 2)
    return support, max(1, len(units))


def classify(text: str, title: str = "", filename: str = "") -> dict:
    """Classify subject rather than mathematical tools used in the subject.

    Combine saturated concept evidence, distinct signals, distributed content,
    headings and corroborated title/filename. Never infer content from a folder
    or a misleading title. Scores/confidence are heuristic, not probabilities.
    """
    text = text or ""
    # Bibliographies contribute mentions of other subjects rather than teaching
    # evidence. Trim a final references section, without discarding earlier text.
    for match in re.finditer(r"(?im)^\s*(?:references|bibliography|referencias|bibliografia)\s*$", text):
        if match.start() > len(text) * .65:
            text = text[:match.start()]
            break
    tokens = _tokens(text)
    frequencies = Counter(token for token in tokens if 2 < len(token) < 60 and token not in _STOPWORDS)
    keywords = [{"word": word, "count": count}
                for word, count in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:20]]
    analysis = {"version": CLASSIFIER_VERSION, "method": "Conceitos + estrutura + amostragem distribuída",
                "candidates": [], "secondary": [], "sections": 0, "decision": "insufficient"}
    result = {"category": "Revisar", "confidence": 0.0, "keywords": keywords,
              "reason": "Sem texto extraível suficiente; o PDF pode precisar de OCR.", "scores": {}, "analysis": analysis}
    if not tokens:
        return result
    counts = _counts(tokens)
    scores, concepts, examples = _evidence(counts)
    # Anchor phrases also enter the n-gram vocabulary, see registration below.
    title_counts = _counts(_tokens(title))
    filename_counts = _counts(_tokens(filename))
    # A survey can have several well-supported child subjects without repeating
    # broad vocabulary. Recognize that breadth only with an explicit book title.
    surveys = {"Matemática discreta": ("Teoria dos grafos", "Combinatória", "Lógica matemática"),
               "Análise real": ("Teoria da medida", "Análise funcional")}
    for parent, children in surveys.items():
        aliases = TAXONOMY[parent][2].split("|")
        if not any(title_counts[alias] or filename_counts[alias] for alias in aliases):
            continue
        supported = [child for child in children if len(concepts.get(child, {})) >= 3 and scores.get(child, 0) >= 12]
        if len(supported) >= 2:
            parent_concepts = concepts.setdefault(parent, {})
            for index, child in enumerate(supported):
                parent_concepts[1000 + index] = 1
                examples[parent].append((4, "panorama de " + child.lower()))
            scores[parent] = scores.get(parent, 0) + sum(sorted((scores[child] for child in supported), reverse=True)[:2]) * .75
    title_focus = _focus(title_counts, concepts)
    if not title_focus:
        title_focus = _focus(filename_counts, concepts)
    support, sections = _section_support(text, tokens)
    analysis["sections"] = sections
    headings = " ".join(line for line in text.splitlines()
                        if 5 < len(line.strip()) < 150 and
                        (re.match(r"^\s*(?:chapter|capitulo|section|secao|\d{1,2}(?:\.\d{1,2}){0,2})[. :\t]+", line, re.I)
                         or re.search(r"\.{3,}\s*\d+\s*$", line)))[:24000]
    heading_scores, _heading_concepts, heading_examples = _evidence(_counts(_tokens(headings)))
    title_contributions = {}
    for category in scores:
        scores[category] += 6 * support[category] / sections
        scores[category] += min(12, heading_scores.get(category, 0) * .3)
        if category in title_focus:
            title_contributions[category] = min(45, 10 + scores[category] * .65)
            scores[category] += title_contributions[category]
    focus = set(title_focus)
    if not focus and scores:
        best_score = max(scores.values())
        body_focus = _focus(counts, concepts)
        focus = {category for category in body_focus
                 if len(concepts[category]) >= 3 and scores[category] >= max(18, best_score * .75)}
    # An application can teach/use eigenvalues without being a linear-algebra
    # textbook. Attenuate tools only when a different subject is corroborated.
    tool_penalties = {}
    tools = {
        "Teoria dos grafos": {"Álgebra linear": .48},
        "Redes neurais em grafos": {"Álgebra linear": .38, "Aprendizado de máquina": .65, "Teoria dos grafos": .72, "Estatística": .65, "Otimização": .65},
        "Aprendizado de máquina": {"Álgebra linear": .48, "Estatística": .68, "Otimização": .68, "Cálculo": .6},
        "Análise real": {"Cálculo": .52},
        "Métodos numéricos": {"Álgebra linear": .55, "Cálculo": .7},
        "Análise funcional": {"Álgebra linear": .55, "Análise real": .7},
        "Geometria diferencial": {"Álgebra linear": .6, "Geometria": .65},
        "Teoria da medida": {"Análise real": .65},
        "Sistemas dinâmicos": {"Equações diferenciais": .65},
    }
    for subject in focus:
        for tool, multiplier in tools.get(subject, {}).items():
            if tool in scores and tool not in title_focus:
                tool_penalties[tool] = min(tool_penalties.get(tool, 1), multiplier)
    for category, multiplier in tool_penalties.items():
        scores[category] *= multiplier
    ranking = sorted(scores, key=lambda category: (-scores[category], category))
    result["scores"] = {category: round(scores[category], 2) for category in ranking}
    best = ranking[0] if ranking else None
    top = scores.get(best, 0)
    second = scores[ranking[1]] if len(ranking) > 1 else 0
    for category in ranking[:5]:
        terms = sorted(set(examples[category]), key=lambda item: (-item[0], -counts[item[1]], item[1]))
        family, msc, _ = TAXONOMY[category]
        analysis["candidates"].append({
            "category": category, "family": family, "msc": msc, "score": round(scores[category], 2),
            "signals": len(concepts[category]), "sections": support[category],
            "terms": [term for _weight, term in terms[:8]],
            "title_match": category in title_focus,
            "heading_terms": sorted({term for _weight, term in heading_examples.get(category, [])})[:5],
            "role": "ferramenta de apoio" if category in tool_penalties else "assunto",
        })
    if len(tokens) < 24 or len(frequencies) < 9:
        result.update(confidence=.15, reason="Texto muito curto ou repetitivo para decidir uma área com segurança.")
        return result
    nonmath = [(term, counts[term]) for term in _NONMATH if counts[term]]
    if top < 8 or (best and len(concepts[best]) < 2):
        if len(nonmath) >= 2 or (len(tokens) >= 65 and not concepts):
            result.update(category="Outros assuntos", confidence=min(.88, .57 + len(nonmath) * .035),
                          reason="O texto sugere outro assunto: " + ", ".join(term for term, _ in nonmath[:4]) + "." if nonmath else
                          "Texto sem evidências distintivas das áreas disponíveis.")
            analysis["decision"] = "other"
        else:
            result.update(confidence=.3, reason="Poucos conceitos matemáticos distintos; é necessária revisão do conteúdo.")
        return result
    if (second >= 8 and top / second < 1.22 and
            not (len(title_focus) == 1 and best in title_focus and top / second >= 1.05)):
        result.update(confidence=.4, reason=f"Conteúdo ambíguo entre {best} e {ranking[1]}; evidências próximas. Consulte os assuntos sugeridos.")
        analysis["decision"] = "ambiguous"
        return result
    if len(nonmath) >= 4 and top < 13:
        result.update(confidence=.35, reason="Há sinais matemáticos e de outros assuntos, sem predominância suficiente.")
        return result
    distinct = len(concepts[best])
    margin = (top - second) / top
    confidence = .48 + .23 * margin + .035 * min(distinct, 6) + .035 * min(top / 15, 2)
    analysis.update(decision="classified", family=TAXONOMY[best][0], msc=TAXONOMY[best][1])
    analysis["secondary"] = [entry for entry in analysis["candidates"][1:]
                             if entry["score"] >= top * .23 and entry["signals"] >= 2][:3]
    terms = analysis["candidates"][0]["terms"][:5]
    basis = "Título corroborado pelo conteúdo. " if best in title_focus else "Evidência no conteúdo. "
    explanation = basis + "Conceitos: " + ", ".join(terms) + ". "
    if sections > 1:
        explanation += f"Presença em {support[best]} de {sections} trechos avaliados. "
    if tool_penalties:
        explanation += "Ferramentas de apoio separadas do tema: " + ", ".join(tool_penalties) + ". "
    result.update(category=best, confidence=round(min(.95, max(.5, confidence)), 3),
                  reason=explanation + "Estimativa heurística, sem calibração estatística.")
    return result
