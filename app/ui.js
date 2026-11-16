'use strict';
const $=id=>document.getElementById(id);
let token=location.hash.slice(1)||sessionStorage.getItem('fullmoon-session');
if(token)sessionStorage.setItem('fullmoon-session',token);
history.replaceState(null,'',location.pathname);
let sheets=[],pdfReviewRequired=false,importVersion=0,checking=false,cartCreated=false,guideTimer=0;
// Lignes retirées de la commande, par numéro de ligne du fichier (1 = première ligne de la feuille).
// Vidé à chaque nouveau fichier, changement de feuille ou de ligne d'en-tête.
const removed=new Set();
// Quantités corrigées à la main, par numéro de ligne. Le fichier d'origine n'est pas modifié.
const edited=new Map();
const currency=value=>new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR'}).format(Number(value));
function message(text,error=false){$('message').textContent=text;$('message').className='notice'+(error?' bad':'');}
function setStep(){scheduleGuide();}
function invalidate(){cartCreated=false;$('results').hidden=true;resetCart();setStep();}
async function api(path,payload){
 const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Session':token||''},body:JSON.stringify(payload)});
 const data=await response.json();if(!response.ok)throw Error(data.error||'Erreur de connexion');return data;
}
function options(select,headers,pattern){select.replaceChildren();headers.forEach((label,i)=>{const option=new Option(`${i+1} · ${label||'Sans titre'}`,i);select.add(option);});const found=headers.findIndex(x=>pattern.test(x));select.value=String(Math.max(0,found));}
function mapping(){
 const sheet=sheets[Number($('sheet').value)];if(!sheet)return;
 const index=Number($('header').value)-1;const headers=sheet.rows[index]||[];
 options($('reference'),headers,/réf|ref|sku|code article/i);options($('quantity'),headers,/quant|qty|qté|qte|nb/i);options($('price'),headers,/prix|price|tarif/i);removed.clear();edited.clear();invalidate();
}
function lineButton(line){
 const button=document.createElement('button');button.type='button';button.className='copy linebtn';
 const off=removed.has(line);button.textContent=off?'Rétablir':'Retirer';
 button.title=off?'Remettre cette ligne dans la commande':'Retirer cette ligne de la commande';
 button.addEventListener('click',()=>toggleLines([line],!off));return button;
}
// Champ de quantité modifiable : la nouvelle valeur remplace celle du fichier pour cette ligne.
function quantityInput(line,value,original){
 const input=document.createElement('input');input.type='text';input.inputMode='numeric';input.className='qtyedit';input.value=value;
 input.setAttribute('aria-label',`Quantité de la ligne ${line}`);
 if(edited.has(line)){input.classList.add('changed');input.title=`Quantité du fichier : ${original||'vide'}`;}
 input.addEventListener('keydown',event=>{if(event.key==='Enter')input.blur();else if(event.key==='Escape'){input.value=value;input.blur();}});
 input.addEventListener('change',()=>setQuantity(line,input.value,original,input));
 return input;
}
async function setQuantity(line,raw,original,input){
 if(checking)return;
 const value=raw.replace(/\s/g,'');
 if(!/^\d{1,7}$/.test(value)||Number(value)<1){message(`Ligne ${line} : saisissez une quantité entière d’au moins 1. Pour écarter l’article, utilisez « Retirer ».`,true);input.value=edited.get(line)??original;return;}
 if(String(Number(value))===String(Number(original)))edited.delete(line);else edited.set(line,String(Number(value)));
 await refreshAfterEdit(`Quantité de la ligne ${line} : ${Number(value)}.`);
}
function renderPdfRows(){
 if(!pdfReviewRequired)return;
 $('pdfrows').replaceChildren();
 sheets[0].rows.slice(1).forEach((row,k)=>{
  const line=k+2,tr=document.createElement('tr');tr.classList.toggle('removed',removed.has(line));
  row.forEach((value,i)=>{const td=document.createElement('td');if(i===1&&!removed.has(line))td.append(quantityInput(line,edited.get(line)??value,value));else td.textContent=i===1?edited.get(line)??value:value;tr.append(td);});
  const td=document.createElement('td');td.append(lineButton(line));tr.append(td);$('pdfrows').append(tr);
 });
}
// Retire ou rétablit des lignes, puis relance le contrôle si des résultats étaient affichés :
// les doublons partageant un stock, les quantités retenues dépendent des lignes conservées.
async function toggleLines(lines,remove){
 if(checking||!lines.length)return;
 const all=sheetRows();
 const next=new Set(removed);for(const line of lines)remove?next.add(line):next.delete(line);
 if(!all.some(row=>!next.has(row.line))){message('Impossible de retirer toutes les lignes : la commande doit garder au moins un article.',true);return;}
 removed.clear();next.forEach(line=>removed.add(line));
 const count=all.filter(row=>removed.has(row.line)).length;
 await refreshAfterEdit(count?`${count} ligne(s) retirée(s) de la commande.`:'Toutes les lignes sont dans la commande.');
}
// Après un retrait ou une quantité modifiée : le panier préparé est annulé et le contrôle relancé
// si des résultats étaient affichés (les doublons partagent le même stock).
async function refreshAfterEdit(note){
 const shown=!$('results').hidden;cartCreated=false;resetCart();renderPdfRows();
 if(shown&&!(pdfReviewRequired&&!$('pdfconfirmed').checked))await run();
 else{$('results').hidden=true;message(note);}
}
$('file').addEventListener('change',async()=>{
 invalidate();clearCartClient();const version=++importVersion;pdfReviewRequired=false;$('pdfreview').hidden=true;$('pdfconfirmed').checked=false;$('pdfrows').replaceChildren();$('check').disabled=true;sheets=[];dropzone.classList.remove('loaded');$('droptitle').textContent='Déposez le fichier de commande ici';$('mapping').hidden=true;$('filename').textContent='';const file=$('file').files[0];if(!file){scheduleGuide();return;}
 try{
 if(file.size>8000000)throw Error('Fichier limité à 8 Mo.');message('Lecture du fichier de commande…');
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 const data=await api('/import',{name:file.name,data:btoa(binary)});if(version!==importVersion)return;sheets=data.sheets;
 $('sheet').replaceChildren(...sheets.map((s,i)=>new Option(s.name,i)));$('mapping').hidden=false;$('filename').textContent=file.name;dropzone.classList.add('loaded');$('droptitle').textContent='Fichier chargé — déposez-en un autre pour le remplacer';mapping();$('check').disabled=false;message('Fichier chargé. Vérifiez les colonnes. Les prix du fichier doivent être hors taxes.');
 if(data.pdf_review_required){pdfReviewRequired=true;$('pdfreview').hidden=false;$('pdfnotice').textContent=data.notice;$('check').disabled=true;renderPdfRows();await detectPdfCustomer(data.customer_hint,version);}
 }catch(error){if(version===importVersion)message(error.message,true);}
});
// Glisser-déposer : le fichier déposé passe par le même champ que le sélecteur.
const dropzone=$('dropzone');
for(const type of ['dragenter','dragover'])dropzone.addEventListener(type,event=>{event.preventDefault();dropzone.classList.add('dragging');});
for(const type of ['dragleave','dragend'])dropzone.addEventListener(type,()=>dropzone.classList.remove('dragging'));
dropzone.addEventListener('drop',event=>{event.preventDefault();dropzone.classList.remove('dragging');if(!event.dataTransfer.files.length)return;$('file').files=event.dataTransfer.files;$('file').dispatchEvent(new Event('change'));});
$('sheet').addEventListener('change',mapping);$('header').addEventListener('change',mapping);
$('source').addEventListener('change',()=>{const show=$('source').value==='excel';$('pricebox').hidden=!show;invalidate();});
for(const id of ['reference','quantity','price','shop'])$(id).addEventListener('input',invalidate);
$('pdfconfirmed').addEventListener('change',()=>{$('check').disabled=pdfReviewRequired&&!$('pdfconfirmed').checked;});
// Toutes les lignes d'articles du fichier, lignes retirées comprises.
function sheetRows(){
 const sheet=sheets[Number($('sheet').value)];if(!sheet)throw Error('Choisissez un fichier Excel, CSV ou PDF.');
 const ref=Number($('reference').value),qty=Number($('quantity').value),price=Number($('price').value);
 if(ref===qty)throw Error('La référence et la quantité doivent utiliser deux colonnes distinctes.');
 const header=Number($('header').value);if(!Number.isInteger(header)||header<1)throw Error('Ligne d’en-tête invalide.');
 return sheet.rows.slice(header).map((row,i)=>{const line=header+i+1,original=(row[qty]||'').trim();
  return {reference:(row[ref]||'').trim(),quantity:edited.get(line)??original,original,price:(row[price]||'').trim(),line};}).filter(row=>row.reference||row.quantity).map(({original,...row})=>row);
}
function payload(){
 if(pdfReviewRequired&&!$('pdfconfirmed').checked)throw Error('Vérifiez les lignes extraites du PDF puis cochez la confirmation.');
 const all=sheetRows(),rows=all.filter(row=>!removed.has(row.line));
 if(!rows.length&&all.length)throw Error('Toutes les lignes ont été retirées. Rétablissez au moins un article.');
 if(!rows.length||rows.length>500)throw Error('La commande doit contenir de 1 à 500 lignes.');
 return {rows,shop:$('shop').value,price_source:$('source').value};
}
function display(result){
 $('rows').replaceChildren();let requested=0,accepted=0;
 const originals=new Map();try{const sheet=sheets[Number($('sheet').value)],qty=Number($('quantity').value),header=Number($('header').value);sheet.rows.slice(header).forEach((row,i)=>originals.set(header+i+1,(row[qty]||'').trim()));}catch{}
 for(const row of result.rows){
 const tr=document.createElement('tr');tr.style.animationDelay=Math.min($('rows').children.length,24)*28+'ms';const values=[`${row.line} · ${row.reference}`,row.name||'—',row.requested,row.available??'—',row.accepted,row.unit_ht===undefined?'—':new Intl.NumberFormat('fr-FR',{minimumFractionDigits:2,maximumFractionDigits:6}).format(Number(row.unit_ht)),row.subtotal===undefined?'—':currency(row.subtotal),row.error||(!row.accepted?'Rupture':row.missing?'Quantité réduite':'Disponible')];
 values.forEach((value,i)=>{const td=document.createElement('td');if(i===2){td.append(quantityInput(row.line,String(edited.get(row.line)??value),originals.get(row.line)));}else if(i===7){const pill=document.createElement('span');pill.className='pill '+(row.error||!row.accepted?'bad':row.missing?'warn':'good');pill.textContent=value;td.append(pill);}else td.textContent=value;tr.append(td);});
 const action=document.createElement('td');action.append(lineButton(row.line));tr.append(action);$('rows').append(tr);requested+=Number(row.requested)||0;accepted+=row.accepted;
 }
 // Lignes retirées : affichées à la fin, barrées, pour pouvoir les rétablir.
 let others=[];try{others=sheetRows().filter(row=>removed.has(row.line));}catch{}
 for(const row of others){
  const tr=document.createElement('tr');tr.className='removed';
  const values=[`${row.line} · ${row.reference}`,'—',row.quantity,'—','0','—','—'];
  for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}
  const status=document.createElement('td'),pill=document.createElement('span');pill.className='pill';pill.textContent='Retirée de la commande';status.append(pill);
  const action=document.createElement('td');action.append(lineButton(row.line));tr.append(status,action);$('rows').append(tr);
 }
 const failing=result.rows.filter(row=>row.error).map(row=>row.line);
 $('removeerrors').hidden=!failing.length;$('removeerrors').textContent=`Retirer les ${failing.length} ligne(s) en erreur`;$('removeerrors').onclick=()=>toggleLines(failing,true);
 $('removedcount').textContent=others.length?`${others.length} ligne(s) retirée(s) de la commande, affichée(s) en fin de tableau.`:'';
 countUp($('requested'),requested);countUp($('accepted'),accepted);countUp($('missing'),requested-accepted);countUp($('total'),Number(result.ht),currency);const share=requested?Math.round(accepted/requested*100):0;$('ratiobar').style.width='0';$('ratiolabel').textContent=`${share} % de la commande retenue`;setTimeout(()=>{$('ratiobar').style.width=share+'%';},60);$('checked').textContent='Vérifié le '+new Date(result.checked_at).toLocaleString('fr-FR');$('results').hidden=false;setStep(3);
}
async function run(){
 $('check').disabled=true;
 try{
  const input=payload();checking=true;$('check').classList.add('loading');scheduleGuide();message('Contrôle des références, prix et stocks via MCP…');
  const result=await api('/check',input);display(result);
  const errors=result.rows.some(row=>row.error);
  message(errors?'Des lignes nécessitent une correction.':'Contrôle terminé. Les disponibilités sont affichées.',errors);
 }catch(error){message(error.message,true);}
 finally{checking=false;$('check').classList.remove('loading');$('check').disabled=false;scheduleGuide();}
}
$('check').addEventListener('click',run);
if(!token)message('Ouvrez l’outil avec « Lancer FullMoon.command » (Mac) ou « Lancer FullMoon.bat » (Windows) pour créer une session locale sécurisée.',true);

