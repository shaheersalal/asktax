"""Insert newly discovered statute PDFs into fbr_documents as pending."""
import sys
sys.path.insert(0, '/app')
from app.db.session import SessionLocal
from sqlalchemy import text

DOCS = [
    # IT Ordinance — new 2026 version
    {'title': 'Income Tax Ordinance, 2001 Amended upto 20.02.2026',
     'url': 'https://download1.fbr.gov.pk/Docs/2026226162211364IncomeTaxOrdinance2001-Amended-20.02.2026.pdf',
     'category': 'income_tax_ordinance', 'doc_date': '2026-02-20'},

    # Sales Tax Act 1990 — yearly versions 2019-2025
    {'title': 'Sales Tax Act 1990 amended upto 30-06-2025',
     'url': 'https://download1.fbr.gov.pk/Docs/202586148252375SalesTaxActupdatedupto2025-26.pdf',
     'category': 'sales_tax_act', 'doc_date': '2025-06-30'},
    {'title': 'Sales Tax Act 1990 amended upto 30.06.2024',
     'url': 'https://download1.fbr.gov.pk/Docs/20247231874252122SalesTaxAct,1990updatedbyFinanceAct,2024upto30.06.2024--12.07.2024.pdf',
     'category': 'sales_tax_act', 'doc_date': '2024-06-30'},
    {'title': 'Sales Tax Act 1990 amended upto 07.05.2024',
     'url': 'https://download1.fbr.gov.pk/Docs/20245161352214828SalesTaxAct1990withIndexupdatedupto07.05.2024.pdf',
     'category': 'sales_tax_act', 'doc_date': '2024-05-07'},
    {'title': 'Sales Tax Act 1990 amended upto 30.06.2023',
     'url': 'https://download1.fbr.gov.pk/Docs/20237418746219SalesTaxAct,1990withIndexupdatedupto30.06.2023.pdf',
     'category': 'sales_tax_act', 'doc_date': '2023-06-30'},
    {'title': 'Sales Tax Act 1990 as amended up to 30.06.2022',
     'url': 'https://download1.fbr.gov.pk/Docs/20227191074612108SalesTaxAct,1990withIndexupdatedupto30.06.2022.pdf',
     'category': 'sales_tax_act', 'doc_date': '2022-06-30'},
    {'title': 'Sales Tax Act 1990 as amended up to 30.06.2021',
     'url': 'https://download1.fbr.gov.pk/Docs/2021102913104021295SalesTaxAct,1990updatedupto30.06.2021withcontents.pdf',
     'category': 'sales_tax_act', 'doc_date': '2021-06-30'},
    {'title': 'Sales Tax Act 1990 as amended up to 30.06.2020',
     'url': 'https://download1.fbr.gov.pk/Docs/2020812168165722SalesTaxAct,1990updatedupto(30.06.2020)--.pdf',
     'category': 'sales_tax_act', 'doc_date': '2020-06-30'},
    {'title': 'Sales Tax Act 1990 as amended up to 30.06.2019',
     'url': 'https://download1.fbr.gov.pk/Docs/20191041410503505SalesTaxAct,1990(30.06.19)-bookversion.pdf',
     'category': 'sales_tax_act', 'doc_date': '2019-06-30'},

    # Sales Tax Rules 2006
    {'title': 'Sales Tax Rules 2006 updated upto 30-06-2025',
     'url': 'https://download1.fbr.gov.pk/Docs/2025881385446623STR-2006-UpdatedUpto06-08-2025(ver-iv).pdf',
     'category': 'sales_tax_rules', 'doc_date': '2025-06-30'},
    {'title': 'Sales Tax Rules 2006 Updated upto 01-01-2025',
     'url': 'https://download1.fbr.gov.pk/Docs/20251141513138962Sales-Tax-Rules-2006-UpdatedUpto01-01-2025.pdf',
     'category': 'sales_tax_rules', 'doc_date': '2025-01-01'},
    {'title': 'Sales Tax Rules 2006 updated upto 31.10.2023',
     'url': 'https://download1.fbr.gov.pk/Docs/2023111018111745261Sales-Tax-Rules-2006-Updated-upto31-10-2023.pdf',
     'category': 'sales_tax_rules', 'doc_date': '2023-10-31'},

    # Federal Excise Act 2005 — yearly versions
    {'title': 'Federal Excise Act 2005 amended upto 30-06-2025',
     'url': 'https://download1.fbr.gov.pk/Docs/202588138517680FEDAct,2005withindexupdatedupto30-06-2025.pdf',
     'category': 'federal_excise_act', 'doc_date': '2025-06-30'},
    {'title': 'Federal Excise Act 2005 amended upto 30.06.2024',
     'url': 'https://download1.fbr.gov.pk/Docs/2024723187395487FEAct,2005withindexupdatedupto30-06-2024--12.07.2024-1.pdf',
     'category': 'federal_excise_act', 'doc_date': '2024-06-30'},
    {'title': 'Federal Excise Act 2005 amended upto 30.06.2023',
     'url': 'https://download1.fbr.gov.pk/Docs/2023741871156937FEAct,2005withindexupdatedupto30-06-2023.pdf',
     'category': 'federal_excise_act', 'doc_date': '2023-06-30'},
    {'title': 'Federal Excise Act 2005 as amended up to 30.06.2022',
     'url': 'https://download1.fbr.gov.pk/Docs/20227191075110861FEAct,2005updatedupto30-06-2022(updated).pdf',
     'category': 'federal_excise_act', 'doc_date': '2022-06-30'},
    {'title': 'Federal Excise Act 2005 as amended up to 30.06.2021',
     'url': 'https://download1.fbr.gov.pk/Docs/202112113125139136FEAct2005updatedupto30-06-2021.pdf',
     'category': 'federal_excise_act', 'doc_date': '2021-06-30'},
    {'title': 'Federal Excise Act 2005 as amended up to 30.06.2020',
     'url': 'https://download1.fbr.gov.pk/Docs/2020812168190190FEDAct,2005updatedupto30-06-2020--.pdf',
     'category': 'federal_excise_act', 'doc_date': '2020-06-30'},
    {'title': 'Federal Excise Act 2005 as amended up to 30.06.2019',
     'url': 'https://download1.fbr.gov.pk/Docs/2019101111101143848FEDAct,2005.pdf',
     'category': 'federal_excise_act', 'doc_date': '2019-06-30'},

    # Federal Excise Rules 2005
    {'title': 'Federal Excise Rules 2005 updated upto 31.10.2023',
     'url': 'https://download1.fbr.gov.pk/Docs/2023111018112130929FED-Rules-2005-updated-upto-31.10.2023.pdf',
     'category': 'federal_excise_rules', 'doc_date': '2023-10-31'},

    # Income Tax Rules 2002
    {'title': 'Income Tax Rules 2002 Amended upto 24.11.2023',
     'url': 'https://download1.fbr.gov.pk/Docs/2023112416114319348IncomeTaxRules2002AmendedUpto24.11.2023.pdf',
     'category': 'income_tax_rules', 'doc_date': '2023-11-24'},
    {'title': 'Income Tax Rules 2002 Amended upto 8th September 2020',
     'url': 'https://download1.fbr.gov.pk/Docs/202010211102851544IncomeTaxRules2002.pdf',
     'category': 'income_tax_rules', 'doc_date': '2020-09-08'},

    # Customs Tariff
    {'title': 'Pakistan Customs Tariff 2025-26',
     'url': 'https://download1.fbr.gov.pk/Docs/20258111683941732Tariff-2025-26.pdf',
     'category': 'customs_act', 'doc_date': '2025-07-01'},
    {'title': 'Pakistan Customs Tariff 2020-21',
     'url': 'https://download1.fbr.gov.pk/Docs/202011613115526330CustomsTariff(Ch01-97).pdf',
     'category': 'customs_act', 'doc_date': '2020-07-01'},
]

db = SessionLocal()
saved = skipped = 0
for doc in DOCS:
    try:
        result = db.execute(text(
            "INSERT INTO fbr_documents (title, url, category, status, doc_date) "
            "VALUES (:title, :url, :category, 'pending', :doc_date) "
            "ON CONFLICT (url) DO NOTHING"
        ), doc)
        if result.rowcount > 0:
            saved += 1
            print(f"  + {doc['title'][:70]}")
        else:
            skipped += 1
    except Exception as e:
        print(f"ERR {doc['title'][:50]}: {e}")

db.commit()
db.close()
print(f"\nSaved: {saved}, Skipped (already exist): {skipped}")
