from __future__ import annotations

from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(Path.cwd() / ".env", override=False)

# =============================================================================
# Métadonnées du site
# =============================================================================

DISCIPLINE = "mathématiques"
NIVEAU = "Terminale générale"
ENSEIGNEMENT = "Spécialité mathématiques"

SITE_TITLE = "Révisions bac mathématiques"
SITE_SUBTITLE = "Terminale générale — spécialité mathématiques"

PROGRAMME_VERSION = os.getenv("PROGRAMME_VERSION", "2019")
BAC_TARGET = os.getenv("BAC_TARGET", "bac_2026_2027")

# =============================================================================
# Arborescence
# =============================================================================

SITE_ROOT = Path(os.getenv("BAC_SITE_ROOT", PROJECT_ROOT / "site"))

PDF_DIR = SITE_ROOT / "pdf"
IMG_DIR = SITE_ROOT / "img"
DATA_DIR = SITE_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PAGES_DIR = RAW_DIR / "pages"
INTERMEDIATE_DIR = DATA_DIR / "intermediate"
GENERATED_DIR = DATA_DIR / "generated"
REPORTS_DIR = DATA_DIR / "rapports"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
PROGRAMME_DIR = SITE_ROOT / "programme"

# =============================================================================
# Fichiers de données
# =============================================================================

MANIFEST_CSV = DATA_DIR / "manifest.csv"
MANIFEST_JSON = DATA_DIR / "manifest.json"
PROGRAMME_JSON = DATA_DIR / "programme.json"
PROGRAMME_PDF_PATH = Path(
    os.getenv(
        "PROGRAMME_PDF_PATH",
        PROGRAMME_DIR / "terminale-specialite-mathematiques-2019.pdf",
    )
)
PROGRAMME_OFFICIEL_RAW_JSON = DATA_DIR / "programme_officiel_raw.json"
PROGRAMME_OFFICIEL_JSON = DATA_DIR / "programme_officiel.json"
PROGRAMME_COVERAGE_JSON = REPORTS_DIR / "programme_coverage.json"
EXERCISES_RAW_JSON = INTERMEDIATE_DIR / "exercises_raw.json"
EXERCISES_JSON = DATA_DIR / "exercices.json"
COURSES_JSON = DATA_DIR / "cours.json"
QUIZ_JSON = DATA_DIR / "quiz.json"
DATA_JSON = DATA_DIR / "data.json"

# =============================================================================
# Domaines officiels du programme
# =============================================================================

PROGRAMME_DOMAINS = {
    "algebre-geometrie": "Algèbre et géométrie",
    "analyse": "Analyse",
    "probabilites": "Probabilités",
    "algorithmique-programmation": "Algorithmique et programmation",
    "logique-ensembles": "Vocabulaire ensembliste et logique",
}

DOMAIN_ORDER = [
    "algebre-geometrie",
    "analyse",
    "probabilites",
    "algorithmique-programmation",
    "logique-ensembles",
]

# =============================================================================
# Thèmes de révision du site
# =============================================================================

ALLOWED_THEMES = {
    "combinatoire-denombrement": "Combinatoire et dénombrement",
    "geometrie-vecteurs-espace": "Vecteurs, droites et plans de l’espace",
    "geometrie-orthogonalite-distances": "Orthogonalité et distances dans l’espace",
    "geometrie-reperage": "Représentations paramétriques et équations cartésiennes",
    "suites": "Suites",
    "limites-fonctions": "Limites de fonctions",
    "derivation-convexite": "Dérivation et convexité",
    "continuite": "Continuité et théorème des valeurs intermédiaires",
    "logarithme": "Fonction logarithme",
    "trigonometrie": "Fonctions sinus et cosinus",
    "primitives-equations-differentielles": "Primitives et équations différentielles",
    "calcul-integral": "Calcul intégral",
    "bernoulli-binomiale": "Schéma de Bernoulli et loi binomiale",
    "variables-aleatoires": "Sommes de variables aléatoires",
    "concentration-grands-nombres": "Concentration et loi des grands nombres",
    "algorithmique-python": "Algorithmique et Python",
    "logique-raisonnement": "Logique, ensembles et raisonnement",
}

THEME_ORDER = list(ALLOWED_THEMES.keys())

THEME_TO_DOMAIN = {
    "combinatoire-denombrement": "algebre-geometrie",
    "geometrie-vecteurs-espace": "algebre-geometrie",
    "geometrie-orthogonalite-distances": "algebre-geometrie",
    "geometrie-reperage": "algebre-geometrie",
    "suites": "analyse",
    "limites-fonctions": "analyse",
    "derivation-convexite": "analyse",
    "continuite": "analyse",
    "logarithme": "analyse",
    "trigonometrie": "analyse",
    "primitives-equations-differentielles": "analyse",
    "calcul-integral": "analyse",
    "bernoulli-binomiale": "probabilites",
    "variables-aleatoires": "probabilites",
    "concentration-grands-nombres": "probabilites",
    "algorithmique-python": "algorithmique-programmation",
    "logique-raisonnement": "logique-ensembles",
}