let cartTicket=null;
function cartMessage(text,error=false){$('cartmessage').textContent=text;$('cartmessage').className='notice '+(error?'bad':'good');}
function cartChip(active){$('cartchip').classList.toggle('on',active);$('cartchip').lastChild.textContent=active?'Paniers actifs':'Paniers inactifs';}
function resetCart(){cartTicket=null;$('cartconfirmation').hidden=true;}
for(const id of ['cartdelivery','cartbilling','customersearch'])$(id).addEventListener('input',resetCart);
$('shop').addEventListener('input',clearCartClient);
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
 $('cartconfig').open=false;cartChip(true);cartMessage('Accès aux paniers activé.');
}));
$('forgetcart').addEventListener('click',()=>cartAction(async()=>{
 await api('/cart/forget',{});$('cartkey').value='';resetCart();$('keystatus').textContent='Clé oubliée.';cartChip(false);cartMessage('Accès aux paniers désactivé.');
}));
async function customerAddresses(){
 resetCart();$('cartdelivery').replaceChildren();$('cartbilling').replaceChildren();
 const result=await api('/cart/details',{shop:$('shop').value,cart:{customer:$('cartcustomer').value}});
 const addressLabel=a=>[a.alias,a.company,a.address1,a.address2,a.postcode,a.city].filter(Boolean).join(' · ');
 for(const id of ['cartdelivery','cartbilling'])$(id).replaceChildren(new Option('Choisir une adresse',''),...result.addresses.map(a=>new Option(addressLabel(a),a.id)));
 $('customerfields').hidden=false;
 if(!result.addresses.length)throw Error('Ce client n’a pas d’adresse utilisable. Ajoutez son adresse dans PrestaShop.');
 // Une seule adresse : elle est choisie d'office pour la livraison et la facturation (modifiable, et reprise dans le récapitulatif).
 if(result.addresses.length===1){
  for(const id of ['cartdelivery','cartbilling'])$(id).value=String(result.addresses[0].id);
  cartMessage('Client trouvé. Sa seule adresse est sélectionnée pour la livraison et la facturation : vérifiez-la.');
  return;
 }
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
 if(!$('cartcustomer').value||!$('cartdelivery').value||!$('cartbilling').value)throw Error('Validez un client puis sélectionnez ses deux adresses.');
 input.cart={customer:$('cartcustomer').value,delivery:$('cartdelivery').value,billing:$('cartbilling').value,order_ref:orderReference(input.rows)};
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
  if(result.result)display(result.result);resetCart();cartCreated=true;celebrate($('createcart'),70);
  cartMessage(`Panier n° ${result.cart_id} créé et vérifié dans PrestaShop pour ${result.order_ref}. Retrouvez-le dans Commandes → Paniers, puis créez la commande depuis le back-office. Vérifiez le transport, les prix, les taxes et les stocks avant validation.`);
 }catch(error){resetCart();throw error;}
 finally{await loadCartHistory();}
}));


