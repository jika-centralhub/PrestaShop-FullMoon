#!/bin/zsh
# Installation (ou mise à jour) de FullMoon Commandes sur un Mac.
#
# Utilisation, dans le Terminal :
#   zsh -c "$(curl -fsSL https://raw.githubusercontent.com/jika-centralhub/PrestaShop-FullMoon/main/Installer%20FullMoon.command)"
# ou double-clic sur ce fichier s'il est déjà sur le Mac.
#
# Ce que fait le script : vérifie Python 3.13 et Git, télécharge ou met à jour l'application
# dans ~/Documents/PrestaShop-FullMoon, installe ses dépendances, demande la clé PrestaShop
# en lecture seule (saisie masquée, jamais affichée), lance les tests hors ligne, vérifie la
# connexion à la boutique, puis crée le raccourci « FullMoon » sur le Bureau.
# Il ne modifie ni PrestaShop, ni les réglages du Mac. Relancez-le à tout moment pour mettre à jour.

setopt pipe_fail
REPO_URL="https://github.com/jika-centralhub/PrestaShop-FullMoon.git"
DIR="${FULLMOON_DIR:-$HOME/Documents/PrestaShop-FullMoon}"
DESKTOP="${FULLMOON_DESKTOP:-$HOME/Desktop}"
LOG_DIR="$HOME/Library/Logs/FullMoon"

bold=$'\e[1m'; red=$'\e[31m'; green=$'\e[32m'; dim=$'\e[2m'; off=$'\e[0m'
step(){ print -- "\n${bold}▸ $1${off}"; }
ok(){ print -- "  ${green}✓${off} $1"; }
info(){ print -- "  ${dim}$1${off}"; }
fail(){ print -- "\n  ${red}✗ $1${off}\n"; [[ -t 0 ]] && read -rs '?Appuyez sur Entrée pour fermer.' </dev/tty; exit 1; }

print -- "${bold}FullMoon Commandes — installation${off}"
print -- "${dim}Dossier : $DIR${off}"

# 1. Outils nécessaires ---------------------------------------------------------
step "Vérification des outils"
[[ $(uname) == Darwin ]] || fail "Ce script est prévu pour macOS."
PY=""
for candidate in python3.13 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 /opt/homebrew/bin/python3.13 /usr/local/bin/python3.13; do
  if command -v "$candidate" >/dev/null 2>&1; then PY=$(command -v "$candidate"); break; fi
done
if [[ -z $PY ]]; then
  open "https://www.python.org/downloads/macos/" 2>/dev/null || true
  fail "Python 3.13 est introuvable. Installez « Python 3.13.x — macOS installer » depuis la page python.org qui vient de s'ouvrir, puis relancez ce script."
fi
ok "Python : $("$PY" -V 2>&1)"
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  fail "Git n'est pas installé. Acceptez l'installation des « outils de ligne de commande » proposée par macOS, puis relancez ce script."
fi
ok "Git : $(git --version)"

# 2. Téléchargement ou mise à jour ----------------------------------------------
step "Téléchargement de l'application"
if [[ -d $DIR/.git ]]; then
  cd "$DIR" || fail "Dossier inaccessible : $DIR"
  [[ -z $(git status --porcelain --untracked-files=no) ]] || fail "Des fichiers de l'application ont été modifiés dans $DIR. Mise à jour interrompue pour ne rien écraser."
  git fetch --quiet origin || fail "Impossible de joindre GitHub. Vérifiez la connexion Internet."
  git merge --ff-only --quiet origin/main || fail "Mise à jour impossible sans écraser des modifications locales."
  ok "Application mise à jour (version $(git rev-parse --short HEAD))"
elif [[ -e $DIR ]]; then
  fail "Le dossier $DIR existe déjà mais ne contient pas l'application. Renommez-le, puis relancez ce script."
else
  mkdir -p "${DIR:h}"
  git clone --quiet "$REPO_URL" "$DIR" || fail "Téléchargement impossible. Vérifiez la connexion Internet."
  cd "$DIR"
  ok "Application téléchargée (version $(git rev-parse --short HEAD))"
fi

# 3. Environnement Python ---------------------------------------------------------
step "Installation des composants (une à deux minutes la première fois)"
if [[ ! -x .venv/bin/python ]] || [[ $(.venv/bin/python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null) != 3.13 ]]; then
  rm -rf .venv
  "$PY" -m venv .venv || fail "Création de l'environnement Python impossible."
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements-lock.txt || fail "Installation des composants impossible. Vérifiez la connexion Internet puis relancez."
.venv/bin/python -m pip check >/dev/null || fail "Composants incohérents. Relancez le script."
ok "Composants installés"

