"""Rule-based answers to user questions (no LLM).

Supported question shapes:
  * "<поле> по договору <номер> [на <дата>]"      -> value of one field, as of the date
  * "по каким договорам поставка раньше <дата>"   -> deadline filter + unagreed deadline changes
The "на <дата>" part re-runs the analysis as of that date.
"""
import re
from datetime import date

from .extractors import FIELDS
from .resolver import analyze, OK, NO_DATA, REVIEW
from .textutil import find_dates, fmt_date

BEFORE_RE = re.compile(r'(раньше|ранее|до|не\s+позднее)\s*$', re.IGNORECASE)
AS_OF_RE = re.compile(r'(на|по\s+состоянию\s+на)\s*$', re.IGNORECASE)


def detect_field(question: str) -> str | None:
    q = question.lower()
    if re.search(r'сумм|стоимост|цен[аыу]', q):
        return 'amount'
    if re.search(r'срок|дат\w*\s+постав|когда', q):
        return 'deadline'
    return None


def answer(files: list[tuple[str, str]], question: str, check_date: date) -> dict:
    as_of, before = check_date, None
    for d in find_dates(question):
        prefix = question[:d.start]
        if BEFORE_RE.search(prefix):
            before = d.value
        elif AS_OF_RE.search(prefix):
            as_of = d.value

    analysis = analyze(files, as_of)
    contracts = analysis['contracts']
    mentioned = [c for c in contracts if re.search(r'(?<![\w-])' + re.escape(c['number']) + r'(?![\w-])',
                                                   question, re.IGNORECASE)]
    field = detect_field(question)
    base = {'question': question, 'as_of': as_of}

    if before and not mentioned:
        return {**base, **_deadline_filter(contracts, before)}
    if field and mentioned:
        return {**base, 'kind': 'field', 'field': field, 'text': _field_text(mentioned, field, as_of),
                'items': [{'contract': c['number'], 'result': c['fields'][field]} for c in mentioned]}
    hint = ('Не удалось распознать вопрос. Укажите номер договора и поле (сумма или срок поставки), '
            'например: «Какова сумма договора ' + (contracts[0]['number'] if contracts else 'STN-101') +
            '?», или фильтр: «По каким договорам поставка раньше 01.04.2026?»')
    if not contracts:
        hint = 'Сначала загрузите договоры.'
    elif mentioned and not field:
        hint = 'Укажите, что нужно узнать: сумму договора или срок поставки.'
    elif field and not mentioned:
        hint = 'Укажите номер договора. Загружены: ' + ', '.join(c['number'] for c in contracts)
    return {**base, 'kind': 'unknown', 'text': hint, 'items': []}


def _field_text(contracts: list[dict], field: str, as_of: date) -> str:
    parts = []
    for c in contracts:
        r = c['fields'][field]
        subject = FIELDS[field]['phrase'].format(n=c['number'])
        if r['status'] == OK:
            s = f'{subject} на {fmt_date(as_of)}: {r["display"]}.'
            if r['changed_by']:
                s += f' Значение изменено документом «{r["changed_by"]["title"]}» ({r["changed_by"]["file"]})'
                if r['original']:
                    s += f', в исходном договоре было {r["original"]["display"]}'
                s += '.'
        elif r['status'] == NO_DATA:
            s = f'{subject}: нет данных — значение в тексте не найдено.'
            if r['hints']:
                s += f' В договоре: «{r["hints"][0]["quote"]}»'
            if r['ignored']:
                s += ' Найденные в тексте суммы (' + ', '.join(i['display'] for i in r['ignored']) + \
                     ') не являются стоимостью договора.'
        else:
            s = f'{subject}: требует проверки. ' + ' '.join(r['issues'])
        if r['proposals']:
            s += ' Есть несогласованные изменения: ' + '; '.join(
                f'{p["display"]} ({p["kind_label"].lower()} {p["file"]} — {p["role_label"].lower()})'
                for p in r['proposals']) + '.'
        parts.append(s)
    return '\n'.join(parts)


def _deadline_filter(contracts: list[dict], before: date) -> dict:
    matched, unknown, other_proposals = [], [], []
    for c in contracts:
        r = c['fields']['deadline']
        if r['status'] == OK and r['value'] < before:
            matched.append(c)
        elif r['status'] != OK:
            unknown.append(c)
        elif r['proposals']:
            other_proposals.append(c)

    lines = []
    if matched:
        lines.append(f'Поставка раньше {fmt_date(before)} запланирована по договорам: ' +
                     ', '.join(f'{c["number"]} ({c["fields"]["deadline"]["display"]})' for c in matched) + '.')
        for c in matched:
            props = c['fields']['deadline']['proposals']
            if props:
                lines.append(f'{c["number"]}: есть несогласованные изменения срока — ' + '; '.join(
                    f'{p["display"]} ({p["kind_label"].lower()} {p["file"]}, {p["reason"].lower()})' for p in props) +
                    '. Действующий срок не меняется.')
            else:
                lines.append(f'{c["number"]}: несогласованных изменений срока нет.')
    else:
        lines.append(f'Договоров со сроком поставки раньше {fmt_date(before)} не найдено.')
    if unknown:
        lines.append('Срок не удалось определить (нет данных или требует проверки): ' +
                     ', '.join(c['number'] for c in unknown) + '.')
    for c in other_proposals:
        props = c['fields']['deadline']['proposals']
        lines.append(f'Также есть несогласованные изменения срока по {c["number"]} (действующий срок '
                     f'{c["fields"]["deadline"]["display"]}): ' +
                     '; '.join(f'{p["display"]} ({p["file"]})' for p in props) + '.')

    return {'kind': 'filter', 'field': 'deadline', 'before': before, 'text': '\n'.join(lines),
            'items': [{'contract': c['number'], 'result': c['fields']['deadline']}
                      for c in matched + unknown + other_proposals]}
