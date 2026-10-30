"""Run: python -m unittest -v"""
import unittest
from datetime import date
from pathlib import Path

from contract_checker.resolver import analyze, OK, NO_DATA, REVIEW, APPLIED, PROPOSAL, NOT_EFFECTIVE, NEEDS_REVIEW
from contract_checker.questions import answer
from contract_checker.server import decode_text
from contract_checker.textutil import parse_number_words, find_amounts

SAMPLES = Path(__file__).resolve().parent.parent / 'samples'
CHECK = date(2026, 3, 10)


def samples() -> dict[str, str]:
    return {p.name: p.read_text(encoding='utf-8') for p in sorted(SAMPLES.glob('*.txt'))}


def by_number(analysis) -> dict:
    return {c['number']: c for c in analysis['contracts']}


def run(files: dict[str, str], check=CHECK) -> dict:
    return by_number(analyze(list(files.items()), check))


NEW_CONTRACT = """ДОГОВОР ПОСТАВКИ № STN-104
Дата: 01.03.2026
Статус: подписан обеими сторонами 01.03.2026.

1.1. Покупатель: ООО «СеверМет». Поставщик: ООО «Альфа Трейд».
1.2. Предмет: поставка насосов.

2.1. Общая стоимость поставки составляет 2 450 000 (два миллиона четыреста пятьдесят тысяч) рублей, включая НДС. Указанная сумма — итоговая стоимость договора.
2.2. Оплата производится не позднее 15.05.2026.

3.1. Поставщик обязуется поставить насосы не позднее 25.03.2026.

5.1. Изменения условий действуют после подписания дополнительного соглашения обеими сторонами и наступления указанной в нем даты вступления в силу.
"""


def amendment(contract='STN-104', status='подписано обеими сторонами 05.03.2026.',
              effective='4. Настоящее соглашение вступает в силу 05.03.2026.',
              body='2. Пункт 3.1 договора изложить в следующей редакции: «Поставщик обязуется поставить насосы не позднее 05.04.2026».',
              supplier='ООО «Альфа Трейд»'):
    return f"""ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1 К ДОГОВОРУ {contract}
Дата: 05.03.2026
Статус: {status}
Покупатель: ООО «СеверМет». Поставщик: {supplier}.

{body}

{effective}
"""


class SampleDocumentsTest(unittest.TestCase):
    def setUp(self):
        self.c = run(samples())

    def test_three_contracts_with_suppliers(self):
        self.assertEqual(sorted(self.c), ['STN-101', 'STN-102', 'STN-103'])
        self.assertEqual(self.c['STN-101']['supplier']['value'], 'ООО «Вектор Прибор»')
        self.assertEqual(self.c['STN-102']['supplier']['value'], 'ООО «Контур Электро»')
        self.assertEqual(self.c['STN-103']['supplier']['value'], 'ООО «ПромСнаб Сервис»')

    def test_signed_amendment_changes_stn101(self):
        f = self.c['STN-101']['fields']
        self.assertEqual((f['amount']['status'], f['amount']['value']), (OK, 1_380_000))
        self.assertEqual((f['deadline']['status'], f['deadline']['value']), (OK, date(2026, 4, 30)))
        self.assertEqual(f['deadline']['changed_by']['file'], '02_amendment_signed.txt')
        self.assertEqual(f['deadline']['original']['display'], '15.04.2026')
        self.assertIn('30.04.2026', f['deadline']['citation']['quote'])

    def test_draft_is_proposal_not_applied(self):
        f = self.c['STN-101']['fields']
        self.assertEqual([(p['display'], p['role']) for p in f['deadline']['proposals']], [('10.05.2026', PROPOSAL)])
        self.assertEqual([p['value'] for p in f['amount']['proposals']], [1_450_000])

    def test_stn102_payment_date_and_advance_are_not_used(self):
        f = self.c['STN-102']['fields']
        self.assertEqual(f['amount']['value'], 840_000)
        self.assertEqual(f['deadline']['value'], date(2026, 3, 20))
        self.assertEqual([i['display'].replace(' ', ' ') for i in f['amount']['ignored']], ['252 000 ₽'])

    def test_letter_is_proposal(self):
        props = self.c['STN-102']['fields']['deadline']['proposals']
        self.assertEqual([(p['value'], p['file'], p['role']) for p in props],
                         [(date(2026, 3, 27), '05_supplier_email.txt', PROPOSAL)])

    def test_stn103_amount_is_no_data_not_budget_limit(self):
        f = self.c['STN-103']['fields']['amount']
        self.assertEqual((f['status'], f['value'], f['display']), (NO_DATA, None, 'нет данных'))
        self.assertIn('приложении № 1', f['hints'][0]['quote'])

    def test_citations_point_to_exact_text(self):
        files = samples()
        for c in self.c.values():
            for f in c['fields'].values():
                cite = f['citation']
                if cite:
                    line = files[cite['file']].splitlines()[cite['line'] - 1]
                    self.assertEqual(line[cite['start']:cite['end']], cite['quote'])

    def test_file_names_and_order_do_not_matter(self):
        renamed = {f'{i}_upload.txt': text for i, text in enumerate(reversed(list(samples().values())))}
        c = run(renamed)
        self.assertEqual(c['STN-101']['fields']['deadline']['value'], date(2026, 4, 30))
        self.assertEqual(c['STN-102']['fields']['deadline']['value'], date(2026, 3, 20))

    def test_amendment_not_in_force_before_its_date(self):
        f = run(samples(), date(2026, 2, 20))['STN-101']['fields']['deadline']
        self.assertEqual(f['value'], date(2026, 4, 15))
        self.assertIn(NOT_EFFECTIVE, [p['role'] for p in f['proposals']])


