"""Regression coverage for final-audit N3–N6, independent of formal gold."""
import unittest
from datetime import date
from demo.planner import local_plan, parse_transfer


class FinalQueryEdges(unittest.TestCase):
    def plan(self, text):
        return local_plan(text, date(2026, 10, 26), ())

    def test_invalid_or_ambiguous_amounts_clarify(self):
        for amount in ('-25', '25.999', '0', '10000.01', '25元或30', '1,00', '1万', '25.00.1', '−25', '25分', '25美元', '25k'):
            with self.subTest(amount=amount):
                result, message = parse_transfer(f'给测试用户B转{amount}元')
                self.assertIsNone(result)
                self.assertTrue(message)

    def test_valid_amounts(self):
        for amount, cents in [('25', 2500), ('0.01', 1), ('10000', 1000000), ('25.99', 2599)]:
            with self.subTest(amount=amount):
                result, message = parse_transfer(f'给测试用户B转{amount}元')
                self.assertIsNone(message)
                self.assertEqual(result, {'recipient_user': 'isolation_user', 'amount_cents': cents})

    def test_grouped_amount_never_partially_parsed(self):
        result, message = parse_transfer('给测试用户B转1,000元')
        self.assertTrue(result is None and message or result['amount_cents'] == 100000)

    def test_named_accounts(self):
        for name, expected in [('主账户', 'acc_daily'), ('日常账户', 'acc_daily'), ('储蓄账户', 'acc_savings'), ('acc_other', 'acc_other')]:
            with self.subTest(name=name):
                self.assertEqual(self.plan(f'2026年9月{name}支出')['plan']['filters']['account_id'], expected)
        self.assertEqual(self.plan('2026年9月投资账户支出')['route'], 'clarification')

    def test_enumerated_months_same_year(self):
        a = self.plan('8月和9月各类别支出')['plan']
        b = self.plan('9月和8月各类别支出')['plan']
        self.assertEqual((a['start'], a['end_exclusive']), (b['start'], b['end_exclusive']))
        self.assertEqual(b['start'], '2026-08-01')

    def test_cross_year_range(self):
        result = self.plan('2025年12月到2026年2月支出')['plan']
        self.assertEqual((result['start'], result['end_exclusive']), ('2025-12-01', '2026-03-01'))

    def test_daily_qualifiers_never_widen(self):
        for text in ('2026年9月1日支出', '2026年9月30日支出', '2026-09-01支出', '2026年2月30日支出', '2026年13月支出', '本月1日支出', '2026年9月一日支出', '2026年9月初支出', '2026年9月上旬支出', '2026年9月中旬支出', '2026年9月末支出'):
            with self.subTest(text=text):
                result = self.plan(text)
                self.assertEqual(result['route'], 'clarification')

    def test_disjoint_month_enumeration_clarifies(self):
        self.assertEqual(self.plan('10月和8月各类别支出')['route'], 'clarification')

    def test_unsupported_amount_units_exact(self):
        for text in ('给测试用户B转−25元', '给测试用户B转25分', '给测试用户B转25美元', '给测试用户B转25k元'):
            with self.subTest(text=text):
                result, message = parse_transfer(text)
                self.assertIsNone(result)
                self.assertTrue(message)

    def test_unknown_card_accounts_clarify(self):
        for name in ('信用卡', '工资卡', '银行卡', '储蓄卡'):
            with self.subTest(name=name):
                self.assertEqual(self.plan(f'2026年9月{name}支出')['route'], 'clarification')

class TransferSignBoundaryTests(unittest.TestCase):
    def test_chinese_negative_and_leading_decimal_are_not_salvaged(self):
        from demo.planner import parse_transfer
        for text in ('给测试用户B转负25元', '给测试用户B转負25元', '给测试用户B转.25元', '给测试用户B转＋25元'):
            with self.subTest(text=text):
                parsed, clarification = parse_transfer(text)
                self.assertIsNone(parsed)
                self.assertTrue(clarification)