THEME_DESCRIPTIONS = {
    "combinatoire-denombrement": "Principes additif et multiplicatif, k-uplets, permutations, combinaisons, coefficients binomiaux, triangle et relation de Pascal.",
    "geometrie-vecteurs-espace": "Vecteurs de l’espace, combinaisons linéaires, droites, plans, directions, bases, repères et positions relatives.",
    "geometrie-orthogonalite-distances": "Produit scalaire dans l’espace, orthogonalité, normes, distances, vecteur normal, projections orthogonales.",
    "geometrie-reperage": "Représentations paramétriques de droites, équations cartésiennes de plans, intersections, appartenance, systèmes linéaires simples.",
    "suites": "Convergence, divergence, limites de suites, comparaison, théorème des gendarmes, suites géométriques, suites monotones majorées ou minorées.",
    "limites-fonctions": "Limites finies ou infinies, asymptotes, opérations sur les limites, comparaison, croissances comparées.",
    "derivation-convexite": "Dérivée d’une composée, dérivée seconde, variations, tangentes, convexité, concavité, points d’inflexion.",
    "continuite": "Continuité, image d’une suite convergente, théorème des valeurs intermédiaires, existence et unicité de solutions.",
    "logarithme": "Logarithme népérien, propriétés algébriques, dérivée, variations, limites et croissances comparées.",
    "trigonometrie": "Fonctions sinus et cosinus, dérivées, variations, courbes représentatives, équations et inéquations trigonométriques.",
    "primitives-equations-differentielles": "Primitives, équations différentielles y'=f, y'=ay et y'=ay+b.",
    "calcul-integral": "Intégrale comme aire, lien avec les primitives, linéarité, positivité, Chasles, valeur moyenne, intégration par parties.",
    "bernoulli-binomiale": "Succession d’épreuves indépendantes, Bernoulli, loi binomiale, calculs de probabilités.",
    "variables-aleatoires": "Sommes de variables aléatoires, espérance, variance, échantillons, somme et moyenne d’un échantillon.",
    "concentration-grands-nombres": "Bienaymé-Tchebychev, inégalité de concentration, taille d’échantillon, précision, risque et loi des grands nombres.",
    "algorithmique-python": "Listes, indices, boucles, conditions, fonctions, simulations, méthodes numériques, seuils, dichotomie, Euler, Monte-Carlo.",
    "logique-raisonnement": "Ensembles, implication, équivalence, réciproque, contraposée, quantificateurs, récurrence, contre-exemple, absurde.",
}

THEME_KEY_RELATIONS = {
    "combinatoire-denombrement": ["n^k k-uplets", "n! permutations", "C(n,k)=n!/(k!(n-k)!)", "Relation de Pascal"],
    "geometrie-vecteurs-espace": ["Droite : point + vecteur directeur", "Plan : point + deux vecteurs non colinéaires", "Base de l’espace"],
    "geometrie-orthogonalite-distances": ["u·v=||u||||v||cos(theta)", "u·v=0", "Norme et distance en repère orthonormé"],
    "geometrie-reperage": ["Droite paramétrique", "Plan ax+by+cz+d=0", "Vecteur normal (a,b,c)"],
    "suites": ["Suite croissante majorée converge", "Théorème des gendarmes", "Comportement de q^n", "Récurrence"],
    "limites-fonctions": ["Asymptote verticale", "Asymptote horizontale", "Croissances comparées", "Terme prépondérant"],
    "derivation-convexite": ["(v o u)'=(v' o u)u'", "(e^u)'=u'e^u", "Signe de f'", "f''>=0 convexité", "Tangente"],
    "continuite": ["Toute fonction dérivable est continue", "TVI", "Continuité + stricte monotonie => unicité"],
    "logarithme": ["ln(ab)=ln(a)+ln(b)", "(ln x)'=1/x", "ln réciproque de exp", "croissances comparées"],
    "trigonometrie": ["sin'=cos", "cos'=-sin", "sin^2+cos^2=1", "sin impair, cos pair"],
    "primitives-equations-differentielles": ["F'=f", "primitive de 1/x", "y'=ay => y=Ce^{ax}", "constante d’intégration"],
    "calcul-integral": ["∫ f = F(b)-F(a)", "linéarité", "Chasles", "valeur moyenne", "intégration par parties"],
    "bernoulli-binomiale": ["P(X=k)=C(n,k)p^k(1-p)^{n-k}", "E(X)=np", "V(X)=np(1-p)", "indépendance"],
    "variables-aleatoires": ["E(X+Y)=E(X)+E(Y)", "V(X+Y)=V(X)+V(Y) si indépendantes", "V(aX)=a^2V(X)", "Mn=Sn/n"],
    "concentration-grands-nombres": ["P(|X-mu|>=delta)<=V/delta^2", "P(|Mn-mu|>=delta)<=V/(n delta^2)", "loi des grands nombres"],
    "algorithmique-python": ["for", "while", "listes", "dichotomie", "Euler", "Monte-Carlo", "indices"],
    "logique-raisonnement": ["appartenance vs inclusion", "implication vs réciproque", "contraposée", "contre-exemple", "récurrence"],
}