class QuestionsTest(unittest.TestCase):
    def ask(self, q):
        return answer(list(samples().items()), q, CHECK)

    def test_q1_deadline_stn101(self):
        a = self.ask('Какой срок поставки действует по договору STN-101 на 10 марта 2026 года?')
        self.assertEqual(a['items'][0]['result']['value'], date(2026, 4, 30))
        self.assertIn('30.04.2026', a['text'])

    def test_q2_amount_stn102(self):
        a = self.ask('Какова сумма договора STN-102?')
        self.assertEqual(a['items'][0]['result']['value'], 840_000)

    def test_q3_amount_stn103(self):
        a = self.ask('Какова сумма договора STN-103?')
        self.assertEqual(a['items'][0]['result']['status'], NO_DATA)
        self.assertIn('нет данных', a['text'])

    def test_q4_before_april(self):
        a = self.ask('По каким договорам поставка запланирована раньше 1 апреля 2026 года '
                     'и есть ли несогласованные изменения срока?')
        self.assertEqual(a['kind'], 'filter')
        self.assertIn('STN-102 (20.03.2026)', a['text'])
        self.assertIn('27.03.2026', a['text'])
        self.assertNotIn('STN-103', a['text'])

    def test_question_date_changes_as_of(self):
        a = self.ask('Какой срок поставки по договору STN-101 на 20.02.2026?')
        self.assertEqual(a['items'][0]['result']['value'], date(2026, 4, 15))

    def test_unrecognized_question(self):
        self.assertEqual(self.ask('Что нового?')['kind'], 'unknown')


