"""Field extractors. Each one looks at the sentences of one document and returns
candidates (values with an exact quote), plus hints that explain a missing value.

To add a new field (e.g. penalty rate): write an extract_<field>(doc) -> Extraction
function here and register it in FIELDS.
"""
import re
from dataclasses import dataclass, field

from .documents import Document, Citation
from .textutil import find_amounts, find_dates, fmt_amount, fmt_date


@dataclass
class Candidate:
    value: object
    display: str
    citation: Citation
    warnings: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    candidates: list[Candidate] = field(default_factory=list)
    # Sentences that talk about the field but contain no usable value ("определяется приложением").
    hints: list[Citation] = field(default_factory=list)
    # Values of the same type that were found but deliberately not used (advance payment, budget limit).
    ignored: list[Candidate] = field(default_factory=list)

    def distinct_values(self):
        return sorted({c.value for c in self.candidates}, key=str)


# ---------------------------------------------------------------- amount

PRICE_CONTEXT_RE = re.compile(
    r'стоимост\w*|цен\w*\s+(?:договора|поставки)|сумм\w*\s+договора'
    r'|(?:общ|итогов|полн)\w*\s+(?:сумм|цен)\w*', re.IGNORECASE)
PRICE_EXCLUDE_RE = re.compile(r'аванс|лимит|бюджет|неустойк|штраф|пени|предоплат', re.IGNORECASE)
PRICE_DOUBT_RE = re.compile(r'не\s+является|ориентировочн|предварительн|не\s+более|не\s+менее|примерн',
                            re.IGNORECASE)


def extract_amount(doc: Document) -> Extraction:
    ex = Extraction()
    for s in doc.body_sentences():
        text = s.text
        amounts = find_amounts(text)
        is_price = PRICE_CONTEXT_RE.search(text) and not PRICE_EXCLUDE_RE.search(text)
        if is_price and amounts:
            for a in amounts:
                warnings = []
                if a.words and a.words_value is not None and a.words_value != int(a.value):
                    warnings.append(f'Сумма цифрами ({fmt_amount(a.value)}) не совпадает с суммой прописью '
                                    f'({fmt_amount(a.words_value)})')
                if PRICE_DOUBT_RE.search(text):
                    warnings.append('Формулировка допускает, что сумма не окончательная')
                ex.candidates.append(Candidate(a.value, fmt_amount(a.value), doc.cite(s), warnings))
        elif is_price:
            ex.hints.append(doc.cite(s))
        else:
            for a in amounts:
                ex.ignored.append(Candidate(a.value, fmt_amount(a.value), doc.cite(s)))
    return ex


# ---------------------------------------------------------------- delivery deadline

DELIVERY_RE = re.compile(r'постав(?!щик)\w*', re.IGNORECASE)
DEADLINE_EXCLUDE_RE = re.compile(r'оплат|вступ\w*\s+в\s+силу|по\s+состоянию', re.IGNORECASE)
FROM_PREFIX_RE = re.compile(r'(?:\bс|\bот)\s*$', re.IGNORECASE)   # "перенести с 20.03 на 27.03"
TO_PREFIX_RE = re.compile(r'(?:\bна|\bпозднее|\bдо|\bпо)\s*$', re.IGNORECASE)


def extract_deadline(doc: Document) -> Extraction:
    ex = Extraction()
    for s in doc.body_sentences():
        text = s.text
        if not DELIVERY_RE.search(text):
            continue
        if DEADLINE_EXCLUDE_RE.search(text):
            continue
        dates = [d for d in find_dates(text) if not FROM_PREFIX_RE.search(text[:d.start])]
        if not dates:
            if re.search(r'срок\w*', text, re.IGNORECASE):
                ex.hints.append(doc.cite(s))
            continue
        # "заменить 15.04.2026 на 30.04.2026": prefer the date after "на / не позднее / до"
        targeted = [d for d in dates if TO_PREFIX_RE.search(text[:d.start])]
        for d in targeted or dates:
            ex.candidates.append(Candidate(d.value, fmt_date(d.value), doc.cite(s)))
    return ex


FIELDS = {
    'amount': {'label': 'Сумма договора', 'phrase': 'Сумма договора {n}', 'extract': extract_amount},
    'deadline': {'label': 'Срок поставки', 'phrase': 'Срок поставки по договору {n}', 'extract': extract_deadline},
}
