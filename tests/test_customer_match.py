import sys
import unittest
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import app
from pdf_import import customer_hint

@asynccontextmanager
async def session():
    yield object()

class CustomerMatchTests(unittest.IsolatedAsyncioTestCase):
    async def match(self, rows, email='buyer@example.test'):
        query = AsyncMock(return_value=rows)
        with patch.object(app, 'mcp_session', session), patch.object(app, 'query', query):
            result = await app.customer_match({'shop': 1, 'email': email})
        self.assertEqual(query.call_args.args[1], 'customers')
        self.assertNotIn('secure_key', query.call_args.args[3])
        return result

    def customer(self, **values):
        return dict(id='10', email='buyer@example.test', active='1', deleted='0', id_shop='1', **values)

    async def test_unique_case_insensitive_and_minimal(self):
        result = await self.match([self.customer(secure_key='never-return-this')], 'BUYER@example.test')
        self.assertEqual(result['status'], 'unique')
        self.assertNotIn('secure_key', result['customers'][0])

    async def test_missing(self):
        self.assertEqual((await self.match([]))['status'], 'missing')

    async def test_duplicates_require_selection(self):
        self.assertEqual((await self.match([self.customer(), self.customer()]))['status'], 'ambiguous')

    async def test_reject_inactive_deleted_other_shop_or_approximate(self):
        for key, value in [('active','0'), ('deleted','1'), ('id_shop','2'), ('email','other@example.test')]:
            row=self.customer();row[key]=value
            self.assertEqual((await self.match([row]))['status'], 'missing')

    async def test_filter_injection(self):
        with self.assertRaises(ValueError):
            await app.customer_match({'shop':1,'email':'buyer%[@example.test'})

class HintTests(unittest.TestCase):
    def test_buyer_label_required(self):
        self.assertIsNone(customer_hint('Fournisseur : Test\nEmail : supplier@example.test')['email'])

    def test_ambiguity(self):
        self.assertIsNone(customer_hint('Acheteur : Test\nEmail : buyer@example.test\nEmail : supplier@example.test')['email'])

    def test_exact_email(self):
        self.assertEqual(customer_hint('Acheteur : Test\nEmail : BUYER@example.test')['email'], 'buyer@example.test')
