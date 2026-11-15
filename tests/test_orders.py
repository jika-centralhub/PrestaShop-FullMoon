import sys, unittest, asyncio
from pathlib import Path
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'app'))
import app
class Checks(unittest.TestCase):
 def setUp(self):
  self.catalog={'A':{'stock_id':'1','available':5,'catalog_price':'2.50','name':'Produit A'},'B':{'stock_id':'1','available':5,'catalog_price':'2.50','name':'Même stock'}}
 def test_shared_stock(self):
  r=app.allocate([{'reference':'A','quantity':4},{'reference':'B','quantity':4}],self.catalog,Decimal(20),'ht','catalog')
  self.assertEqual([x['accepted'] for x in r['rows']],[4,1]);self.assertEqual(r['ttc'],'15.00')
 def test_shortage(self):
  r=app.allocate([{'reference':'A','quantity':99}],self.catalog,Decimal(20),'ht','catalog')
  self.assertEqual(r['rows'][0]['accepted'],5)
 def test_invalid(self):
  for q in ['-1','1.5','NaN','Infinity','=1+1','',0]:
   r=app.allocate([{'reference':'A','quantity':q}],self.catalog,Decimal(20),'ht','catalog')
   self.assertTrue(r['rows'][0]['error']);self.assertEqual(r['rows'][0]['accepted'],0)
 def test_unknown(self):
  r=app.allocate([{'reference':'X','quantity':2}],{'X':{'error':'Référence inconnue'}},Decimal(20),'ht','catalog')
  self.assertTrue(r['rows'][0]['error'])
 def test_ttc(self):
  r=app.allocate([{'reference':'A','quantity':3,'price':'12,00'}],self.catalog,Decimal(20),'ttc','excel')
  self.assertEqual((r['ht'],r['tax'],r['ttc']),('30.00','6.00','36.00'))
 def test_zero_stock(self):
  self.catalog['A']['available']=0
  r=app.allocate([{'reference':'A','quantity':2}],self.catalog,Decimal(20),'ht','catalog')
  self.assertEqual(r['rows'][0]['accepted'],0)
 def test_invoice_escape(self):
  p={'invoice':{'number':'TEST','seller':'<script>','buyer':'Client','terms':'Comptant','date':'2026-11-15'},'vat':'20'}
  r=app.allocate([{'reference':'A','quantity':9}],self.catalog,Decimal(20),'ht','catalog')
  doc=app.invoice(p,r);self.assertIn('&lt;script&gt;',doc);self.assertNotIn('<script>',doc);self.assertIn('<td>5</td>',doc)
 def test_missing_invoice(self):
  with self.assertRaises(ValueError):app.invoice({}, {'rows':[]})
if __name__ == '__main__': unittest.main()
