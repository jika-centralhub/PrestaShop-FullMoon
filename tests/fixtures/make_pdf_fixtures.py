"""Regenerate the synthetic PDF fixtures (fictitious data only, never real documents).

Not used by the tests themselves. Requires reportlab, which is not an application dependency:
    python -m pip install reportlab && python tests/fixtures/make_pdf_fixtures.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
ROWS = [
    ['00123', '000456', '0001234567890', '12', 'Produit fictif A'],
    # Wrapped supplier reference and quantity: must be rebuilt as ABC-0099 and 1200.
    ['789', 'ABC-\n0099', '3000000000017', '1\n200', 'Produit fictif B\nsur deux lignes'],
]


def build(name, header, table=True):
    style = getSampleStyleSheet()['Normal']
    story = [Paragraph(line, style) for line in header] + [Spacer(1, 12)]
    if table:
        grid = Table([['Réf. interne', 'Réf. fournisseur', 'EAN', 'Qté', 'Nom du produit']] + ROWS,
                     colWidths=[70, 90, 110, 40, 200])
        grid.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.black), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story.append(grid)
    SimpleDocTemplate(str(HERE / name), pagesize=A4).build(story)


if __name__ == '__main__':
    build('acheteur.pdf', ['Fournisseur : Exemple Fournisseur', 'Acheteur : Exemple Client SARL', 'E-mail : acheteur@example.test'])
    build('acheteur-et-fournisseur.pdf', ['Fournisseur : Exemple Fournisseur', 'E-mail : fournisseur@example.test',
                                          'Acheteur : Exemple Client SARL', 'E-mail : acheteur@example.test'])
    build('sans-acheteur.pdf', ['Fournisseur : Exemple Fournisseur', 'E-mail : fournisseur@example.test'])
    build('format-inconnu.pdf', ['Document sans tableau de commande'], table=False)
