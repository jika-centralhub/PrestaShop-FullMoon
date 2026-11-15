import sys, tempfile, unittest, json
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'app'))
import carts, app

PROFILE=dict(id_customer=1,id_address_delivery=10,id_address_invoice=11,id_currency=1,id_lang=1,id_shop=1,id_shop_group=1,label='Client test',secure_key='a'*32)
RESULT={'rows':[dict(product_id=5,attribute_id=0,accepted=3,error=''),dict(product_id=5,attribute_id=0,accepted=2,error='')]}
PAYLOAD=dict(rows=[dict(reference='X',quantity=10)],shop='1',price_source='catalog',cart=dict(customer='1',delivery='10',billing='11',order_ref='TEST-1'))
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.s=carts.CartService(Path(self.tmp.name));self.posts=0
  self.ticket=self.s.prepare(PAYLOAD,RESULT,PROFILE)['ticket']
  self.expected=carts.expected_cart(PROFILE,carts.merged_rows(RESULT))
 def response(self,expected=None):
  root=ET.fromstring(carts.cart_xml(expected or self.expected,PROFILE['secure_key']))
  ET.SubElement(root.find('cart'),'id').text='99';return root
 def network(self,method,path,**kwargs):
  if method=='POST': self.posts+=1
  return self.response()
 def test_merge_duplicate_lines(self):
  self.assertEqual(self.expected['rows'],[dict(id_product=5,id_product_attribute=0,quantity=5,id_address_delivery=10)])
 def test_create_verify_and_retry_without_second_post(self):
  with patch.object(self.s.writer,'request',side_effect=self.network):
   self.assertEqual(self.s.commit(self.ticket,RESULT,PROFILE)['cart_id'],99)
   self.assertEqual(self.s.commit(self.ticket,RESULT,PROFILE)['cart_id'],99)
   fresh=carts.CartService(Path(self.tmp.name))
   self.assertEqual(fresh.existing(self.ticket)['cart_id'],99)
  self.assertEqual(self.posts,1)
 def test_changed_stock_no_write(self):
  result={'rows':[dict(product_id=5,attribute_id=0,accepted=4,error='')]}
  with patch.object(self.s.writer,'request') as req:
   with self.assertRaisesRegex(ValueError,'changé'):self.s.commit(self.ticket,result,PROFILE)
   req.assert_not_called()
 def test_changed_customer_no_write(self):
  with patch.object(self.s.writer,'request') as req:
   with self.assertRaises(ValueError):self.s.commit(self.ticket,RESULT,{**PROFILE,'id_customer':2})
   req.assert_not_called()
 def test_timeout_blocks_retry_and_duplicate_reference(self):
  with patch.object(self.s.writer,'request',side_effect=TimeoutError) as req:
   with self.assertRaisesRegex(ValueError,'incertain'):self.s.commit(self.ticket,RESULT,PROFILE)
   with self.assertRaisesRegex(ValueError,'incertain'):self.s.commit(self.ticket,RESULT,PROFILE)
   self.assertEqual(req.call_count,1)
  with self.assertRaises(ValueError):self.s.prepare(PAYLOAD,RESULT,PROFILE)
 def test_bad_readback_keeps_id_and_only_retries_get(self):
  bad={**self.expected,'rows':[{**self.expected['rows'][0],'quantity':4}]}
  def call(method,path,**kw):
   if method=='POST':self.posts+=1;return self.response()
   return self.response(bad)
  with patch.object(self.s.writer,'request',side_effect=call):
   with self.assertRaisesRegex(ValueError,'99'):self.s.commit(self.ticket,RESULT,PROFILE)
  with patch.object(self.s.writer,'request',side_effect=self.network):
   self.assertTrue(self.s.existing(self.ticket)['verified'])
  self.assertEqual(self.posts,1)
 def test_expired_ticket_no_write(self):
  self.s.previews[self.ticket]['expires']=0
  with patch.object(self.s.writer,'request') as req:
   with self.assertRaisesRegex(ValueError,'expirée'):self.s.commit(self.ticket,RESULT,PROFILE)
   req.assert_not_called()
 def test_empty_or_error_blocks(self):
  for result in [{'rows':[]},{'rows':[dict(error='unknown',accepted=0)]}]:
   with self.assertRaises(ValueError):carts.merged_rows(result)
 def test_credentials_absent_from_ledger_and_preview(self):
  with patch.object(self.s.writer,'request',side_effect=self.network):self.s.commit(self.ticket,RESULT,PROFILE)
  self.assertNotIn('a'*32,json.dumps(self.s.previews))
  self.assertNotIn(b'a'*32,(self.s.folder/'registre.sqlite').read_bytes())
 def test_destination_methods_restricted(self):
  for method,path in [('POST','/orders'),('PUT','/carts/1'),('DELETE','/carts/1'),('GET','/customers'),('POST','/carts?ws_key=x'),('POST','https://evil.test')]:
   with self.assertRaises(ValueError):self.s.writer.request(method,path)
 def test_permissions_reject_extra_write_and_readonly(self):
  good='<prestashop><api><carts get="true" post="true" put="false" delete="false"/></api></prestashop>'
  for xml,valid in [(good,True),(good.replace('put="false"','put="true"'),False),(good.replace('post="true"','post="false"'),False),(good.replace('</api>','<orders post="true"/></api>'),False)]:
   with patch.object(self.s.writer,'request',return_value=ET.fromstring(xml)):
    if valid:self.s.writer.verify_permissions()
    else:
     with self.assertRaises(ValueError):self.s.writer.verify_permissions()
 def test_xml_entity_refused(self):
  with self.assertRaises(ValueError):carts.parse_xml(b'<!DOCTYPE x [<!ENTITY x "x">]><prestashop/>')
 def test_customer_address_ownership(self):
  async def call(session,name,args):
   return {'customers':dict(id=1,id_shop=1,active='1',deleted='0'),'shops':dict(active='1')}[args['resource']]
  async def query(*args,**kw):return [dict(id=20,id_customer=2,deleted='0')]
  import asyncio
  with patch.object(app,'call',side_effect=call),patch.object(app,'query',side_effect=query):
   with self.assertRaisesRegex(ValueError,'adresses'):
    asyncio.run(app.cart_profile(None,PAYLOAD,True))
if __name__=='__main__':unittest.main()
