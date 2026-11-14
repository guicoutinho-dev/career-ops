"""Rule-based classification of Facebook group posts. Pure functions, no I/O."""
from __future__ import annotations

import re

from hub.domain.models import Classification

RELEVANT_SCORE = 40

SUBURBS = [
    "Wolli Creek", "Surry Hills", "Summer Hill", "Dulwich Hill", "St Peters", "Eastgardens",
    "Rosebery", "Kogarah", "Rockdale", "Redfern", "Sydenham", "Marrickville", "Chippendale",
    "Mascot", "Bondi", "Bankstown", "Parramatta", "Newtown", "Strathfield", "Burwood",
    "Ashfield", "Hurstville", "Arncliffe", "Kingsford", "Randwick", "Zetland", "Waterloo",
    "Alexandria", "Glebe", "Pyrmont", "Ultimo", "Haymarket", "Lidcombe", "Auburn", "Homebush",
    "Campsie", "Canterbury", "Lakemba", "Epping", "Chatswood", "Manly", "Coogee", "Maroubra",
    "Botany", "Banksmeadow", "Tempe", "Erskineville", "Enmore", "Petersham", "Lewisham",
    "Penrith", "Liverpool", "Blacktown", "Westmead", "Granville", "Sutherland", "Cronulla",
    "Brighton-Le-Sands", "Banksia", "Carlton", "Allawah", "Hillsdale", "Pagewood", "Eveleigh",
]

STAY_TERMS = [
    "room", "quarto", "accommodation", "acomoda", "flatmate", "housemate", "alugo", "aluga",
    "aluguel", "sublet", "subloca", "bond", "suite", "suíte", "studio", "apartamento",
    "cama", "morar", "dividir", "short term", "short-term", "curta temporada", "pw", "p/w",
    "per week", "por semana", "inspection", "bills included",
]
JOB_TERMS = [
    "cleaner", "labourer", "laborer", "bartender", "barista", "kitchen hand", "hiring",
    "procurando", "contratando", "vaga para", "vagas para", "emprego", "trabalho", "job",
    "casual", "full time", "part time", "white card", "abn", "traffic control", "waiter",
    "waitress", "chef", "farm", "picking", "per hour", "por hora", "/hr", "/h", "ticket",
    "start immediately", "immediate start", "recruit",
]

_WEEKLY = re.compile(
    r"\$\s*(\d{2,4})|\b(\d{3})\s*(?:aud|pw|p/w|por semana|per week|/week|/w\b|/sem)"
)
_HOURLY = re.compile(
    r"\$\s*(\d{2,3})(?:[.,]\d+)?\s*(?:\+)?\s*(?:/|per|por|a|p)?\s*(?:h\b|hr\b|hrs\b|hour|hora)"
)
_PHONE = re.compile(r"(?:\+?61|0)[\s-]?(4\d{2})[\s-]?(\d{3})[\s-]?(\d{3})")

_NOT_AN_OFFER = re.compile(
    r"car rental|carro para alugar|aluguel de carro|"
    r"procurando emprego|procuro emprego|procurando trabalho|procuro trabalho|"
    r"looking for (?:a )?(?:casual |hospitality |cleaning |any )?(?:work|job)|"
    r"i'?m looking for (?:a )?(?:room|accommodation)|looking for (?:a )?(?:room|accommodation)|"
    r"algu[eé]m (?:alugando|tem|sabe|indica)|procuro (?:um )?quarto|procurando (?:um )?quarto"
)

_SCAM_PATTERNS = {
    "deposit before inspection": re.compile(
        r"(deposit|dep[oó]sito|bond).{0,50}(before|antes).{0,40}(inspect|visit|ver|visita|see|view)"
    ),
    "gift card or crypto payment": re.compile(r"gift\s*card|bitcoin|crypto|usdt|western union"),
    "refuses inspection": re.compile(
        r"no inspection|sem visita|n[aã]o (precisa|pode).{0,10}visit|cannot inspect"
    ),
}


