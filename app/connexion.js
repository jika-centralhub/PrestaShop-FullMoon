'use strict';
const $ = id => document.getElementById(id);
$('signin').addEventListener('submit', async event => {
 event.preventDefault();
 $('submit').disabled = true; $('message').hidden = true;
 try {
  const response = await fetch('/connexion', {method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({username: $('username').value, password: $('password').value, code: $('code').value})});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Connexion impossible.');
  location.replace('/');
 } catch (error) {
  $('password').value = ''; $('code').value = '';
  $('message').textContent = error.message; $('message').className = 'notice bad'; $('message').hidden = false;
 } finally { $('submit').disabled = false; }
});
