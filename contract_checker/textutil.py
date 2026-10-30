"""Low-level text helpers: paragraphs, sentences, dates, money amounts, number words.

Everything works on offsets inside the original paragraph text, so every extracted
value can be cited with an exact quote and highlighted in the source.
"""
import re
from dataclasses import dataclass
from datetime import date


@dataclass
class Paragraph:
    line: int          # 1-based line number in the source file
    text: str          # paragraph text exactly as in the file (without line break)
    clause: str | None  # leading clause number, e.g. "2.1"
    body_start: int    # offset where text after the clause number starts


@dataclass
class Sentence:
    para: Paragraph
    start: int  # offsets inside para.text
    end: int

    @property
    def text(self) -> str:
        return self.para.text[self.start:self.end]


CLAUSE_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)\.?\s+')
SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[«"(]?[А-ЯЁA-Z])')


def split_paragraphs(text: str) -> list[Paragraph]:
    paragraphs = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        m = CLAUSE_RE.match(line)
        paragraphs.append(Paragraph(line=i, text=line, clause=m.group(1) if m else None,
                                    body_start=m.end() if m else len(line) - len(line.lstrip())))
    return paragraphs


def split_sentences(para: Paragraph) -> list[Sentence]:
    sentences = []
    pos = para.body_start
    for m in SENTENCE_END_RE.finditer(para.text, pos):
        sentences.append(Sentence(para, pos, m.start()))
        pos = m.end()
    end = len(para.text.rstrip())
    if pos < end:
        sentences.append(Sentence(para, pos, end))
    return sentences


# ---------------------------------------------------------------- dates

MONTHS = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12,
}
DATE_NUM_RE = re.compile(r'(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d])')
DATE_WORD_RE = re.compile(r'(?<!\d)(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s+(\d{4})(?:\s*(?:года|г\.?))?',
                          re.IGNORECASE)


@dataclass
class DateMention:
    value: date
    start: int
    end: int


def find_dates(text: str) -> list[DateMention]:
    found = []
    for m in DATE_NUM_RE.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        _append_date(found, y, mo, d, m)
    for m in DATE_WORD_RE.finditer(text):
        _append_date(found, int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)), m)
    return sorted(found, key=lambda x: x.start)


def _append_date(found, y, mo, d, m):
    try:
        found.append(DateMention(date(y, mo, d), m.start(), m.end()))
    except ValueError:
        pass  # 31.02.2026 and the like are not dates


def parse_date(text: str) -> date | None:
    dates = find_dates(text.strip())
    return dates[0].value if dates else None


def fmt_date(d: date | None) -> str | None:
    return d.strftime('%d.%m.%Y') if d else None


# ---------------------------------------------------------------- money

AMOUNT_RE = re.compile(
    r'(?<![\d,.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:,(\d{1,2}))?'  # 1 200 000 or 1200000[,00]
    r'\s*(?:\(([^()]*)\)\s*)?'                                          # optional amount in words
    r'(руб\w*|₽)',
    re.IGNORECASE)


@dataclass
class AmountMention:
    value: float
    words: str | None       # amount in words from the parentheses, if any
    words_value: int | None  # parsed words, None if missing or not parseable
    start: int
    end: int


def find_amounts(text: str) -> list[AmountMention]:
    result = []
    for m in AMOUNT_RE.finditer(text):
        rubles = int(re.sub(r'\D', '', m.group(1)))
        kopecks = int(m.group(2).ljust(2, '0')) if m.group(2) else 0
        words = m.group(3)
        result.append(AmountMention(rubles + kopecks / 100, words,
                                    parse_number_words(words) if words else None, m.start(), m.end()))
    return result


def fmt_amount(value: float | None) -> str | None:
    if value is None:
        return None
    s = f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
    return (s[:-3] if s.endswith(',00') else s) + ' ₽'


_NUMBER_WORDS = {
    'ноль': 0, 'один': 1, 'одна': 1, 'одно': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4, 'пять': 5,
    'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9, 'десять': 10, 'одиннадцать': 11, 'двенадцать': 12,
    'тринадцать': 13, 'четырнадцать': 14, 'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17,
    'восемнадцать': 18, 'девятнадцать': 19, 'двадцать': 20, 'тридцать': 30, 'сорок': 40,
    'пятьдесят': 50, 'шестьдесят': 60, 'семьдесят': 70, 'восемьдесят': 80, 'девяносто': 90,
    'сто': 100, 'двести': 200, 'триста': 300, 'четыреста': 400, 'пятьсот': 500, 'шестьсот': 600,
    'семьсот': 700, 'восемьсот': 800, 'девятьсот': 900,
}
_SCALES = {
    'тысяча': 10**3, 'тысячи': 10**3, 'тысяч': 10**3,
    'миллион': 10**6, 'миллиона': 10**6, 'миллионов': 10**6,
    'миллиард': 10**9, 'миллиарда': 10**9, 'миллиардов': 10**9,
}


def parse_number_words(text: str) -> int | None:
    """'один миллион двести тысяч' -> 1200000. None if any word is not a number word."""
    words = re.findall(r'[а-яё]+', text.lower())
    if not words:
        return None
    total = current = 0
    for w in words:
        if w in _NUMBER_WORDS:
            current += _NUMBER_WORDS[w]
        elif w in _SCALES:
            total += (current or 1) * _SCALES[w]
            current = 0
        else:
            return None
    return total + current
