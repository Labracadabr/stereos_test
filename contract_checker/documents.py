"""Document model: type, signing status, effective date, parties and contract references.

The type and status are taken from the document text only (title and "Статус:" line),
never from the file name or upload order.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from .textutil import Paragraph, Sentence, split_paragraphs, split_sentences, find_dates

CONTRACT = 'contract'
AMENDMENT = 'amendment'   # дополнительное соглашение (signed or not — see .signed)
DRAFT = 'draft'           # проект изменения
LETTER = 'letter'         # письмо / предложение
UNKNOWN = 'unknown'

KIND_LABELS = {
    CONTRACT: 'Договор',
    AMENDMENT: 'Дополнительное соглашение',
    DRAFT: 'Проект изменения',
    LETTER: 'Письмо',
    UNKNOWN: 'Не распознан',
}

# Contract numbers: "№ STN-101", "№ 15/2026", "№ А-7" ...
NUMBER_RE = re.compile(r'№\s*([A-Za-zА-ЯЁа-яё0-9]+(?:[-/][A-Za-zА-ЯЁа-яё0-9]+)*)')
TO_CONTRACT_RE = re.compile(r'к\s+договору\s*(?:поставки\s*)?(?:№\s*)?([A-Za-zА-ЯЁа-яё0-9]+(?:[-/][A-Za-zА-ЯЁа-яё0-9]+)*)',
                            re.IGNORECASE)
HEADER_FIELD_RE = re.compile(r'^\s*(Дата(?: проекта)?|Статус|От|Кому|Тема|Покупатель|Поставщик)\s*:', re.IGNORECASE)
SIGNED_RE = re.compile(r'(?<!не )подписан\w*\s+(?:обеими\s+)?сторонами', re.IGNORECASE)
UNSIGNED_RE = re.compile(r'не\s+подписан|проект|на\s+согласовании|не\s+согласован', re.IGNORECASE)
EFFECTIVE_RE = re.compile(r'вступ\w*\s+в\s+силу', re.IGNORECASE)
SUPPLIER_RE = re.compile(r'Поставщик\s*:\s*([^.«]*«[^»]+»|[^.]+)')
BUYER_RE = re.compile(r'Покупатель\s*:\s*([^.«]*«[^»]+»|[^.]+)')


@dataclass
class Citation:
    file: str
    line: int
    start: int
    end: int
    quote: str

    def to_dict(self):
        return {'file': self.file, 'line': self.line, 'start': self.start, 'end': self.end, 'quote': self.quote}


@dataclass
class Document:
    name: str
    text: str
    paragraphs: list[Paragraph]
    title: str
    kind: str = UNKNOWN
    number: str | None = None          # own number (contract number or amendment number)
    target_contract: str | None = None  # "к договору X" from the title
    signed: bool | None = None          # None = status not stated
    signed_date: date | None = None
    effective_date: date | None = None
    status_citation: Citation | None = None
    effective_citation: Citation | None = None
    supplier: str | None = None
    supplier_citation: Citation | None = None
    buyer: str | None = None

    def cite(self, sentence: Sentence) -> Citation:
        return Citation(self.name, sentence.para.line, sentence.start, sentence.end, sentence.text)

    def cite_paragraph(self, para: Paragraph) -> Citation:
        text = para.text.rstrip()
        return Citation(self.name, para.line, para.body_start, len(text), text[para.body_start:])

    def body_sentences(self) -> list[Sentence]:
        """Sentences of the document body: without the title and header fields (Дата:, Статус:, Тема: ...)."""
        result = []
        for para in self.paragraphs[1:]:
            if HEADER_FIELD_RE.match(para.text):
                continue
            result.extend(split_sentences(para))
        return result

    def to_dict(self):
        return {
            'name': self.name,
            'title': self.title,
            'kind': self.kind,
            'kind_label': KIND_LABELS[self.kind],
            'number': self.number,
            'signed': self.signed,
            'signed_date': self.signed_date.isoformat() if self.signed_date else None,
            'effective_date': self.effective_date.isoformat() if self.effective_date else None,
            'paragraphs': [{'line': p.line, 'text': p.text} for p in self.paragraphs],
        }


def parse_document(name: str, text: str) -> Document:
    paragraphs = split_paragraphs(text)
    title = paragraphs[0].text.strip() if paragraphs else ''
    doc = Document(name=name, text=text, paragraphs=paragraphs, title=title)
    if not paragraphs:
        return doc

    doc.kind = _detect_kind(title, paragraphs)
    to_contract = TO_CONTRACT_RE.search(title)
    if to_contract:
        doc.target_contract = to_contract.group(1)
    own_number = NUMBER_RE.search(title[:to_contract.start()] if to_contract else title)
    if own_number:
        doc.number = own_number.group(1)

    _parse_status(doc)
    _parse_parties(doc)
    return doc


def _detect_kind(title: str, paragraphs: list[Paragraph]) -> str:
    t = title.upper()
    if 'ПРОЕКТ' in t:
        return DRAFT
    if 'ПИСЬМО' in t or any(re.match(r'^\s*От\s*:', p.text) for p in paragraphs[:6]):
        return LETTER
    if re.search(r'ДОПОЛНИТЕЛЬН\w*\s+СОГЛАШЕН', t):
        return AMENDMENT
    if t.startswith('ДОГОВОР'):
        return CONTRACT
    return UNKNOWN


def _parse_status(doc: Document):
    for para in doc.paragraphs:
        if re.match(r'^\s*Статус\s*:', para.text, re.IGNORECASE):
            doc.status_citation = doc.cite_paragraph(para)
            status = para.text
            if SIGNED_RE.search(status) and not UNSIGNED_RE.search(status):
                doc.signed = True
                dates = find_dates(status)
                doc.signed_date = dates[0].value if dates else None
            elif UNSIGNED_RE.search(status):
                doc.signed = False
            break
    if doc.kind in (DRAFT, LETTER):
        doc.signed = False  # a draft or a letter never changes the contract by itself

    for sentence in doc.body_sentences():
        if EFFECTIVE_RE.search(sentence.text):
            dates = find_dates(sentence.text)
            if dates:
                doc.effective_date = dates[0].value
                doc.effective_citation = doc.cite(sentence)
                break


def _parse_parties(doc: Document):
    for para in doc.paragraphs:
        for sentence in split_sentences(para) or []:
            m = SUPPLIER_RE.search(sentence.text)
            if m and doc.supplier is None:
                doc.supplier = m.group(1).strip()
                start = sentence.start + m.start()
                doc.supplier_citation = Citation(doc.name, para.line, start, sentence.start + m.end(),
                                                 para.text[start:sentence.start + m.end()])
            b = BUYER_RE.search(sentence.text)
            if b and doc.buyer is None:
                doc.buyer = b.group(1).strip()


def mentions_contract(doc: Document, number: str) -> bool:
    return re.search(r'(?<![\w-])' + re.escape(number) + r'(?![\w-])', doc.text) is not None
