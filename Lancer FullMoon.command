#!/bin/zsh
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'Installez les dépendances selon README.md avant de lancer.'
  exit 1
fi
.venv/bin/python -B app/app.py
read '?Appuyez sur Entrée pour fermer.'