class NewAndChangedDocumentsTest(unittest.TestCase):
    def test_new_contract_same_format(self):
        c = run({**samples(), 'new.txt': NEW_CONTRACT})['STN-104']
        self.assertEqual(c['supplier']['value'], 'ООО «Альфа Трейд»')
        self.assertEqual(c['fields']['amount']['value'], 2_450_000)
        self.assertEqual(c['fields']['deadline']['value'], date(2026, 3, 25))  # not the payment date
        self.assertFalse(c['needs_review'])

    def test_changed_amount_field(self):
        files = samples()
        files['03_contract.txt'] = files['03_contract.txt'].replace(
            '840 000 (восемьсот сорок тысяч)', '910 000 (девятьсот десять тысяч)')
        self.assertEqual(run(files)['STN-102']['fields']['amount']['value'], 910_000)

    def test_changed_deadline_field(self):
        files = samples()
        files['04_contract.txt'] = files['04_contract.txt'].replace('30.04.2026', '28.03.2026')
        a = answer(list(files.items()), 'По каким договорам поставка раньше 01.04.2026?', CHECK)
        self.assertIn('STN-103 (28.03.2026)', a['text'])

    def test_digits_and_words_mismatch_needs_review(self):
        files = samples()
        files['03_contract.txt'] = files['03_contract.txt'].replace('840 000 (', '850 000 (')
        f = run(files)['STN-102']['fields']['amount']
        self.assertEqual(f['status'], REVIEW)
        self.assertTrue(any('прописью' in i for i in f['issues']))

    def test_other_price_wording(self):
        text = NEW_CONTRACT.replace('Общая стоимость поставки составляет', 'Цена договора составляет')
        self.assertEqual(run({'c.txt': text})['STN-104']['fields']['amount']['value'], 2_450_000)

    def test_textual_date(self):
        text = NEW_CONTRACT.replace('не позднее 25.03.2026', 'не позднее 25 марта 2026 г.')
        self.assertEqual(run({'c.txt': text})['STN-104']['fields']['deadline']['value'], date(2026, 3, 25))

    def test_missing_deadline_is_no_data(self):
        text = NEW_CONTRACT.replace('3.1. Поставщик обязуется поставить насосы не позднее 25.03.2026.\n', '')
        f = run({'c.txt': text})['STN-104']['fields']['deadline']
        self.assertEqual((f['status'], f['value'], f['display']), (NO_DATA, None, 'нет данных'))

    def test_signed_amendment_applies(self):
        c = run({'c.txt': NEW_CONTRACT, 'a.txt': amendment()})['STN-104']
        self.assertEqual(c['related'][0]['role'], APPLIED)
        self.assertEqual(c['fields']['deadline']['value'], date(2026, 4, 5))

    def test_amendment_effective_after_check_date_not_applied(self):
        c = run({'c.txt': NEW_CONTRACT,
                 'a.txt': amendment(effective='4. Настоящее соглашение вступает в силу 01.04.2026.')})['STN-104']
        self.assertEqual(c['related'][0]['role'], NOT_EFFECTIVE)
        self.assertEqual(c['fields']['deadline']['value'], date(2026, 3, 25))
        self.assertEqual(c['fields']['deadline']['proposals'][0]['value'], date(2026, 4, 5))

    def test_amendment_without_effective_date_needs_review(self):
        c = run({'c.txt': NEW_CONTRACT, 'a.txt': amendment(effective='')})['STN-104']
        self.assertEqual(c['related'][0]['role'], NEEDS_REVIEW)
        self.assertEqual(c['fields']['deadline']['status'], REVIEW)
        self.assertEqual(c['fields']['deadline']['value'], date(2026, 3, 25))

    def test_unsigned_amendment_is_proposal(self):
        c = run({'c.txt': NEW_CONTRACT, 'a.txt': amendment(status='не подписано сторонами.')})['STN-104']
        self.assertEqual(c['related'][0]['role'], PROPOSAL)
        self.assertEqual(c['fields']['deadline']['value'], date(2026, 3, 25))

    def test_amendment_with_other_supplier_needs_review(self):
        c = run({'c.txt': NEW_CONTRACT, 'a.txt': amendment(supplier='ООО «Другая»')})['STN-104']
        self.assertEqual(c['related'][0]['role'], NEEDS_REVIEW)
        self.assertEqual(c['fields']['deadline']['value'], date(2026, 3, 25))

    def test_amendment_to_unknown_contract_is_unlinked(self):
        a = analyze([('c.txt', NEW_CONTRACT), ('a.txt', amendment(contract='STN-999'))], CHECK)
        self.assertEqual([u['file'] for u in a['unlinked']], ['a.txt'])

    def test_two_different_amounts_need_review(self):
        text = NEW_CONTRACT.replace('2.2. Оплата', '2.2. Общая стоимость поставки составляет 2 500 000 рублей. Оплата')
        f = run({'c.txt': text})['STN-104']['fields']['amount']
        self.assertEqual((f['status'], f['value']), (REVIEW, None))


class TextUtilTest(unittest.TestCase):
    def test_number_words(self):
        self.assertEqual(parse_number_words('один миллион триста восемьдесят тысяч'), 1_380_000)
        self.assertEqual(parse_number_words('восемьсот сорок тысяч'), 840_000)
        self.assertEqual(parse_number_words('двадцать одна тысяча пятьсот'), 21_500)
        self.assertIsNone(parse_number_words('сто рублей ноль копеек'))

    def test_amount_with_kopecks_and_nbsp(self):
        self.assertEqual(find_amounts('составляет 1 200 000,50 руб.')[0].value, 1_200_000.5)

    def test_decode_cp1251(self):
        self.assertEqual(decode_text('Договор'.encode('cp1251')), 'Договор')
        self.assertEqual(decode_text('﻿Договор'.encode('utf-8')), 'Договор')


if __name__ == '__main__':
    unittest.main()