THEME_KEYWORDS = {k: [ALLOWED_THEMES[k].lower()] + [x.lower() for x in THEME_KEY_RELATIONS.get(k, [])] for k in ALLOWED_THEMES}

DIFFICULTY_MIN = 1
DIFFICULTY_MAX = 3
DIFFICULTY_LABELS = {1: "application directe", 2: "raisonnement guidé", 3: "problème de synthèse"}

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "64000"))
CLAUDE_MAX_TOKENS_PROGRAMME = int(os.getenv("CLAUDE_MAX_TOKENS_PROGRAMME", str(CLAUDE_MAX_TOKENS)))
CLAUDE_MAX_TOKENS_EXERCISE = int(os.getenv("CLAUDE_MAX_TOKENS_EXERCISE", str(CLAUDE_MAX_TOKENS)))
CLAUDE_MAX_TOKENS_COURSE = int(os.getenv("CLAUDE_MAX_TOKENS_COURSE", str(CLAUDE_MAX_TOKENS)))
CLAUDE_MAX_TOKENS_QUIZ = int(os.getenv("CLAUDE_MAX_TOKENS_QUIZ", str(CLAUDE_MAX_TOKENS)))
CLAUDE_TEMPERATURE = float(os.getenv("CLAUDE_TEMPERATURE", "0.1"))

DEFAULT_DPI = int(os.getenv("PDF_RENDER_DPI", "144"))
MAX_EXERCISE_TEXT_CHARS = int(os.getenv("MAX_EXERCISE_TEXT_CHARS", "42000"))
PDF_EXTENSIONS = (".pdf",)
DEFAULT_QUESTIONS_PER_THEME = int(os.getenv("QUIZ_QUESTIONS_PER_THEME", "10"))
QUIZ_OPTIONS_COUNT = 4
SITE_RELATIVE_PREFIXES = ("img/", "pdf/", "data/")


def ensure_directories() -> None:
    for path in [PDF_DIR, IMG_DIR, DATA_DIR, RAW_DIR, PAGES_DIR, INTERMEDIATE_DIR, GENERATED_DIR, REPORTS_DIR, PROGRAMME_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def is_allowed_theme(theme_id: str) -> bool:
    return theme_id in ALLOWED_THEMES


def require_allowed_theme(theme_id: str) -> str:
    if theme_id not in ALLOWED_THEMES:
        allowed = ", ".join(ALLOWED_THEMES)
        raise ValueError(f"Thème inconnu : {theme_id!r}. Thèmes autorisés : {allowed}")
    return theme_id


def get_theme_label(theme_id: str) -> str:
    require_allowed_theme(theme_id)
    return ALLOWED_THEMES[theme_id]


def get_theme_domain(theme_id: str) -> str:
    require_allowed_theme(theme_id)
    return THEME_TO_DOMAIN[theme_id]


def get_domain_label(domain_id: str) -> str:
    if domain_id not in PROGRAMME_DOMAINS:
        allowed = ", ".join(PROGRAMME_DOMAINS)
        raise ValueError(f"Domaine inconnu : {domain_id!r}. Domaines autorisés : {allowed}")
    return PROGRAMME_DOMAINS[domain_id]


def get_theme_description(theme_id: str) -> str:
    require_allowed_theme(theme_id)
    return THEME_DESCRIPTIONS.get(theme_id, ALLOWED_THEMES[theme_id])


def get_theme_key_relations(theme_id: str) -> list[str]:
    require_allowed_theme(theme_id)
    return THEME_KEY_RELATIONS.get(theme_id, [])


def get_theme_keywords(theme_id: str) -> list[str]:
    require_allowed_theme(theme_id)
    return THEME_KEYWORDS.get(theme_id, [])


def to_site_relative(path: Path | str) -> str:
    p = Path(path)
    try:
        rel = p.resolve().relative_to(SITE_ROOT.resolve())
    except ValueError:
        rel = p
    rel_str = rel.as_posix()
    if rel_str.startswith("site/"):
        rel_str = rel_str[len("site/"):]
    if rel_str.startswith("/"):
        raise ValueError(f"Chemin absolu interdit dans les données du site : {rel_str}")
    if not rel_str.startswith(SITE_RELATIVE_PREFIXES):
        raise ValueError(f"Chemin relatif inattendu : {rel_str!r}. Préfixes attendus : {SITE_RELATIVE_PREFIXES}")
    return rel_str


def programme_metadata() -> dict:
    return {
        "discipline": DISCIPLINE,
        "niveau": NIVEAU,
        "enseignement": ENSEIGNEMENT,
        "programme_version": PROGRAMME_VERSION,
        "bac_target": BAC_TARGET,
        "domains": PROGRAMME_DOMAINS,
        "domain_order": DOMAIN_ORDER,
        "themes": ALLOWED_THEMES,
        "theme_order": THEME_ORDER,
        "theme_to_domain": THEME_TO_DOMAIN,
        "theme_descriptions": THEME_DESCRIPTIONS,
    }