def _contains_any(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if t in text)


def _weekly_price(body: str) -> int | None:
    prices: list[int] = []
    for line in body.lower().splitlines():
        if "bond" in line and not re.search(r"week|semana|rent|aluguel|pw|p/w", line):
            continue
        for a, b in _WEEKLY.findall(line):
            value = int(a or b)
            if 120 <= value <= 1500:
                prices.append(value)
    return min(prices) if prices else None


def _hourly_rate(low: str) -> int | None:
    rates = [int(m) for m in _HOURLY.findall(low) if 15 <= int(m) <= 150]
    return max(rates) if rates else None


def _suburb(body: str) -> str | None:
    low = body.lower()
    best: tuple[int, str] | None = None
    for name in SUBURBS:
        match = re.search(rf"\b{re.escape(name.lower())}\b", low)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), name)
    return best[1] if best else None


def _is_noise_or_seeker(low: str) -> bool:
    return bool(_NOT_AN_OFFER.search(low))


def _ticket(low: str) -> str:
    if "white card" in low or "whitecard" in low:
        return "White Card"
    if re.search(r"\brsa\b|\brcg\b|\bbartender\b", low):
        return "RSA / RCG"
    if re.search(r"driver'?s? licen[cs]e|\bcnh\b|\bute\b|traffic control", low):
        return "Driver Licence"
    return "No Ticket (ABN/TFN)"


def _phone(body: str) -> str | None:
    match = _PHONE.search(body)
    return f"61{match.group(1)}{match.group(2)}{match.group(3)}" if match else None


def _scam_flags(low: str) -> list[str]:
    return [name for name, pat in _SCAM_PATTERNS.items() if pat.search(low)]


def _score_stay(c: Classification) -> int:
    score = 30
    if c.price_week is not None:
        if c.price_week <= 300:
            score += 35
        elif c.price_week <= 350:
            score += 30
        elif c.price_week <= 400:
            score += 15
        elif c.price_week > 600:
            score -= 25
    if c.has_ensuite:
        score += 8
    if c.bills_included:
        score += 6
    if c.suburb:
        score += 4
    return score


def _score_job(c: Classification) -> int:
    score = 25
    if c.pay_hourly:
        score += 25 if c.pay_hourly >= 36 else 15
    if c.phone:
        score += 15
    if c.ticket == "No Ticket (ABN/TFN)":
        score += 5
    return score


def classify(body: str) -> Classification:
    """Classify a post body as a stay, job or other and extract key facts."""
    text = (body or "").strip()
    if not text:
        return Classification()
    low = text.lower()
    if _is_noise_or_seeker(low):
        return Classification()

    stay_hits = _contains_any(low, STAY_TERMS)
    job_hits = _contains_any(low, JOB_TERMS)
    price_week = _weekly_price(text)
    pay_hourly = _hourly_rate(low)

    if pay_hourly and (job_hits >= stay_hits or not price_week):
        kind = "job"
    elif price_week and stay_hits >= 1:
        kind = "stay"
    elif job_hits > stay_hits and job_hits >= 2:
        kind = "job"
    elif stay_hits >= 2:
        kind = "stay"
    elif job_hits >= 1 and pay_hourly:
        kind = "job"
    else:
        return Classification()

    c = Classification(
        kind=kind,
        suburb=_suburb(text),
        phone=_phone(text),
        scam_flags=_scam_flags(low),
    )
    if kind == "stay":
        c.price_week = price_week
        c.bills_included = bool(re.search(r"bills? included|utilities included|contas inclu", low))
        c.has_ensuite = bool(re.search(r"ensuite|en-suite|banheiro privativo|private bathroom", low))
        c.score = _score_stay(c)
    else:
        c.pay_hourly = pay_hourly
        c.ticket = _ticket(low)
        c.score = _score_job(c)

    c.score = max(0, c.score - 30 * len(c.scam_flags))
    return c
