'use strict';
const $=id=>document.getElementById(id);
let token=location.hash.slice(1)||sessionStorage.getItem('fullmoon-session');
if(token)sessionStorage.setItem('fullmoon-session',token);
history.replaceState(null,'',location.pathname);
let sheets=[];
const currency=value=>new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR'}).format(Number(value));
function message(text,error=false){$('message').textContent=text;$('message').className=error?'bad':'';}
function invalidate(){$('results').hidden=true;resetCart();}
async function api(path,payload){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Session':token||''},body:JSON.stringify(payload)});
 const data=await response.json();if(!response.ok)throw Error(data.error||'Erreur de connexion');return data;
}
function options(select,headers,pattern){select.replaceChildren();headers.forEach((label,i)=>{const option=new Option(`${i+1} · ${label||'Sans titre'}`,i);select.add(option);});const found=headers.findIndex(x=>pattern.test(x));select.value=String(Math.max(0,found));}
function mapping(){
 const sheet=sheets[Number($('sheet').value)];if(!sheet)return;
 const index=Number($('header').value)-1;const headers=sheet.rows[index]||[];
 options($('reference'),headers,/réf|ref|sku|code article/i);options($('quantity'),headers,/quant|qty|qté|qte|nb/i);options($('price'),headers,/prix|price|tarif/i);invalidate();
}
$('file').addEventListener('change',async()=>{
 invalidate();$('check').disabled=true;const file=$('file').files[0];if(!file)return;
 try{
 if(file.size>8000000)throw Error('Fichier limité à 8 Mo.');message('Lecture du fichier de commande…');
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 const data=await api('/import',{name:file.name,data:btoa(binary)});sheets=data.sheets;
 $('sheet').replaceChildren(...sheets.map((s,i)=>new Option(s.name,i)));$('mapping').hidden=false;$('filename').textContent=file.name;mapping();$('check').disabled=false;message('Fichier chargé. Vérifiez les colonnes et renseignez la TVA.');
 }catch(error){message(error.message,true);}
});
$('sheet').addEventListener('change',mapping);$('header').addEventListener('change',mapping);
$('source').addEventListener('change',()=>{const show=$('source').value==='excel';$('pricebox').hidden=!show;$('modebox').hidden=!show;invalidate();});
for(const id of ['reference','quantity','price','shop','vat','mode'])$(id).addEventListener('input',invalidate);
function payload(){
 const sheet=sheets[Number($('sheet').value)];if(!sheet)throw Error('Choisissez un fichier Excel ou CSV.');
 const ref=Number($('reference').value),qty=Number($('quantity').value),price=Number($('price').value);
 if(ref===qty)throw Error('La référence et la quantité doivent utiliser deux colonnes distinctes.');
 const header=Number($('header').value);if(!Number.isInteger(header)||header<1)throw Error('Ligne d’en-tête invalide.');
 const rows=sheet.rows.slice(header).map((row,i)=>({reference:(row[ref]||'').trim(),quantity:(row[qty]||'').trim(),price:(row[price]||'').trim(),line:header+i+1})).filter(row=>row.reference||row.quantity);
 if(!rows.length||rows.length>500)throw Error('La commande doit contenir de 1 à 500 lignes.');
 return {rows,shop:$('shop').value,vat:$('vat').value,price_source:$('source').value,price_mode:$('mode').value};
}
function display(result){
 $('rows').replaceChildren();let requested=0,accepted=0;
 for(const row of result.rows){
 const tr=document.createElement('tr');const values=[`${row.line} · ${row.reference}`,row.name||'—',row.requested,row.available??'—',row.accepted,row.unit_ht===undefined?'—':new Intl.NumberFormat('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:6}).format(Number(row.unit_ht)),row.subtotal===undefined?'—':currency(row.subtotal),row.error||(!row.accepted?'Rupture':row.missing?'Quantité réduite':'Disponible')];
 values.forEach((value,i)=>{const td=document.createElement('td');td.textContent=value;if(i===7)td.className=row.error?'bad':row.missing?'warn':'good';tr.append(td);});$('rows').append(tr);requested+=Number(row.requested)||0;accepted+=row.accepted;
 }
 $('requested').textContent=requested;$('accepted').textContent=accepted;$('missing').textContent=requested-accepted;$('total').textContent=currency(result.ttc);$('checked').textContent='Vérifié le '+new Date(result.checked_at).toLocaleString('fr-FR');$('results').hidden=false;
}
async function run(){
 $('check').disabled=true;
 try{
  const input=payload();message('Contrôle des références, prix et stocks via MCP…');
  const result=await api('/check',input);display(result);
  const errors=result.rows.some(row=>row.error);
  message(errors?'Des lignes nécessitent une correction.':'Contrôle terminé. Les disponibilités sont affichées.',errors);
 }catch(error){message(error.message,true);}
 finally{$('check').disabled=false;}
}
$('check').addEventListener('click',run);
if(!token)message('Ouvrez l’outil avec « Lancer FullMoon.command » pour créer une session locale sécurisée.',true);

let cartTicket=null;
function cartMessage(text,error=false){$('cartmessage').textContent=text;$('cartmessage').className=error?'bad':'good';}
function resetCart(){cartTicket=null;$('cartconfirmation').hidden=true;}
for(const id of ['cartref','cartdelivery','cartbilling','customersearch'])$(id).addEventListener('input',resetCart);
$('shop').addEventListener('input',()=>{$('customerfields').hidden=true;$('cartcustomer').replaceChildren();});
async function cartAction(action){
 const controls=[...document.querySelectorAll('button,input,select,textarea')].map(el=>[el,el.disabled]);
 controls.forEach(([el])=>el.disabled=true);
 try{await action();}catch(error){cartMessage(error.message,true);}
 finally{controls.forEach(([el,disabled])=>el.disabled=disabled);}
}
$('activatecart').addEventListener('click',()=>cartAction(async()=>{
 const key=$('cartkey').value.trim();$('cartkey').value='';
 await api('/cart/key',{key,shop:$('shop').value});
 $('keystatus').textContent='Clé vérifiée et active pour cette session. Seule la création de paniers est autorisée.';
 $('cartconfig').open=false;cartMessage('Accès aux paniers activé.');
}));
$('forgetcart').addEventListener('click',()=>cartAction(async()=>{
 await api('/cart/forget',{});$('cartkey').value='';resetCart();$('keystatus').textContent='Clé oubliée.';cartMessage('Accès aux paniers désactivé.');
}));
async function customerAddresses(){
 resetCart();$('cartdelivery').replaceChildren();$('cartbilling').replaceChildren();
 const result=await api('/cart/details',{shop:$('shop').value,cart:{customer:$('cartcustomer').value}});
 const addressLabel=a=>[a.alias,a.company,a.address1,a.address2,a.postcode,a.city].filter(Boolean).join(' · ');
 for(const id of ['cartdelivery','cartbilling'])$(id).replaceChildren(new Option('Choisir une adresse',''),...result.addresses.map(a=>new Option(addressLabel(a),a.id)));
 $('customerfields').hidden=false;
 if(!result.addresses.length)throw Error('Ce client n’a pas d’adresse utilisable. Ajoutez son adresse dans PrestaShop.');
 cartMessage('Client trouvé. Sélectionnez les adresses de livraison et de facturation.');
}
$('findcustomer').addEventListener('click',()=>cartAction(async()=>{
 resetCart();$('customerfields').hidden=true;$('cartcustomer').replaceChildren();
 const result=await api('/cart/customers',{shop:$('shop').value,search:$('customersearch').value});
 $('cartcustomer').replaceChildren(...result.customers.map(c=>new Option(`#${c.id} · ${[c.company,c.firstname,c.lastname,c.email].filter(Boolean).join(' · ')}`,c.id)));
 await customerAddresses();
}));
$('cartcustomer').addEventListener('change',()=>cartAction(customerAddresses));
$('preparecart').addEventListener('click',()=>cartAction(async()=>{
 resetCart();const input=payload();
 if(!$('cartcustomer').value||!$('cartdelivery').value||!$('cartbilling').value)throw Error('Recherchez le client puis sélectionnez ses deux adresses.');
 if(!$('cartref').value.trim())throw Error('Renseignez la référence de commande du client.');
 input.cart={customer:$('cartcustomer').value,delivery:$('cartdelivery').value,billing:$('cartbilling').value,order_ref:$('cartref').value.trim()};
 cartMessage('Vérification du client, des produits et des stocks via MCP…');
 const result=await api('/cart/prepare',input);display(result.result);cartTicket=result.ticket;
 $('cartsummary').textContent=`Commande ${result.order_ref} — ${result.customer} — ${result.quantity} unités retenues. Livraison : ${$('cartdelivery').selectedOptions[0].textContent}. Facturation : ${$('cartbilling').selectedOptions[0].textContent}.`;
 $('cartconfirmation').hidden=false;cartMessage('Panier prêt à être créé. Vérifiez le récapitulatif et les quantités retenues.');
}));
async function loadCartHistory(){
 const result=await api('/cart/history',{});$('carthistory').replaceChildren();
 if(!result.items.length){$('carthistory').textContent='Aucun envoi enregistré.';return;}
 const labels={verified:'Créé et vérifié',created_unverified:'Créé — vérification à terminer',uncertain:'Envoi incertain — contrôle manuel requis'};
 for(const item of result.items){
  const row=document.createElement('p');row.textContent=`${item.order_ref} · Client #${item.customer} · ${labels[item.state]||item.state}${item.cart_id?' · Panier #'+item.cart_id:''}`;
  if(item.state==='created_unverified'){
   const retry=document.createElement('button');retry.className='secondary';retry.textContent='Revérifier le panier existant';
   retry.addEventListener('click',()=>cartAction(async()=>{const data=await api('/cart/create',{ticket:item.operation});cartMessage(`Panier n° ${data.cart_id} vérifié.`);await loadCartHistory();}));row.append(retry);
  }
  $('carthistory').append(row);
 }
}
$('refreshcarts').addEventListener('click',()=>cartAction(loadCartHistory));
$('createcart').addEventListener('click',()=>cartAction(async()=>{
 if(!cartTicket)throw Error('Préparez à nouveau le panier.');
 cartMessage('Dernier contrôle du stock, création puis relecture du panier dans PrestaShop…');
 try{
  const result=await api('/cart/create',{ticket:cartTicket});
  if(result.result)display(result.result);resetCart();
  cartMessage(`Panier n° ${result.cart_id} créé et vérifié dans PrestaShop pour ${result.order_ref}. Retrouvez-le dans Commandes → Paniers, puis créez la commande depuis le back-office. Vérifiez le transport, les prix, les taxes et les stocks avant validation.`);
 }catch(error){resetCart();throw error;}
 finally{await loadCartHistory();}
}));

let directoryClients=[],directoryVersion=0,contactVersion=0;
function clearContact(){contactVersion++;$('clientcontact').hidden=true;$('clientemail').textContent='';$('clientaddresses').replaceChildren();}
function showDirectory(){
 clearContact();
 const term=$('clientfilter').value.trim().toLocaleLowerCase('fr-FR');
 const matches=directoryClients.filter(c=>[c.id,c.firstname,c.lastname,c.company].join(' ').toLocaleLowerCase('fr-FR').includes(term));
 $('clientdirectory').replaceChildren(new Option(matches.length?'Choisir un client':'Aucun client correspondant',''),...matches.map(c=>new Option([c.company,[c.firstname,c.lastname].filter(Boolean).join(' '),`#${c.id}`].filter(Boolean).join(' · '),c.id)));
 $('clientinfo').textContent='';
 $('directorystatus').textContent=`${matches.length} client(s) affiché(s) sur ${directoryClients.length} chargé(s).`;
}
$('loadclients').addEventListener('click',async()=>{
 const version=++directoryVersion,shop=$('shop').value;clearContact();
 $('loadclients').disabled=true;$('clientdirectory').disabled=true;$('clientfilter').disabled=true;
 $('clientinfo').textContent='';$('directorystatus').textContent='Chargement des clients actifs via le MCP…';
 try{
  const result=await api('/customers/list',{shop});if(version!==directoryVersion)return;
  directoryClients=result.customers;$('clientfilter').value='';showDirectory();
  $('clientdirectory').disabled=false;$('clientfilter').disabled=false;
  $('directorystatus').textContent+=` Actualisé le ${new Date(result.checked_at).toLocaleString('fr-FR')}.`;
 }catch(error){if(version===directoryVersion){directoryClients=[];$('clientdirectory').replaceChildren(new Option('Chargement indisponible',''));$('directorystatus').textContent=error.message;}}
 finally{if(version===directoryVersion)$('loadclients').disabled=false;}
});
$('clientfilter').addEventListener('input',showDirectory);
$('clientdirectory').addEventListener('change',async()=>{
 clearContact();const version=contactVersion,customer=$('clientdirectory').value,shop=$('shop').value;
 if(!customer){$('clientinfo').textContent='';return;}
 $('clientinfo').textContent='Lecture des coordonnées via le MCP…';
 try{
  const data=await api('/customers/contact',{shop,customer});if(version!==contactVersion)return;
  $('clientinfo').textContent=`Client #${data.customer.id} — ${[data.customer.company,data.customer.firstname,data.customer.lastname].filter(Boolean).join(' · ')}`;
  $('clientemail').textContent='E-mail : '+(data.customer.email||'Non renseigné');
  if(!data.addresses.length){$('clientaddresses').textContent='Aucune adresse enregistrée.';}
  for(const address of data.addresses){
   const article=document.createElement('article');article.className='contactaddress';
   const title=document.createElement('strong');title.textContent=address.alias||`Adresse #${address.id}`;article.append(title);
   const details=document.createElement('p');details.style.whiteSpace='pre-line';
   details.textContent=[address.company,[address.firstname,address.lastname].filter(Boolean).join(' '),address.address1,address.address2,[address.postcode,address.city].filter(Boolean).join(' '),address.country].filter(Boolean).join('\n');
   article.append(details);$('clientaddresses').append(article);
  }
  $('clientcontact').hidden=false;
 }catch(error){if(version===contactVersion)$('clientinfo').textContent=error.message;}
});
$('shop').addEventListener('input',()=>{
 clearContact();directoryVersion++;directoryClients=[];$('clientfilter').value='';$('clientfilter').disabled=true;
 $('clientdirectory').replaceChildren(new Option('Rechargez le répertoire de cette boutique',''));$('clientdirectory').disabled=true;
 $('loadclients').disabled=false;$('clientinfo').textContent='';$('directorystatus').textContent='Boutique modifiée. Rechargez les clients.';
});
