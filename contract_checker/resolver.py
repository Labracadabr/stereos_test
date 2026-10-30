"""Links documents to contracts and computes the conditions in force on the check date.

Rule of the task: a condition is changed only by an amendment that is signed by both
parties and has entered into force by the check date. Letters, drafts and anything
with an unclear status are shown as "proposals" or "needs review", never applied.
"""
from collections import defaultdict
from datetime import date

from .documents import (Document, parse_document, mentions_contract, KIND_LABELS,
                        CONTRACT, AMENDMENT, DRAFT, LETTER, UNKNOWN)
from .extractors import FIELDS, Extraction
from .textutil import fmt_date

OK, NO_DATA, REVIEW = 'ok', 'no_data', 'review'

APPLIED = 'applied'            # signed amendment in force -> changes the contract
PROPOSAL = 'proposal'          # letter, draft, unsigned amendment -> not agreed
NOT_EFFECTIVE = 'not_effective'  # signed, but not in force on the check date
NEEDS_REVIEW = 'needs_review'  # cannot decide automatically
ROLE_LABELS = {
    APPLIED: 'Действует, изменяет условия',
    PROPOSAL: 'Не согласовано',
    NOT_EFFECTIVE: 'Не вступило в силу на дату проверки',
    NEEDS_REVIEW: 'Требует проверки',
}


def analyze(files: list[tuple[str, str]], check_date: date) -> dict:
    docs = [parse_document(name, text) for name, text in files]
    contracts_by_number = defaultdict(list)
    for d in docs:
        if d.kind == CONTRACT and d.number:
            contracts_by_number[d.number].append(d)

    # Link every non-contract document to the contract(s) it refers to.
    links = defaultdict(list)   # contract number -> [(doc, role, reason)]
    unlinked = []
    for d in docs:
        if d.kind == CONTRACT:
            if not d.number:
                unlinked.append(_unlinked(d, 'Не найден номер договора в заголовке'))
            continue
        if d.target_contract and d.target_contract in contracts_by_number:
            refs = [d.target_contract]
        else:
            refs = [n for n in contracts_by_number if mentions_contract(d, n)]
        if not refs:
            reason = (f'Договор {d.target_contract} не загружен' if d.target_contract
                      else 'Не найдена ссылка на загруженный договор')
            unlinked.append(_unlinked(d, reason))
            continue
        for number in refs:
            role, reason = _classify(d, contracts_by_number[number][0], check_date)
            if len(refs) > 1:
                role, reason = NEEDS_REVIEW, 'Документ упоминает несколько договоров: ' + ', '.join(refs)
            links[number].append((d, role, reason))

    contracts = []
    for number, same_number in contracts_by_number.items():
        for contract in same_number:
            contracts.append(_resolve_contract(contract, links[number], check_date,
                                               duplicate=len(same_number) > 1))

    return {
        'check_date': check_date,
        'contracts': contracts,
        'unlinked': unlinked,
        'documents': [d.to_dict() for d in docs],
    }


def _unlinked(d: Document, reason: str) -> dict:
    return {'file': d.name, 'title': d.title, 'kind_label': KIND_LABELS[d.kind], 'reason': reason}


def _classify(d: Document, contract: Document, check_date: date) -> tuple[str, str]:
    if d.kind == LETTER:
        return PROPOSAL, 'Письмо — предложение стороны, не изменяет условия договора'
    if d.kind == DRAFT:
        return PROPOSAL, 'Проект изменения не подписан'
    if d.kind == UNKNOWN:
        return NEEDS_REVIEW, 'Тип документа не распознан'
    if d.kind != AMENDMENT:
        return NEEDS_REVIEW, 'Документ не является допсоглашением'
    if d.signed is False:
        return PROPOSAL, 'Допсоглашение не подписано'
    if d.signed is None:
        return NEEDS_REVIEW, 'В допсоглашении не указан статус подписания'
    if d.signed_date is None:
        return NEEDS_REVIEW, 'Не указана дата подписания допсоглашения'
    if d.signed_date > check_date:
        return NOT_EFFECTIVE, f'Подписано {fmt_date(d.signed_date)}, после даты проверки'
    if d.effective_date is None:
        return NEEDS_REVIEW, 'Не указана дата вступления допсоглашения в силу'
    if d.effective_date > check_date:
        return NOT_EFFECTIVE, f'Вступает в силу {fmt_date(d.effective_date)}, после даты проверки'
    if d.supplier and contract.supplier and _norm(d.supplier) != _norm(contract.supplier):
        return NEEDS_REVIEW, f'Поставщик в допсоглашении ({d.supplier}) не совпадает с договором ({contract.supplier})'
    return APPLIED, (f'Подписано {fmt_date(d.signed_date)}, '
                     f'вступило в силу {fmt_date(d.effective_date)}')


