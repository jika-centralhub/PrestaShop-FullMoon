"""Start the private tunnel using a runtime key entered only in this terminal."""
import getpass
import os
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parent
client = project / 'bin' / 'tunnel-client'
profiles = project / 'tunnel-profiles'
if not (profiles / 'fullmoon.yaml').exists():
    raise SystemExit('Le profil du tunnel doit être configuré avant le démarrage.')
key = getpass.getpass('Collez la clé OpenAI dédiée au tunnel (saisie masquée), puis Entrée : ')
if not key.strip():
    raise SystemExit('Aucune clé saisie.')
environment = dict(os.environ, CONTROL_PLANE_API_KEY=key.strip())
arguments = ['--profile', 'fullmoon', '--profile-dir', str(profiles)]
result = subprocess.run([str(client), 'doctor', *arguments, '--explain'], env=environment)
if result.returncode:
    raise SystemExit('Le diagnostic a échoué. Gardez cette fenêtre ouverte pour examiner le résultat.')
print('Tunnel FullMoon en cours. Gardez cette fenêtre ouverte. Ctrl+C pour arrêter.', flush=True)
os.execve(str(client), [str(client), 'run', *arguments], environment)