// ——— Trouver un client : recherche unique, en consultation seule ———
// Le répertoire (e-mail compris) reste en mémoire dans la page. Seuls les numéros
// des derniers clients consultés sont mémorisés dans le navigateur.
let clients=[],clientsShop='',clientsCheckedAt='',clientsVersion=0,cardVersion=0,activeIndex=-1,currentResults=[],shopTimer=0;
const RECENT_KEY='fullmoon-recent-clients',MAX_RESULTS=8;
const WEBMAIL=/^(gmail|googlemail|hotmail|outlook|live|msn|yahoo|ymail|icloud|me|mac|orange|wanadoo|free|sfr|neuf|laposte|bbox|aol|gmx|proton|protonmail)\./;
const fold=value=>String(value??'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLocaleLowerCase('fr-FR').trim();
const personName=c=>[c.firstname,c.lastname].filter(Boolean).join(' ');
const clientLabel=c=>[c.company,personName(c)].filter(Boolean).join(' · ')||'Client sans nom';
function recentIds(){
 try{const all=JSON.parse(localStorage.getItem(RECENT_KEY)||'{}');const list=all&&all[$('shop').value];return Array.isArray(list)?list.map(String):[];}catch{return [];}
}
function rememberClient(id){
 try{const all=JSON.parse(localStorage.getItem(RECENT_KEY)||'{}')||{};const shop=$('shop').value;const list=Array.isArray(all[shop])?all[shop].map(String):[];
  all[shop]=[String(id),...list.filter(x=>x!==String(id))].slice(0,6);localStorage.setItem(RECENT_KEY,JSON.stringify(all));}catch{}
}
// Classement : e-mail ou numéro exact, puis e-mail qui commence par la saisie, puis qui la contient, puis noms et entreprises.
function searchClients(raw){
 const term=fold(raw);if(!term)return [];
 const tokens=term.split(/\s+/),found=[];
 for(const c of clients){
  const email=fold(c.email),names=fold([c.company,c.firstname,c.lastname].join(' ')),id=String(c.id);
  let score=-1;
  if(id===term||(email&&email===term))score=0;
  else if(email.startsWith(term))score=1;
  else if(email.includes(term))score=2;
  else if(names.split(/\s+/).some(word=>word.startsWith(term)))score=3;
  else if(tokens.every(t=>`${email} ${names} ${id}`.includes(t)))score=4;
  if(score>=0)found.push({score,client:c});
 }
 return found.sort((a,b)=>a.score-b.score||fold(a.client.email).localeCompare(fold(b.client.email))||Number(a.client.id)-Number(b.client.id));
}
function highlight(element,text,raw){
 const term=raw.trim().toLocaleLowerCase('fr-FR'),at=term?text.toLocaleLowerCase('fr-FR').indexOf(term):-1;
 if(at<0){element.textContent=text;return;}
 const mark=document.createElement('mark');mark.textContent=text.slice(at,at+term.length);
 element.replaceChildren(text.slice(0,at),mark,text.slice(at+term.length));
}
function listItem(text,className){const li=document.createElement('li');li.className=className;li.textContent=text;return li;}
function setActive(index){
 activeIndex=index;
 [...$('clientresults').querySelectorAll('li[role=option]')].forEach((li,i)=>{li.classList.toggle('active',i===index);li.setAttribute('aria-selected',String(i===index));if(i===index)li.scrollIntoView({block:'nearest'});});
 $('clientsearch').setAttribute('aria-activedescendant',index>=0?'clientopt'+index:'');
}
function hideResults(){$('clientresults').hidden=true;$('clientsearch').setAttribute('aria-expanded','false');}
function renderResults(){
 const raw=$('clientsearch').value,list=$('clientresults');
 list.replaceChildren();activeIndex=-1;currentResults=[];
 if(!raw.trim()){hideResults();return;}
 const found=searchClients(raw);currentResults=found.slice(0,MAX_RESULTS);
 currentResults.forEach(({client},i)=>{
  const li=document.createElement('li');li.id='clientopt'+i;li.setAttribute('role','option');li.setAttribute('aria-selected','false');
  const button=document.createElement('button');button.type='button';button.className='resultbtn';button.tabIndex=-1;
  const email=document.createElement('strong'),detail=document.createElement('span');
  highlight(email,client.email||'E-mail non renseigné',raw);highlight(detail,`${clientLabel(client)} · n° ${client.id}`,raw);
  button.append(email,detail);button.addEventListener('mousedown',event=>event.preventDefault());button.addEventListener('click',()=>openClient(client.id));
  li.append(button);list.append(li);
 });
 if(!found.length){
  if(!clients.length)list.append(listItem(raw.includes('@')?'Répertoire en cours de chargement. Appuyez sur Entrée pour chercher cet e-mail exact.':'Répertoire en cours de chargement…','empty'));
  else list.append(listItem('Aucun client actif ne correspond. Essayez une autre partie de l’e-mail ou le nom.','empty'));
 }else if(found.length>MAX_RESULTS)list.append(listItem(`${found.length-MAX_RESULTS} autre(s) résultat(s) : précisez la recherche.`,'more'));
 if(currentResults.length===1||currentResults[0]?.score===0)setActive(0);
 list.hidden=false;$('clientsearch').setAttribute('aria-expanded','true');
}
function updateClientStatus(){
 if(!clients.length)return;
 $('clientstatus').textContent=`${clients.length} clients actifs · boutique ${clientsShop} · actualisé à ${new Date(clientsCheckedAt).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'})}`;
}
let clientsReady=Promise.resolve();
function loadClients(){clientsReady=fetchClients();return clientsReady;}
async function fetchClients(){
 if(!token)return;
 const version=++clientsVersion,shop=$('shop').value;
 $('clientrefresh').disabled=true;$('clientstatus').textContent='Chargement du répertoire clients via le MCP… Vous pouvez déjà taper.';
 try{
  const result=await api('/customers/list',{shop});if(version!==clientsVersion)return;
  clients=result.customers;clientsShop=shop;clientsCheckedAt=result.checked_at;
  updateClientStatus();renderRecent();if($('clientsearch').value.trim()&&document.activeElement===$('clientsearch'))renderResults();
 }catch(error){if(version===clientsVersion){clients=[];$('clientstatus').textContent='Répertoire indisponible : '+error.message+' Cliquez sur « Actualiser » pour réessayer.';}}
 finally{if(version===clientsVersion)$('clientrefresh').disabled=false;}
}
function renderRecent(){
 const box=$('clientrecent');box.replaceChildren();
 const items=recentIds().map(id=>clients.find(c=>String(c.id)===id)).filter(Boolean);
 if(!items.length){box.hidden=true;return;}
 const label=document.createElement('span');label.textContent='Récemment consultés';box.append(label);
 for(const c of items){
  const chip=document.createElement('button');chip.type='button';chip.className='recentchip';chip.textContent=c.email||clientLabel(c);chip.title=`${clientLabel(c)} · n° ${c.id}`;
  chip.addEventListener('click',()=>openClient(c.id));box.append(chip);
 }
 box.hidden=false;
}
async function copyText(text,button){
 try{await navigator.clipboard.writeText(text);}
 catch{const area=document.createElement('textarea');area.value=text;area.style.cssText='position:fixed;opacity:0';document.body.append(area);area.select();document.execCommand('copy');area.remove();}
 button.textContent='Copié ✓';button.classList.add('done');
 clearTimeout(button.copyTimer);button.copyTimer=setTimeout(()=>{button.textContent='Copier';button.classList.remove('done');},1600);
}
function copyButton(text){const button=document.createElement('button');button.type='button';button.className='copy';button.textContent='Copier';button.addEventListener('click',()=>copyText(text,button));return button;}
// Partie B du panier : un client validé dans sa fiche y est repris directement, avec ses adresses.
let cardClient=null;
function clearCartClient(){
 resetCart();$('customersearch').value='';$('customerfields').hidden=true;$('cartcustomer').replaceChildren();
 $('cartdelivery').replaceChildren();$('cartbilling').replaceChildren();$('cartvalidated').hidden=true;$('cartmanual').hidden=false;
 $('cardvalidate').classList.remove('done');$('cardvalidate').firstChild.textContent='Valider ce client pour le panier ';
}
async function validateClient(){
 if(!cardClient)return;
 const c=cardClient;clearCartClient();
 $('cartcustomer').replaceChildren(new Option(`n° ${c.id} · ${[c.company,c.firstname,c.lastname,c.email].filter(Boolean).join(' · ')}`,c.id));
 $('cartvalidatedname').textContent=`${clientLabel(c)} · ${c.email||'n° '+c.id}`;$('cartvalidated').hidden=false;$('cartmanual').hidden=true;
 $('cardvalidate').classList.add('done');$('cardvalidate').firstChild.textContent='Client validé pour le panier ';celebrate($('cardvalidate'),18);
 await cartAction(customerAddresses);
 $('cartsection').scrollIntoView({behavior:'smooth',block:'start'});
}
// Référence locale du panier, calculée automatiquement : nom du fichier et empreinte des lignes.
// Elle n'est pas envoyée à PrestaShop ; le registre local s'en sert pour bloquer un second envoi de la même commande.
function orderReference(rows){
 let hash=0x811c9dc5;
 for(const ch of JSON.stringify(rows.map(r=>[r.reference,r.quantity]))){hash^=ch.charCodeAt(0);hash=Math.imul(hash,16777619)>>>0;}
 const name=($('filename').textContent||'Commande').replace(/\s+/g,' ').trim().slice(0,100);
 return `${name} · ${hash.toString(16).padStart(8,'0')}`;
}
function closeCard(){cardVersion++;cardClient=null;$('cardvalidate').disabled=true;$('clientcard').hidden=true;$('cardaddresses').replaceChildren();}
async function openClient(id){
 const version=++cardVersion,shop=$('shop').value,known=clients.find(c=>String(c.id)===String(id));
 hideResults();cardClient=null;$('cardvalidate').disabled=true;$('clientcard').hidden=false;$('cardid').textContent=id;
 $('cardname').textContent=known?clientLabel(known):'Client n° '+id;$('cardemail').textContent=known?.email||'…';$('cardemailcopy').hidden=!known?.email;
 $('cardaddresses').replaceChildren();$('cardstatus').textContent='Lecture des coordonnées via le MCP…';
 try{
  const data=await api('/customers/contact',{shop,customer:id});if(version!==cardVersion)return;
  const c=data.customer;
  $('cardid').textContent=c.id;$('cardname').textContent=clientLabel(c);$('cardemail').textContent=c.email||'Non renseigné';$('cardemailcopy').hidden=!c.email;
  for(const address of data.addresses){
   const article=document.createElement('article');article.className='contactaddress';
   const title=document.createElement('strong');title.textContent=address.alias||`Adresse n° ${address.id}`;
   const lines=[address.company,[address.firstname,address.lastname].filter(Boolean).join(' '),address.address1,address.address2,[address.postcode,address.city].filter(Boolean).join(' '),address.country].filter(Boolean);
   const details=document.createElement('p');details.style.whiteSpace='pre-line';details.textContent=lines.join('\n');
   article.append(title,details,copyButton(lines.join('\n')));$('cardaddresses').append(article);
  }
  $('cardstatus').textContent=data.addresses.length?'':'Aucune adresse enregistrée pour ce client.';
  cardClient=c;$('cardvalidate').disabled=false;
  const already=$('cartcustomer').value===String(c.id)&&!$('cartvalidated').hidden;
  $('cardvalidate').classList.toggle('done',already);$('cardvalidate').firstChild.textContent=already?'Client validé pour le panier ':'Valider ce client pour le panier ';
  rememberClient(c.id);renderRecent();
 }catch(error){if(version===cardVersion)$('cardstatus').textContent=error.message;}
}
async function exactLookup(email){
 $('clientstatus').textContent='Recherche de l’e-mail exact via le MCP…';
 try{
  const result=await api('/customers/match',{shop:$('shop').value,email});
  if(result.status==='unique')await openClient(result.customers[0].id);
  else $('clientstatus').textContent=result.status==='ambiguous'?'Plusieurs clients partagent cet e-mail. Attendez la fin du chargement pour choisir.':'Aucun client actif avec cet e-mail exact.';
 }catch(error){$('clientstatus').textContent=error.message;}
 finally{updateClientStatus();}
}
$('clientsearch').addEventListener('input',renderResults);
$('clientsearch').addEventListener('focus',()=>{if($('clientsearch').value.trim())renderResults();});
$('clientsearch').addEventListener('blur',()=>setTimeout(hideResults,150));
$('clientsearch').addEventListener('keydown',event=>{
 const count=currentResults.length,value=$('clientsearch').value.trim();
 if(event.key==='ArrowDown'&&count){event.preventDefault();if($('clientresults').hidden)renderResults();setActive((activeIndex+1)%count);}
 else if(event.key==='ArrowUp'&&count){event.preventDefault();setActive((activeIndex-1+count)%count);}
 else if(event.key==='Enter'){
  event.preventDefault();
  if(activeIndex>=0&&currentResults[activeIndex])openClient(currentResults[activeIndex].client.id);
  else if(!clients.length&&value.includes('@'))exactLookup(value);
 }
 else if(event.key==='Escape'){if(!$('clientresults').hidden)hideResults();else{$('clientsearch').value='';renderResults();}}
});
$('clientrefresh').addEventListener('click',()=>loadClients());
$('cardclose').addEventListener('click',()=>{closeCard();$('clientsearch').focus();});
$('cardvalidate').addEventListener('click',validateClient);
$('cartchange').addEventListener('click',()=>{clearCartClient();$('customersearch').focus();});
$('cardemailcopy').addEventListener('click',()=>copyText($('cardemail').textContent,$('cardemailcopy')));
$('shop').addEventListener('input',()=>{
 clientsVersion++;clients=[];closeCard();hideResults();hideSuggestions();$('clientrecent').hidden=true;
 $('clientstatus').textContent='Boutique modifiée : rechargement du répertoire…';
 clearTimeout(shopTimer);shopTimer=setTimeout(()=>{if(Number($('shop').value)>0)loadClients();},600);
});

// Suggestions d'après l'en-tête du PDF : chaque mot commun compte d'autant plus qu'il est rare dans le répertoire.
function suggestClients(terms){
 const wanted=new Set((terms||[]).map(fold));if(!wanted.size||!clients.length)return [];
 const index=clients.map(c=>({c,words:new Set(fold([c.company,c.firstname,c.lastname].join(' ')).match(/[a-z]{3,}/g)||[])}));
 const frequency=new Map();for(const {words} of index)for(const w of words)frequency.set(w,(frequency.get(w)||0)+1);
 return index.map(({c,words})=>{let score=0,common=0;for(const w of words)if(wanted.has(w)){score+=1/frequency.get(w);common++;}return {client:c,score,common};})
  .filter(x=>x.score>=0.5).sort((a,b)=>b.score-a.score||b.common-a.common).slice(0,3);
}
function hideSuggestions(){$('clientsuggest').hidden=true;$('suggestlist').replaceChildren();}
function showSuggestions(found){
 const list=$('suggestlist');list.replaceChildren();
 found.forEach(({client,common},i)=>{
  const li=document.createElement('li');if(i===0&&(found.length===1||found[0].score>=2*found[1].score))li.className='best';
  const button=document.createElement('button');button.type='button';button.className='resultbtn';
  const email=document.createElement('strong'),detail=document.createElement('span');
  email.textContent=client.email||'E-mail non renseigné';
  detail.textContent=`${clientLabel(client)} · n° ${client.id} · ${common} mot(s) en commun avec le PDF`+(li.className?' · meilleure correspondance':'');
  button.append(email,detail);button.addEventListener('click',()=>openClient(client.id));li.append(button);list.append(li);
 });
 $('clientsuggest').hidden=!found.length;
}

// Commande PDF : l'e-mail « Acheteur » ouvre directement la fiche si un seul client actif correspond exactement.
// Sinon, la page propose les clients les plus proches de l'en-tête (nom, entreprise, adresses), sans en choisir un.
async function detectPdfCustomer(hint,version){
 $('pdfclientstatus').textContent='Recherche du client acheteur…';hideSuggestions();
 let status='missing';
 try{
  if(hint?.email){
   const result=await api('/customers/match',{shop:$('shop').value,email:hint.email});
   if(version!==importVersion)return;
   if(result.status==='unique'){
    $('clientsearch').value=hint.email;await openClient(result.customers[0].id);
    $('pdfclientstatus').textContent='Client identifié par l’e-mail acheteur exact '+hint.email+'. Sa fiche est ouverte dans « Trouver un client », après le contrôle des stocks : vérifiez-la puis validez-la pour le panier.';
    return;
   }
   status=result.status;
  }
 }catch(error){if(version!==importVersion)return;status='error';}
 closeCard();
 await clientsReady;if(version!==importVersion)return;
 const found=suggestClients(hint?.terms);showSuggestions(found);
 const domain=hint?.email?.split('@')[1]||'',useDomain=domain&&!WEBMAIL.test(domain);
 $('clientsearch').value=found.length?'':hint?.email?(useDomain?'@'+domain:hint.email):'';
 const cause=status==='error'?'Recherche par e-mail indisponible.':!hint?.email?'Aucun e-mail acheteur lisible dans le PDF.':status==='ambiguous'?`Plusieurs clients actifs ont l’e-mail ${hint.email}.`:`Aucun client actif n’a l’e-mail ${hint.email} dans PrestaShop.`;
 $('pdfclientstatus').textContent=cause+(found.length?` ${found.length} client(s) possible(s) proposé(s) dans « Trouver un client », après le contrôle des stocks : ouvrez la fiche, vérifiez-la puis validez-la.`:' Recherchez le client dans « Trouver un client », après le contrôle des stocks'+($('clientsearch').value?' (recherche pré-remplie).':'.'));
}

if(token)loadClients();else $('clientstatus').textContent='Session locale absente : le répertoire clients ne peut pas être chargé.';

// ——— Bandeau guide : toujours visible, il indique l'étape en cours et la prochaine action ———
// L'état est relu depuis la page à chaque changement ; le bouton du bandeau mène à l'endroit utile, sans rien valider à votre place.
const calm=()=>matchMedia('(prefers-reduced-motion:reduce)').matches;
function guideState(){
 if(!token)return {key:'session',step:1,progress:0,title:'Session locale absente',text:'Relancez l’outil avec « Lancer FullMoon » pour ouvrir une session sécurisée.'};
 if(!sheets.length)return {key:'import',step:1,progress:4,title:'Importez la commande',text:'Déposez le fichier Excel, CSV ou PDF du client.',cta:'Choisir le fichier',target:'dropzone',pick:true};
 if(pdfReviewRequired&&!$('pdfconfirmed').checked)return {key:'pdf',step:1,progress:16,title:'Vérifiez les lignes du PDF',text:'Contrôlez les références et les quantités extraites, puis cochez la confirmation.',cta:'Voir les lignes',target:'pdfreview'};
 if(checking)return {key:'checking',step:2,progress:34,title:'Contrôle en cours',text:'Lecture des stocks et des prix dans PrestaShop…',busy:true,target:'check'};
 if($('results').hidden)return {key:'check',step:2,progress:27,title:'Contrôlez les disponibilités',text:'Vérifiez les colonnes reconnues, puis lancez le contrôle des stocks.',cta:'Aller au contrôle',target:'check'};
 if(cartCreated)return {key:'created',step:5,progress:100,title:'Panier créé dans PrestaShop',text:'Retrouvez-le dans Commandes → Paniers pour établir la commande.',cta:'Nouvelle commande',target:'dropzone',pick:true,finished:true};
 if(!$('cartcustomer').value){
  if(!$('clientcard').hidden)return {key:'validate',step:3,progress:58,title:'Validez ce client',text:'Vérifiez sa fiche et ses adresses, puis validez-le pour le panier.',cta:'Voir la fiche',target:'clientcard'};
  if(!$('clientsuggest').hidden)return {key:'suggest',step:3,progress:52,title:'Choisissez le client',text:`${$('suggestlist').children.length} client(s) proposé(s) d’après l’en-tête du PDF.`,cta:'Voir les suggestions',target:'clientsuggest'};
  return {key:'client',step:3,progress:48,title:'Trouvez le client',text:'Tapez une partie de son e-mail, son nom ou son entreprise.',cta:'Rechercher un client',target:'clientsearch'};
 }
 if(!$('cartdelivery').value||!$('cartbilling').value)return {key:'addresses',step:4,progress:70,title:'Choisissez les adresses',text:'Sélectionnez l’adresse de livraison et l’adresse de facturation.',cta:'Voir les adresses',target:'customerfields'};
 if(!$('cartchip').classList.contains('on'))return {key:'key',step:4,progress:78,title:'Activez l’accès aux paniers',text:'Collez la clé Webservice dédiée aux paniers, puis vérifiez-la.',cta:'Saisir la clé',target:'cartconfig'};
 if(!cartTicket)return {key:'prepare',step:4,progress:86,title:'Préparez le panier',text:'Dernière vérification du client, des produits et des stocks.',cta:'Aller à la préparation',target:'preparecart'};
 return {key:'create',step:4,progress:94,title:'Créez le panier',text:'Relisez le récapitulatif, puis créez le panier dans PrestaShop.',cta:'Voir le récapitulatif',target:'cartconfirmation'};
}
let guideKey='',guideTarget=null,guidePick=false;
function paintGuide(state){
 $('guidetitle').textContent=state.title;$('guidetext').textContent=state.text;
 $('guidecta').hidden=!state.cta;if(state.cta)$('guidectalabel').textContent=state.cta;
 $('guide').classList.toggle('busy',Boolean(state.busy));$('guide').classList.toggle('finished',Boolean(state.finished));
}
function updateGuide(){
 const state=guideState();guideTarget=state.target||null;guidePick=Boolean(state.pick);
 [...$('track').children].forEach((li,i)=>{li.classList.toggle('done',i+1<state.step);li.classList.toggle('active',i+1===state.step);});
 $('guidebar').style.width=state.progress+'%';
 const sections=['importsection','results','clientsection','cartsection'].map($),current=guideTarget?$(guideTarget).closest('section'):null;
 sections.forEach((section,i)=>{section.classList.toggle('current',section===current&&!state.finished);section.classList.toggle('done',section!==current&&i+1<state.step||Boolean(state.finished));});
 const signature=state.key+'|'+state.text;if(signature===guideKey)return;
 const first=!guideKey;guideKey=signature;
 if(first||calm()){paintGuide(state);return;}
 $('guide').classList.add('swap');setTimeout(()=>{paintGuide(state);$('guide').classList.remove('swap');},230);
}
function scheduleGuide(){if(guideTimer)return;guideTimer=setTimeout(()=>{guideTimer=0;updateGuide();},40);}
$('guidecta').addEventListener('click',()=>{
 if(!guideTarget)return;
 const target=$(guideTarget);if(target.tagName==='DETAILS')target.open=true;
 target.scrollIntoView({behavior:calm()?'auto':'smooth',block:'center'});
 target.classList.remove('spot');void target.offsetWidth;target.classList.add('spot');setTimeout(()=>target.classList.remove('spot'),1600);
 if(guidePick)$('file').click();
 else if(target.matches('input,button,select'))target.focus({preventScroll:true});
 else if(target.id==='cartconfig')$('cartkey').focus({preventScroll:true});
});
new MutationObserver(scheduleGuide).observe(document.querySelector('main'),{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','class','open']});
for(const type of ['input','change','click'])document.addEventListener(type,scheduleGuide,true);
updateGuide();

// ——— Animations ———
function countUp(element,target,format=value=>String(Math.round(value))){
 cancelAnimationFrame(element.countFrame);clearTimeout(element.countTimer);
 if(calm()||document.hidden){element.textContent=format(target);return;}
 const start=performance.now(),duration=800;
 const tick=now=>{const t=Math.min(1,(now-start)/duration);element.textContent=format(target*(1-Math.pow(1-t,3)));if(t<1)element.countFrame=requestAnimationFrame(tick);};
 element.countFrame=requestAnimationFrame(tick);
 element.countTimer=setTimeout(()=>{cancelAnimationFrame(element.countFrame);element.textContent=format(target);},duration+120);
}
// Petite gerbe de confettis aux couleurs FullMoon, partant de l'élément indiqué.
function celebrate(origin,count){
 if(calm())return;
 const box=origin.getBoundingClientRect(),burst=document.createElement('div');burst.className='burst';
 burst.style.transform=`translate(${box.left+box.width/2}px,${box.top+box.height/2}px)`;
 const colors=['#d62844','#ff6b84','#1c1c20','#2f9e5b','#ffffff','#f3c15b'];
 for(let i=0;i<count;i++){
  const piece=document.createElement('i'),angle=Math.random()*Math.PI*2,distance=90+Math.random()*(count>30?320:140);
  piece.style.setProperty('--x',Math.cos(angle)*distance+'px');piece.style.setProperty('--y',Math.sin(angle)*distance*0.8+(count>30?140:50)+'px');
  piece.style.setProperty('--r',(Math.random()*900-450)+'deg');piece.style.setProperty('--c',colors[i%colors.length]);
  piece.style.setProperty('--w',5+Math.random()*6+'px');piece.style.setProperty('--h',7+Math.random()*9+'px');piece.style.setProperty('--d',1+Math.random()*0.8+'s');
  burst.append(piece);
 }
 document.body.append(burst);setTimeout(()=>burst.remove(),2100);
}
// En-tête qui se resserre au défilement, halo qui suit la souris dans le bandeau d'accueil, cartes qui apparaissent au défilement.
let scrollFrame=0;
addEventListener('scroll',()=>{if(scrollFrame)return;scrollFrame=requestAnimationFrame(()=>{scrollFrame=0;document.body.classList.toggle('scrolled',scrollY>30);});},{passive:true});
$('hero').addEventListener('pointermove',event=>{const box=$('hero').getBoundingClientRect();$('hero').style.setProperty('--mx',event.clientX-box.left+'px');$('hero').style.setProperty('--my',event.clientY-box.top+'px');});
if('IntersectionObserver' in window&&!calm()){
 const reveal=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){entry.target.classList.add('in');reveal.unobserve(entry.target);}},{rootMargin:'0px 0px -8% 0px'});
 for(const card of document.querySelectorAll('main>.card'))if(card.getBoundingClientRect().top>innerHeight){card.classList.add('will-reveal');reveal.observe(card);}
 setTimeout(()=>document.querySelectorAll('.will-reveal:not(.in)').forEach(card=>{if(card.getBoundingClientRect().top<innerHeight)card.classList.add('in');}),1200);
}
