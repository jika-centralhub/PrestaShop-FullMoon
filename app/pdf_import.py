"""Local text-PDF import for the five-column supplier-order layout.
No OCR, network, embedded action execution, or customer mutation.
"""
import io
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c)).replace('\n', ' ')


HEADER_STOPWORDS = set('''
acheteur acheteuse fournisseur vendeur adresse adresses facturation livraison commande bon date email mail courriel
telephone tel fax portable mobile contact reference ref page siret tva intracom numero client societe nom
rue avenue boulevard chemin route place allee impasse quai cours square france cedex zone parc batiment lot
les des une pour par sur avec dans aux est
janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre
lundi mardi mercredi jeudi vendredi samedi dimanche fullmoon distribution
'''.split())


def header_terms(header):
    """Significant words of the order header (buyer, company, addresses), to suggest likely customers.

    The title line, the supplier name, e-mail addresses, numbers and common words are left out.
    These words only rank suggestions in the local page; they never select a customer on their own.
    """
    terms = []
    for line in header.splitlines():
        text = normalized(line).strip()
        if text.startswith('commande'):
            continue
        text = re.sub(r'fournisseur\s*:.*?(?=adresse|$)', ' ', text)
        text = re.sub(r'\S+@\S+', ' ', text)
        terms += [w for w in re.findall(r'[a-z]{3,}', text) if w not in HEADER_STOPWORDS]
    return list(dict.fromkeys(terms))[:40]


def customer_hint(header):
    # Only an explicitly labelled buyer-contact email; never the supplier/title.
    terms = header_terms(header)
    if not re.search(r'acheteur\s*:', normalized(header)):
        return {'email': None, 'reason': 'Acheteur non identifié dans l’en-tête.', 'terms': terms}
    matches = re.findall(r'(?im)\b(?:e-?mail|courriel|mail)\s*:\s*([^\s<>]+@[^\s<>]+\.[a-z]{2,})', header)
    emails = list(dict.fromkeys(x.rstrip('.,;').lower() for x in matches))
    if len(emails) != 1:
        return {'email': None, 'reason': 'E-mail acheteur absent ou ambigu.', 'terms': terms}
    return {'email': emails[0], 'reason': 'E-mail du contact acheteur extrait de l’en-tête.', 'terms': terms}


def parse(data):
    import pdfplumber
    if not data.startswith(b'%PDF-') or len(data) > 8_000_000:
        raise ValueError('PDF invalide ou supérieur à 8 Mo.')
    output = [['Référence fournisseur', 'Quantité', 'Nom du produit', 'Référence interne', 'EAN', 'Page']]
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if not 1 <= len(pdf.pages) <= 20:
            raise ValueError('PDF limité à 20 pages.')
        first = pdf.pages[0]
        headers = []
        for table in first.find_tables():
            for cells, row in zip(table.rows, table.extract()):
                joined = normalized(' '.join(str(x or '') for x in row))
                if len(row) == 5 and all(word in joined for word in ('interne', 'fournisseur', 'ean', 'qte', 'produit')):
                    headers.append(cells.cells)
        if len(headers) != 1 or any(c is None for c in headers[0]):
            raise ValueError('Format PDF non reconnu. Utilisez un PDF texte avec les colonnes Réf. interne, Réf. fournisseur, EAN, Qté et Nom du produit. Les scans ne sont pas pris en charge.')
        cells = headers[0]
        bounds = [c[0] for c in cells] + [cells[-1][2]]
        top = max(c[3] for c in cells)
        hint = customer_hint(first.crop((0, 0, first.width, min(c[1] for c in cells))).extract_text() or '')
        for page_index, page in enumerate(pdf.pages):
            if abs(page.width-first.width) > 1 or page.rotation:
                raise ValueError('Orientation ou largeur de page différente. Import PDF interrompu.')
            if len(page.chars) > 100000:
                raise ValueError('Page PDF trop volumineuse.')
            stop = page.height - 60
            # Continuation pages in this layout start with a full-width separator.
            separators = sorted({e['top'] for e in page.edges if e.get('orientation') == 'h' and abs(e['x0']-bounds[0]) < 2 and abs(e['x1']-bounds[-1]) < 2 and e['top'] < stop})
            start = top if page_index == 0 else (separators[0] if separators else None)
            if start is None:
                raise ValueError(f'Page {page_index+1} non reconnue. Aucun import partiel effectué.')
            words = page.extract_words(x_tolerance=2, y_tolerance=2)
            left = sorted([w for w in words if bounds[0] <= w['x0'] < bounds[1] and start < w['top'] < stop], key=lambda w: (w['top'],w['x0']))
            if not left or any(not re.fullmatch(r'\d{1,30}',w['text']) for w in left):
                raise ValueError(f'Références internes illisibles page {page_index+1}. Aucun import partiel effectué.')
            for index, anchor in enumerate(left):
                end = left[index+1]['top']-2 if index+1 < len(left) else stop
                beginning = anchor['top']-2
                if end <= beginning:
                    raise ValueError('Lignes PDF ambiguës.')
                values = []
                for column in range(5):
                    text = page.crop((bounds[column], beginning, bounds[column+1], end)).extract_text(x_tolerance=2, y_tolerance=2) or ''
                    values.append(text.strip())
                internal, reference, ean, quantity, name = values
                # Join wrapped identifiers and wrapped digits within their own cells only.
                reference = ''.join(reference.split())
                ean = ''.join(ean.split())
                pieces = quantity.split()
                if internal != anchor['text'] or not reference or not name or not pieces or any(not re.fullmatch(r'\d+',p) for p in pieces):
                    raise ValueError(f'Ligne illisible page {page_index+1}. Vérifiez le PDF ou utilisez le CSV.')
                quantity = ''.join(pieces)
                if not 0 < int(quantity) <= 100000000 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+/-]*',reference):
                    raise ValueError(f'Référence ou quantité invalide page {page_index+1}.')
                output.append([reference, quantity, ' '.join(name.split()), internal, ean, str(page_index+1)])
                if len(output) > 501:
                    raise ValueError('Import limité à 500 lignes.')
    return {'sheets': [{'name':'Commande PDF', 'rows':output}], 'customer_hint':hint,
            'pdf_review_required':True, 'notice':'Références fournisseur et quantités reconstituées par colonne. Vérifiez toutes les lignes extraites avant le contrôle des stocks.'}


def read_pdf(data):
    try:
        run = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve())], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        response = json.loads(run.stdout)
    except subprocess.TimeoutExpired:
        raise ValueError('Lecture PDF trop longue. Utilisez le CSV ou un PDF plus simple.') from None
    except (OSError, ValueError):
        raise ValueError('Lecture PDF indisponible. Vérifiez les dépendances de l’installation.') from None
    if 'error' in response:
        raise ValueError(response['error'])
    if run.returncode:
        raise ValueError('Lecture PDF interrompue.')
    return response


if __name__ == '__main__':
    try:
        result = parse(sys.stdin.buffer.read(8_000_001))
    except ValueError as error:
        result = {'error':str(error)}
    except Exception:
        result = {'error':'PDF illisible, protégé ou endommagé. Utilisez un PDF texte non protégé ou le CSV.'}
    # Explicit UTF-8 bytes: the console encoding is cp1252 on Windows, and read_pdf expects UTF-8.
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))
    sys.stdout.buffer.flush()
