import sys, unittest, base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'app'))
import app
class CSVTests(unittest.TestCase):
 def test_formats(self):
  for delimiter in [';',',','\t','|']:
   for encoding in ['utf-8-sig','utf-8','utf-16','cp1252']:
    text=f'Référence{delimiter}Quantité{delimiter}Nom\n005006{delimiter}4{delimiter}"Pêche, citron; frais"\n'
    rows=app.read_csv(text.encode(encoding))
    self.assertEqual(rows[0][0],'Référence');self.assertEqual(rows[1],['005006','4','Pêche, citron; frais'])
 def test_sep(self):
  self.assertEqual(app.read_csv(b'sep=;\r\nRef;Qty\r\n001;2\r\n')[1],['001','2'])
 def test_multiline(self):
  self.assertEqual(app.read_csv(b'Ref;Qty;Nom\n001;2;"a\nb"\n')[1][2],'a\nb')
 def test_invalid(self):
  for data in [b'',b'\n',b'Ref;Qty\n"unclosed;2\n']:
   with self.assertRaises(ValueError):app.read_csv(data)

if __name__ == '__main__': unittest.main()