def _norm(s: str) -> str:
    return ' '.join(s.lower().replace('"', '«').split())


def _resolve_contract(contract: Document, linked: list, check_date: date, duplicate: bool) -> dict:
    issues = []
    if duplicate:
        issues.append(f'Загружено несколько договоров с номером {contract.number}')
    if contract.signed is False:
        issues.append('Договор не подписан')
    elif contract.signed is None:
        issues.append('В договоре не указан статус подписания')
    elif contract.signed_date and contract.signed_date > check_date:
        issues.append(f'Договор подписан {fmt_date(contract.signed_date)}, после даты проверки')

    applied = sorted((d for d, role, _ in linked if role == APPLIED),
                     key=lambda d: (d.effective_date, d.signed_date))
    fields = {key: _resolve_field(key, contract, applied, linked) for key in FIELDS}

    return {
        'number': contract.number,
        'file': contract.name,
        'title': contract.title,
        'supplier': {
            'value': contract.supplier,
            'display': contract.supplier or 'нет данных',
            'status': OK if contract.supplier else NO_DATA,
            'citation': contract.supplier_citation.to_dict() if contract.supplier_citation else None,
        },
        'fields': fields,
        'related': [{
            'file': d.name, 'title': d.title, 'kind_label': KIND_LABELS[d.kind],
            'role': role, 'role_label': ROLE_LABELS[role], 'reason': reason,
            'status_citation': d.status_citation.to_dict() if d.status_citation else None,
        } for d, role, reason in linked],
        'issues': issues,
        'needs_review': bool(issues) or any(f['status'] == REVIEW for f in fields.values())
                        or any(role == NEEDS_REVIEW for _, role, _ in linked),
    }


def _resolve_field(key: str, contract: Document, applied: list[Document], linked: list) -> dict:
    extract = FIELDS[key]['extract']
    base = extract(contract)
    result = {
        'label': FIELDS[key]['label'],
        'status': NO_DATA, 'value': None, 'display': 'нет данных', 'citation': None,
        'changed_by': None, 'original': None,
        'history': [], 'proposals': [], 'issues': [],
        'hints': [h.to_dict() for h in base.hints],
        'ignored': [{'display': c.display, 'citation': c.citation.to_dict()} for c in base.ignored],
    }
    _set_from(result, base, contract, source_label='договор')

    last_effective = None
    for d in applied:
        ex = extract(d)
        if not ex.candidates:
            continue
        if result['status'] != NO_DATA and result['changed_by'] is None:
            result['original'] = {'display': result['display'], 'citation': result['citation']}
        if last_effective == d.effective_date and ex.distinct_values() != [result['value']]:
            result['issues'].append(f'Несколько допсоглашений с одной датой вступления в силу '
                                    f'({fmt_date(d.effective_date)}) меняют поле по-разному')
        _set_from(result, ex, d, source_label='допсоглашение')
        result['changed_by'] = {'file': d.name, 'title': d.title}
        last_effective = d.effective_date

    for d, role, reason in linked:
        if role == APPLIED:
            continue
        for c in extract(d).candidates:
            result['proposals'].append({
                'value': c.value, 'display': c.display, 'file': d.name, 'title': d.title,
                'kind_label': KIND_LABELS[d.kind], 'role': role, 'role_label': ROLE_LABELS[role],
                'reason': reason, 'citation': c.citation.to_dict(),
            })
            if role == NEEDS_REVIEW:
                result['issues'].append(f'{d.name}: документ меняет поле, но его статус не определён — {reason}')

    if result['issues'] and result['status'] == OK:
        result['status'] = REVIEW
    return result


def _set_from(result: dict, ex: Extraction, doc: Document, source_label: str):
    if not ex.candidates:
        return
    values = ex.distinct_values()
    first = ex.candidates[0]
    result['history'].append({'display': first.display if len(values) == 1 else ' / '.join(c.display for c in ex.candidates),
                              'file': doc.name, 'title': doc.title, 'citation': first.citation.to_dict()})
    if len(values) > 1:
        result.update(status=REVIEW, value=None, display='требует проверки', citation=first.citation.to_dict())
        result['issues'].append(f'{doc.name}: найдено несколько разных значений ('
                                + ', '.join(c.display for c in ex.candidates) + ')')
        result['candidates'] = [{'display': c.display, 'citation': c.citation.to_dict()} for c in ex.candidates]
        return
    result.update(status=OK, value=first.value, display=first.display, citation=first.citation.to_dict())
    for c in ex.candidates:
        for w in c.warnings:
            result['issues'].append(f'{doc.name}: {w}')
