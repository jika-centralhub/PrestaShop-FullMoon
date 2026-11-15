import sys, unittest, asyncio
from pathlib import Path
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'app'))
import app
class Checks(unittest.TestCase):
 def setUp(self):
  self.catalog={'A':{'stock_id':'1','available':5,'catalog_price':'2.50','name':'Produit A'},'B':{'stock_id':'1','available':5,'catalog_price':'2.50','name':'Même stock'}}
 def test_shared_stock(self):
  r=app.allocate([{'reference':'A','quantity':4},{'reference':'B','quantity':4}],self.catalog,'catalog')
  self.assertEqual([x['accepted'] for x in r['rows']],[4,1]);self.assertEqual(r['ht'],'12.50')
 def test_shortage(self):
  r=app.allocate([{'reference':'A','quantity':99}],self.catalog,'catalog')
  self.assertEqual(r['rows'][0]['accepted'],5)
 def test_invalid(self):
  for q in ['-1','1.5','NaN','Infinity','=1+1','',0]:
   r=app.allocate([{'reference':'A','quantity':q}],self.catalog,'catalog')
   self.assertTrue(r['rows'][0]['error']);self.assertEqual(r['rows'][0]['accepted'],0)
 def test_unknown(self):
  r=app.allocate([{'reference':'X','quantity':2}],{'X':{'error':'Référence inconnue'}},'catalog')
  self.assertTrue(r['rows'][0]['error'])
 def test_excel_ht(self):
  r=app.allocate([{'reference':'A','quantity':3,'price':'12,00'}],self.catalog,'excel')
  self.assertEqual(r['ht'],'36.00');self.assertNotIn('tax',r);self.assertNotIn('ttc',r)
 def test_zero_stock(self):
  self.catalog['A']['available']=0
  r=app.allocate([{'reference':'A','quantity':2}],self.catalog,'catalog')
  self.assertEqual(r['rows'][0]['accepted'],0)
if __name__ == '__main__': unittest.main()
