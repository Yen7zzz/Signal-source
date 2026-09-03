"""Rule-based scorer.

score_by_rules(title, source_type) -> int (1-10)
is_junk(title, source_type) -> bool
"""

SOURCE_BASE = {
    'sec_edgar': 7,
    'semianalysis': 7,
    'trendforce': 6,
    'digitimes': 5,
    'next_platform': 5,
    'semi_engineering': 4,
    'eetimes': 4,
    'fabricated_knowledge': 4,
    'toms_hardware': 3,
    'serve_the_home': 3,
    'seeking_alpha': 4,
}
DEFAULT_SOURCE_BASE = 4

POSITIVE_KEYWORDS = [
    'hbm', 'dram', 'nand', 'cowos', 'packaging', 'hybrid bonding', 'wafer',
    'capex', 'fab', 'capacity', 'expansion', 'mass production', 'yield', 'yields',
    'shortage', 'shortages', 'supply chain', 'price hike', 'foundry',
    '2nm', '3nm', '1.4nm', '18a', 'node',
    'tsmc', 'sk hynix', 'hynix', 'micron', 'kioxia', 'ymtc', 'smic', 'asml',
    'applied materials', 'lam research', 'rapidus', 'tokyo electron',
    '10-q', '8-k', 'earnings', 'guidance',
]

NEGATIVE_KEYWORDS = [
    'review', 'benchmark', 'hands-on', 'roundup', 'tested', 'best',
    'rtx', 'ryzen', 'radeon', 'geforce', 'motherboard', 'laptop', 'mini pc',
    'keyboard', 'monitor', 'gaming', 'steam', 'console',
    'deal', 'deals', 'save', 'discount', 'msrp', 'sale', 'prime day', 'off', 'now just',
]

POS_CAP = 4
NEG_CAP = 4
HARD_CAP_SCORE = 3


def _hard_cap_hit(title: str) -> bool:
    lower = title.lower()

    if '$' in title and any(w in lower for w in ('save', 'off', 'deal', 'discount')):
        return True
    if title.startswith('Save '):
        return True
    if ' review' in lower:
        return True
    if 'prime day' in lower:
        return True
    return False


def score_by_rules(title: str, source_type: str) -> int:
    if title is None:
        title = ''
    lower = title.lower()

    base = SOURCE_BASE.get(source_type, DEFAULT_SOURCE_BASE)

    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in lower)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in lower)

    pos_score = min(pos_hits, POS_CAP)
    neg_score = min(neg_hits, NEG_CAP)

    score = base + pos_score - neg_score

    if _hard_cap_hit(title):
        score = min(score, HARD_CAP_SCORE)

    score = max(1, min(10, score))
    return int(score)


# ---------------------------------------------------------------------------
# is_junk: exclusion-only classifier. Does not score; only flags obvious
# noise (consumer-product deals/reviews) so it can be filtered out before
# scoring. sec_edgar and tw_revenue are exempt (formatted, non-prose titles).
# ---------------------------------------------------------------------------

JUNK_EXEMPT_SOURCES = {'sec_edgar', 'tw_revenue'}

# phrase-level (not substring) triggers for "off" / "save" to avoid
# matching 'offer', 'office', 'official', 'offload', etc.
RULE_A_OFF_SAVE_PHRASES = [
    ' off ', '% off', 'off this', 'off the', 'save $',
]
RULE_A_DEAL_WORDS = ['deal', 'deals', 'discount']

# a $ + deal/off/save phrase is not junk if it's actually business/M&A news
RULE_A_BUSINESS_EXCLUSIONS = [
    'billion', 'million', 'trillion', ' bn', 'acquisition', 'merger', 'partnership',
]

RULE_C_EXCLUSIONS = ['under review', 'in review', 'reviewing']

RULE_E_PHRASES = [
    'hands-on', 'best of', 'buying guide', 'msrp', 'now just', 'all-time low', 'giveaway',
]

CONSUMER_WORDS = [
    'rtx', 'ryzen', 'radeon', 'geforce', 'motherboard', 'laptop',
    'mini pc', 'keyboard', 'monitor', 'gaming', 'console', 'prebuilt',
]

TRANSACTION_WORDS = [
    'price', 'deal', 'sale', 'cheap', 'budget', 'bundle', 'combo',
]


def _rule_a(title: str, lower: str) -> bool:
    if not ('$' in title and (
        any(p in lower for p in RULE_A_OFF_SAVE_PHRASES)
        or any(w in lower for w in RULE_A_DEAL_WORDS)
    )):
        return False
    if any(w in lower for w in RULE_A_BUSINESS_EXCLUSIONS):
        return False
    return True


def _rule_c(title: str, lower: str) -> bool:
    if any(p in lower for p in RULE_C_EXCLUSIONS):
        return False
    return ' review' in lower


def _rule_d(title: str, lower: str) -> bool:
    return 'prime day' in lower


def _rule_e(title: str, lower: str) -> bool:
    return any(p in lower for p in RULE_E_PHRASES)


def _rule_f(title: str, lower: str) -> bool:
    has_consumer = any(w in lower for w in CONSUMER_WORDS)
    has_transaction = any(w in lower for w in TRANSACTION_WORDS)
    return has_consumer and has_transaction


JUNK_RULES = {
    'A': _rule_a,
    'C': _rule_c,
    'D': _rule_d,
    'E': _rule_e,
    'F': _rule_f,
}


def junk_rule_hits(title: str, source_type: str) -> dict:
    """Return {rule_letter: bool} for each exclusion rule.

    All False when source_type is exempt, regardless of title content.
    """
    if title is None:
        title = ''
    if source_type in JUNK_EXEMPT_SOURCES:
        return {k: False for k in JUNK_RULES}
    lower = title.lower()
    return {k: fn(title, lower) for k, fn in JUNK_RULES.items()}


def is_junk(title: str, source_type: str) -> bool:
    if source_type in JUNK_EXEMPT_SOURCES:
        return False
    hits = junk_rule_hits(title, source_type)
    return any(hits.values())