# 4. Clé PrestaShop en lecture seule -------------------------------------------------
ask_key(){
  info "Créez une clé dédiée à ce Mac dans PrestaShop → Paramètres avancés → Webservice,"
  info "avec uniquement la permission GET (lecture seule). Ne l'envoyez ni par e-mail ni par message."
  local key="" attempt
  for attempt in 1 2 3; do
    read -rs 'key?  Collez la clé (32 caractères, rien ne s’affiche), puis Entrée : ' </dev/tty || true
    print
    key=${key//[[:space:]]/}
    [[ $key =~ '^[A-Za-z0-9]{32}$' ]] && break
    print -- "  ${red}Clé invalide : 32 lettres ou chiffres attendus.${off}"; key=""
  done
  [[ -n $key ]] || fail "Aucune clé valide saisie. Relancez le script quand vous l'aurez."
  [[ -f .env ]] || cp .env.example .env
  local tmp=$(mktemp "$DIR/.env.XXXXXX")
  awk -v k="$key" 'BEGIN{done=0} /^PS_API_KEY=/{print "PS_API_KEY=" k; done=1; next} {print} END{if(!done) print "PS_API_KEY=" k}' .env > "$tmp" && mv "$tmp" .env
  chmod 600 .env
  ok "Clé enregistrée dans .env (accessible à vous seul, jamais envoyée sur GitHub)"
}
check_shop(){
  .venv/bin/python -B -c '
from ps_client import PSClient
try:
    c = PSClient.from_env(".env")
    print(c._session.get(c.base, timeout=c.TIMEOUT).status_code)
except Exception:
    print("erreur")
' 2>/dev/null
}
step "Clé PrestaShop"
umask 077
current_key=""
[[ -f .env ]] && current_key=$(sed -n 's/^PS_API_KEY=//p' .env | head -1)
if [[ $current_key =~ '^[A-Za-z0-9]{32}$' ]]; then ok "Clé déjà configurée (conservée)"; else ask_key; fi
chmod 600 .env

# 5. Vérifications ------------------------------------------------------------------
step "Vérifications"
.venv/bin/python -B -m unittest discover -s tests >/dev/null 2>&1 && ok "Tests hors ligne réussis" || fail "Les tests hors ligne échouent. Ne pas utiliser cette installation ; prévenez la personne qui gère l'outil."
for round in 1 2 3; do
  conn=$(check_shop)
  case $conn in
    200) ok "Connexion à la boutique réussie"; break ;;
    401|403)
      print -- "  ${red}✗ La boutique refuse cette clé (code $conn) : clé inconnue, désactivée ou sans permission GET.${off}"
      [[ $round -lt 3 ]] || { info "L'installation continue ; relancez ce script pour saisir une autre clé."; break; }
      answer=""; read -r 'answer?  Saisir une autre clé ? (o/n) ' </dev/tty || true
      [[ $answer == [oOyY]* ]] || break
      ask_key ;;
    *) print -- "  ${red}✗ Boutique injoignable pour l'instant ($conn). L'installation continue ; vérifiez la connexion Internet.${off}"; break ;;
  esac
done

# 6. Raccourci sur le Bureau ----------------------------------------------------------
step "Raccourci sur le Bureau"
work=$(mktemp -d)
cat > "$work/lancer.sh" <<LAUNCHER
#!/bin/zsh
# Raccourci FullMoon : ouvre l'application si elle tourne déjà, sinon la démarre en arrière-plan puis l'ouvre.
REPO="$DIR"
LOG="$LOG_DIR/app.log"
umask 077; mkdir -p "\${LOG:h}"
pid=\$(lsof -tiTCP:8790 -sTCP:LISTEN 2>/dev/null | head -1)
if [[ -n \$pid ]]; then
  cwd=\$(lsof -a -p \$pid -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
  if [[ \$cwd != "\$REPO" || ! -s "\$REPO/app/session.url" ]]; then
    echo "Le port 8790 est occupé par un autre programme. Fermez-le puis réessayez." >&2; exit 1
  fi
else
  [[ -x "\$REPO/.venv/bin/python" ]] || { echo "Installation FullMoon introuvable dans \$REPO. Relancez l'installation." >&2; exit 1; }
  cd "\$REPO"
  before=\$(stat -f %m app/session.url 2>/dev/null || echo 0)
  nohup .venv/bin/python -B app/app.py --no-browser >> "\$LOG" 2>&1 < /dev/null &!
  for i in {1..60}; do
    lsof -tiTCP:8790 -sTCP:LISTEN >/dev/null 2>&1 && [[ \$(stat -f %m app/session.url 2>/dev/null || echo 0) -gt \$before ]] && break
    sleep 0.25
  done
  lsof -tiTCP:8790 -sTCP:LISTEN >/dev/null 2>&1 || { echo "FullMoon n'a pas démarré. Journal : \$LOG" >&2; exit 1; }
fi
open -a Safari "\$(cat "\$REPO/app/session.url")"
LAUNCHER
cat > "$work/FullMoon.applescript" <<'APPLESCRIPT'
try
	do shell script "/bin/zsh " & quoted form of (POSIX path of (path to resource "lancer.sh"))
on error message
	display dialog message buttons {"OK"} default button 1 with title "FullMoon" with icon caution
end try
APPLESCRIPT
osacompile -o "$work/FullMoon.app" "$work/FullMoon.applescript" 2>/dev/null || fail "Création du raccourci impossible."
cp "$work/lancer.sh" "$work/FullMoon.app/Contents/Resources/lancer.sh"; chmod 700 "$work/FullMoon.app/Contents/Resources/lancer.sh"
mkdir "$work/FullMoon.iconset"
for size in 16 32 64 128 256 512; do sips -z $size $size app/assets/favicon.png --out "$work/FullMoon.iconset/icon_${size}x${size}.png" >/dev/null; done
for size in 16 32 128 256; do cp "$work/FullMoon.iconset/icon_$((size*2))x$((size*2)).png" "$work/FullMoon.iconset/icon_${size}x${size}@2x.png"; done
iconutil -c icns "$work/FullMoon.iconset" -o "$work/FullMoon.app/Contents/Resources/applet.icns" 2>/dev/null || true
codesign --force --deep -s - "$work/FullMoon.app" >/dev/null 2>&1 || true
mkdir -p "$DESKTOP"
rm -rf "$DESKTOP/FullMoon.app"
cp -R "$work/FullMoon.app" "$DESKTOP/FullMoon.app" && touch "$DESKTOP/FullMoon.app"
rm -rf "$work"
ok "Raccourci « FullMoon » créé sur le Bureau"

print -- "\n${green}${bold}Installation terminée.${off}"
print -- "Double-cliquez sur ${bold}FullMoon${off} sur le Bureau pour ouvrir l'application."
print -- "${dim}Pour mettre à jour plus tard : relancez simplement ce script.${off}\n"
