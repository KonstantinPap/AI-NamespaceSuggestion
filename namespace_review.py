import os
import re
import concurrent.futures
from pathlib import Path
from typing import Dict, Tuple, Optional, List
from langchain_community.chat_models import AzureChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain.schema import SystemMessage, HumanMessage
import openai
import threading
import json
from rich.console import Console
from rich.markdown import Markdown

# ----------- KONSTANTEN -----------
OBJECT_NAME_TO_REVIEW = "KVSMEDCLLCMBGeneralMgtSub"
SEARCH_ROOTS = [
    "C:/Repos/DevOps/HC-Work/Product_MED/Product_MED_AL/app/",
    "C:/Repos/DevOps/MTC-Work/Product_MED_Tech365/Product_MED_Tech/app/",
    "C:/Repos/Github/StefanMaron/MSDyn365BC.Code.History/BaseApp/Source/Base Application/",
    "C:/Repos/DevOps/HC-Work/Product_KBA/Product_KBA_BC_AL/app/",
]
OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY")
OPENAI_API_BASE = os.environ.get("AZURE_OPENAI_ENDPOINT")
OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2023-05-15")
OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
AL_INDEX_JSON = "al_index.json"
# ----------------------------------

OBJECT_PATTERN = re.compile(
    r'^(table|page|codeunit|report|xmlport|query|enum|interface|controladdin|pageextension|tableextension|enumextension|profile|dotnet|entitlement|permissionset|permissionsetextension|reportextension|enumvalue|entitlementset|entitlementsetextension)\s+(\d+)?\s*"?([\w\d_]+)"?',
    re.IGNORECASE
)
NAMESPACE_PATTERN = re.compile(r'Namespace\s*=\s*"([\w\d_]+)"', re.IGNORECASE)

def scan_al_file(filepath: str) -> Optional[Tuple[Tuple[str, str], Dict]]:
    """Extrahiere Objekttyp, Name, Namespace (wenn vorhanden), Verzeichnis, Pfad."""
    try:
        with open(filepath, encoding="utf-8") as f:
            lines = f.readlines()
        obj_type, obj_name, namespace = None, None, None
        for line in lines:
            if not obj_type:
                m = OBJECT_PATTERN.match(line.strip())
                if m:
                    obj_type = m.group(1)
                    obj_name = m.group(3)
            if not namespace:
                n = NAMESPACE_PATTERN.search(line)
                if n:
                    namespace = n.group(1)
            if obj_type and obj_name and namespace is not None:
                break
        if obj_type and obj_name:
            return (
                (obj_type.lower(), obj_name),
                {
                    "namespace": namespace,
                    "directory": str(Path(filepath).parent),
                    "filepath": filepath
                }
            )
    except Exception:
        pass
    return None

def parallel_scan_al_files(roots: List[str]) -> Dict[Tuple[str, str], Dict]:
    """Durchsuche alle AL-Dateien in den Verzeichnissen parallel und baue das Dict."""
    al_files = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if f.lower().endswith(".al"):
                    al_files.append(os.path.join(dirpath, f))
    result = {}
    total = len(al_files)
    print(f"Scanne {total} AL-Dateien ...", end="", flush=True)

    # I/O-bound: ThreadPoolExecutor ist hier schneller als ProcessPoolExecutor
    lock = threading.Lock()
    def process_file(filepath):
        r = scan_al_file(filepath)
        if r:
            key, val = r
            with lock:
                result[key] = val

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=os.cpu_count() * 4) as executor:
        futures = []
        for filepath in al_files:
            futures.append(executor.submit(process_file, filepath))
        for idx, f in enumerate(futures, 1):
            f.result()  # Exceptions werden hier ausgelöst
            if idx % 500 == 0 or idx == total:
                print(".", end="", flush=True)
    print(" fertig.")
    return result

def find_object_file(obj_dict: Dict[Tuple[str, str], Dict], object_name: str) -> Optional[Tuple[str, str, Dict]]:
    """Finde das Objekt im Dict (case-insensitive nach Name)."""
    for (obj_type, obj_name), info in obj_dict.items():
        if obj_name.lower() == object_name.lower():
            return obj_type, obj_name, info
    return None

def read_file_content(filepath: str) -> str:
    with open(filepath, encoding="utf-8") as f:
        return f.read()

def extract_references_from_al(content: str) -> List[str]:
    """Extrahiere referenzierte Objektnamen (sehr einfach, kann erweitert werden)."""
    ref_pattern = re.compile(r'(Table|Page|Codeunit|Report|XmlPort|Query|Enum)\s*::\s*"?([\w\d_]+)"?', re.IGNORECASE)
    return list(set(m[1] for m in ref_pattern.findall(content)))

def langchain_analyse(object_type: str, object_name: str, al_content: str, context_objects: List[Dict]) -> str:
    """Führe die Analyse mit LangChain und OpenAI durch."""
    # Namespace-Beschreibungen
    allowed_namespaces_with_desc = [
        ("Microsoft", "Microsoft Standardfunktionalität und Basiskomponenten"),
        ("Microsoft.API", "Microsoft API-spezifische Komponenten und Schnittstellen"),
        ("Microsoft.API.Upgrade", "Upgrade-bezogene APIs und Migrationshilfen"),
        ("Microsoft.API.Webhooks", "Webhooks-Integration und Ereignisbenachrichtigungen"),
        ("Microsoft.AccountantPortal", "Funktionen für das Accountant Portal"),
        ("Microsoft.Assembly.Comment", "Kommentare und Anmerkungen im Bereich Montage"),
        ("Microsoft.Assembly.Costing", "Kalkulation und Kostenrechnung für Montage"),
        ("Microsoft.Assembly.Document", "Dokumentenmanagement im Montagebereich"),
        ("Microsoft.Assembly.History", "Historie und Protokollierung von Montageprozessen"),
        ("Microsoft.Assembly.Posting", "Buchungen und Verbuchungen im Montagebereich"),
        ("Microsoft.Assembly.Reports", "Berichte und Auswertungen zur Montage"),
        ("Microsoft.Assembly.Setup", "Einrichtung und Konfiguration der Montage"),
        ("Microsoft.Bank.BankAccount", "Bankkontenverwaltung"),
        ("Microsoft.Bank.Check", "Scheckverwaltung und -verarbeitung"),
        ("Microsoft.Bank.Deposit", "Einzahlungen und Bankeinlagen"),
        ("Microsoft.Bank.DirectDebit", "Lastschriftverfahren und SEPA-Lastschriften"),
        ("Microsoft.Bank.Ledger", "Bankbuchhaltung und Konten"),
        ("Microsoft.Bank.Payment", "Zahlungsabwicklung und Zahlungsverkehr"),
        ("Microsoft.Bank.PositivePay", "Positive Pay-Funktionen für Banken"),
        ("Microsoft.Bank.Reconciliation", "Bankabstimmung und Kontenabgleich"),
        ("Microsoft.Bank.Reports", "Bankbezogene Berichte und Auswertungen"),
        ("Microsoft.Bank.Setup", "Einrichtung und Konfiguration von Bankfunktionen"),
        ("Microsoft.Bank.Statement", "Bankauszüge und Kontoauszugsverarbeitung"),
        ("Microsoft.Booking", "Buchungsfunktionen und Reservierungen"),
        ("Microsoft.CRM.Analysis", "CRM-Analysen und Auswertungen"),
        ("Microsoft.CRM.BusinessRelation", "Geschäftsbeziehungen im CRM"),
        ("Microsoft.CRM.Campaign", "Kampagnenmanagement im CRM"),
        ("Microsoft.CRM.Comment", "Kommentare und Notizen im CRM"),
        ("Microsoft.CRM.Contact", "Kontaktverwaltung im CRM"),
        ("Microsoft.CRM.Duplicates", "Duplikaterkennung und -management im CRM"),
        ("Microsoft.CRM.Interaction", "Interaktionen und Aktivitäten im CRM"),
        ("Microsoft.CRM.Opportunity", "Vertriebschancen und Opportunities im CRM"),
        ("Microsoft.CRM.Outlook", "Outlook-Integration für CRM"),
        ("Microsoft.CRM.Profiling", "Profiling und Segmentierung im CRM"),
        ("Microsoft.CRM.Reports", "CRM-Berichte und Auswertungen"),
        ("Microsoft.CRM.RoleCenters", "Rollencenter für CRM-Anwender"),
        ("Microsoft.CRM.Segment", "Segmentierung und Zielgruppen im CRM"),
        ("Microsoft.CRM.Setup", "Einrichtung und Konfiguration des CRM"),
        ("Microsoft.CRM.Task", "Aufgabenmanagement im CRM"),
        ("Microsoft.CRM.Team", "Teamverwaltung im CRM"),
        ("Microsoft.CashFlow.Account", "Liquiditätskonten für Cashflow-Planung"),
        ("Microsoft.CashFlow.Comment", "Kommentare im Bereich Cashflow"),
        ("Microsoft.CashFlow.Forecast", "Cashflow-Prognosen und -Planung"),
        ("Microsoft.CashFlow.Reports", "Berichte zur Liquiditätsplanung"),
        ("Microsoft.CashFlow.Setup", "Einrichtung der Cashflow-Funktionen"),
        ("Microsoft.CashFlow.Worksheet", "Arbeitsblätter für Cashflow-Analysen"),
        ("Microsoft.CostAccounting.Account", "Kostenrechnungskonten"),
        ("Microsoft.CostAccounting.Allocation", "Kostenverteilung und Umlagen"),
        ("Microsoft.CostAccounting.Budget", "Kostenrechnungsbudgets"),
        ("Microsoft.CostAccounting.Journal", "Kostenrechnungsjournale"),
        ("Microsoft.CostAccounting.Ledger", "Kostenrechnungshauptbuch"),
        ("Microsoft.CostAccounting.Posting", "Buchungen in der Kostenrechnung"),
        ("Microsoft.CostAccounting.Reports", "Berichte zur Kostenrechnung"),
        ("Microsoft.CostAccounting.Setup", "Einrichtung der Kostenrechnung"),
        ("Microsoft.EServices.EDocument", "Elektronische Dokumente und eServices"),
        ("Microsoft.Finance.AllocationAccount", "Verteilungskonten im Finanzwesen"),
        ("Microsoft.Finance.AllocationAccount.Purchase", "Verteilungskonten für Einkauf"),
        ("Microsoft.Finance.AllocationAccount.Sales", "Verteilungskonten für Verkauf"),
        ("Microsoft.Finance.Analysis", "Finanzanalysen und Auswertungen"),
        ("Microsoft.Finance.AuditFileExport", "Export von Audit-Dateien"),
        ("Microsoft.Finance.Consolidation", "Konsolidierung im Finanzbereich"),
        ("Microsoft.Finance.Currency", "Währungsmanagement"),
        ("Microsoft.Finance.Deferral", "Abgrenzungen und Rechnungsabgrenzungsposten"),
        ("Microsoft.Finance.Dimension", "Dimensionen im Finanzwesen"),
        ("Microsoft.Finance.Dimension.Correction", "Korrekturen von Dimensionen"),
        ("Microsoft.Finance.FinancialReports", "Finanzberichte und Auswertungen"),
        ("Microsoft.Finance.GeneralLedger.Account", "Sachkonten im Hauptbuch"),
        ("Microsoft.Finance.GeneralLedger.Budget", "Budgets im Hauptbuch"),
        ("Microsoft.Finance.GeneralLedger.Journal", "Journale im Hauptbuch"),
        ("Microsoft.Finance.GeneralLedger.Ledger", "Hauptbuchfunktionen"),
        ("Microsoft.Finance.GeneralLedger.Posting", "Buchungen im Hauptbuch"),
        ("Microsoft.Finance.GeneralLedger.Preview", "Vorschau von Hauptbuchbuchungen"),
        ("Microsoft.Finance.GeneralLedger.Reports", "Berichte zum Hauptbuch"),
        ("Microsoft.Finance.GeneralLedger.Reversal", "Stornierungen im Hauptbuch"),
        ("Microsoft.Finance.GeneralLedger.Setup", "Einrichtung des Hauptbuchs"),
        ("Microsoft.Finance.Payroll", "Lohn- und Gehaltsabrechnung"),
        ("Microsoft.Finance.ReceivablesPayables", "Debitoren- und Kreditorenbuchhaltung"),
        ("Microsoft.Finance.RoleCenters", "Rollencenter für Finanzanwender"),
        ("Microsoft.Finance.SalesTax", "Umsatzsteuerverwaltung"),
        ("Microsoft.Finance.VAT", "Mehrwertsteuerverwaltung"),
        ("Microsoft.Finance.VAT.Calculation", "Berechnung der Mehrwertsteuer"),
        ("Microsoft.Finance.VAT.Clause", "Mehrwertsteuerklauseln"),
        ("Microsoft.Finance.VAT.Ledger", "Mehrwertsteuerhauptbuch"),
        ("Microsoft.Finance.VAT.RateChange", "Mehrwertsteuersatzänderungen"),
        ("Microsoft.Finance.VAT.Registration", "Mehrwertsteuerregistrierung"),
        ("Microsoft.Finance.VAT.Reporting", "Mehrwertsteuerberichte"),
        ("Microsoft.Finance.VAT.Setup", "Einrichtung der Mehrwertsteuer"),
        ("Microsoft.FixedAssets.Depreciation", "Abschreibungen auf Anlagegüter"),
        ("Microsoft.FixedAssets.FixedAsset", "Verwaltung von Anlagegütern"),
        ("Microsoft.FixedAssets.Insurance", "Versicherung von Anlagegütern"),
        ("Microsoft.FixedAssets.Journal", "Anlagenjournale"),
        ("Microsoft.FixedAssets.Ledger", "Anlagenhauptbuch"),
        ("Microsoft.FixedAssets.Maintenance", "Wartung von Anlagegütern"),
        ("Microsoft.FixedAssets.Posting", "Buchungen im Anlagenbereich"),
        ("Microsoft.FixedAssets.Reports", "Berichte zu Anlagegütern"),
        ("Microsoft.FixedAssets.Setup", "Einrichtung der Anlagenbuchhaltung"),
        ("Microsoft.Foundation.Address", "Adressverwaltung"),
        ("Microsoft.Foundation.Attachment", "Anhänge und Dokumentenmanagement"),
        ("Microsoft.Foundation.AuditCodes", "Audit-Codes und Prüfungsfunktionen"),
        ("Microsoft.Foundation.BatchProcessing", "Stapelverarbeitung und Hintergrundprozesse"),
        ("Microsoft.Foundation.Calendar", "Kalenderfunktionen"),
        ("Microsoft.Foundation.Comment", "Kommentare und Notizen"),
        ("Microsoft.Foundation.Company", "Unternehmensverwaltung"),
        ("Microsoft.Foundation.Enums", "Aufzählungstypen und Enums"),
        ("Microsoft.Foundation.ExtendedText", "Erweiterte Textfunktionen"),
        ("Microsoft.Foundation.Navigate", "Navigationsfunktionen"),
        ("Microsoft.Foundation.NoSeries", "Nummernserienverwaltung"),
        ("Microsoft.Foundation.PaymentTerms", "Zahlungsbedingungen"),
        ("Microsoft.Foundation.Period", "Periodenverwaltung"),
        ("Microsoft.Foundation.Reporting", "Berichtswesen und Reporting"),
        ("Microsoft.Foundation.Shipping", "Versand und Logistik"),
        ("Microsoft.Foundation.Task", "Aufgabenverwaltung"),
        ("Microsoft.Foundation.UOM", "Mengeneinheitenverwaltung"),
        ("Microsoft.HumanResources.Absence", "Abwesenheitsverwaltung"),
        ("Microsoft.HumanResources.Analysis", "Analysen im Personalbereich"),
        ("Microsoft.HumanResources.Comment", "Kommentare im Personalbereich"),
        ("Microsoft.HumanResources.Employee", "Mitarbeiterverwaltung"),
        ("Microsoft.HumanResources.Payables", "Verbindlichkeiten im Personalbereich"),
        ("Microsoft.HumanResources.Reports", "Berichte im Personalbereich"),
        ("Microsoft.HumanResources.RoleCenters", "Rollencenter für Personalwesen"),
        ("Microsoft.HumanResources.Setup", "Einrichtung des Personalbereichs"),
        ("Microsoft.Integration.D365Sales", "Integration mit Dynamics 365 Sales"),
        ("Microsoft.Integration.Dataverse", "Integration mit Dataverse"),
        ("Microsoft.Integration.Entity", "Integration von Entitäten"),
        ("Microsoft.Integration.FieldService", "Integration mit Field Service"),
        ("Microsoft.Integration.Graph", "Microsoft Graph-Integration"),
        ("Microsoft.Integration.PowerBI", "Power BI-Integration"),
        ("Microsoft.Integration.SyncEngine", "Synchronisationsengine"),
        ("Microsoft.Intercompany", "Intercompany-Funktionen"),
        ("Microsoft.Intercompany.BankAccount", "Intercompany-Bankkonten"),
        ("Microsoft.Intercompany.Comment", "Kommentare im Intercompany-Bereich"),
        ("Microsoft.Intercompany.DataExchange", "Datenaustausch zwischen Unternehmen"),
        ("Microsoft.Intercompany.Dimension", "Dimensionen im Intercompany-Bereich"),
        ("Microsoft.Intercompany.GLAccount", "Sachkonten im Intercompany-Bereich"),
        ("Microsoft.Intercompany.Inbox", "Eingangskorb für Intercompany"),
        ("Microsoft.Intercompany.Journal", "Intercompany-Journale"),
        ("Microsoft.Intercompany.Outbox", "Ausgangskorb für Intercompany"),
        ("Microsoft.Intercompany.Partner", "Intercompany-Partner"),
        ("Microsoft.Intercompany.Reports", "Berichte im Intercompany-Bereich"),
        ("Microsoft.Intercompany.Setup", "Einrichtung des Intercompany-Bereichs"),
        ("Microsoft.Inventory", "Bestandsverwaltung"),
        ("Microsoft.Inventory.Analysis", "Bestandsanalysen"),
        ("Microsoft.Inventory.Availability", "Bestandsverfügbarkeit"),
        ("Microsoft.Inventory.BOM", "Stücklistenverwaltung"),
        ("Microsoft.Inventory.BOM.Tree", "Stücklistenstruktur"),
        ("Microsoft.Inventory.Comment", "Kommentare im Bestandsbereich"),
        ("Microsoft.Inventory.Costing", "Kostenrechnung im Bestandsbereich"),
        ("Microsoft.Inventory.Costing.ActionMessage", "Handlungsempfehlungen zur Kostenrechnung"),
        ("Microsoft.Inventory.Counting", "Inventurzählung"),
        ("Microsoft.Inventory.Counting.Comment", "Kommentare zur Inventurzählung"),
        ("Microsoft.Inventory.Counting.Document", "Dokumente zur Inventurzählung"),
        ("Microsoft.Inventory.Counting.History", "Historie der Inventurzählung"),
        ("Microsoft.Inventory.Counting.Journal", "Inventurzählungsjournale"),
        ("Microsoft.Inventory.Counting.Recording", "Erfassung der Inventurzählung"),
        ("Microsoft.Inventory.Counting.Reports", "Berichte zur Inventurzählung"),
        ("Microsoft.Inventory.Counting.Tracking", "Nachverfolgung der Inventurzählung"),
        ("Microsoft.Inventory.Document", "Bestandsdokumente"),
        ("Microsoft.Inventory.History", "Bestandshistorie"),
        ("Microsoft.Inventory.Intrastat", "Intrastat-Meldungen"),
        ("Microsoft.Inventory.Item", "Artikelverwaltung"),
        ("Microsoft.Inventory.Item.Attribute", "Artikelattribute"),
        ("Microsoft.Inventory.Item.Catalog", "Artikelkatalog"),
        ("Microsoft.Inventory.Item.Picture", "Artikelbilder"),
        ("Microsoft.Inventory.Item.Substitution", "Artikelersatz"),
        ("Microsoft.Inventory.Journal", "Bestandsjournale"),
        ("Microsoft.Inventory.Ledger", "Bestandshauptbuch"),
        ("Microsoft.Inventory.Location", "Lagerorte"),
        ("Microsoft.Inventory.MarketingText", "Marketingtexte für Artikel"),
        ("Microsoft.Inventory.Planning", "Bestandsplanung"),
        ("Microsoft.Inventory.Posting", "Buchungen im Bestandsbereich"),
        ("Microsoft.Inventory.Reconciliation", "Bestandsabstimmung"),
        ("Microsoft.Inventory.Reports", "Berichte zur Bestandsverwaltung"),
        ("Microsoft.Inventory.Requisition", "Bestellanforderungen"),
        ("Microsoft.Inventory.RoleCenters", "Rollencenter für Bestandsverwaltung"),
        ("Microsoft.Inventory.Setup", "Einrichtung der Bestandsverwaltung"),
        ("Microsoft.Inventory.StandardCost", "Standardkosten im Bestand"),
        ("Microsoft.Inventory.Tracking", "Bestandsnachverfolgung"),
        ("Microsoft.Inventory.Transfer", "Bestandsübertragungen"),
        ("Microsoft.Iventory.Item", "Artikelverwaltung (Tippfehler: Inventory)"),
        ("Microsoft.Manufacturing.Capacity", "Kapazitätsplanung in der Fertigung"),
        ("Microsoft.Manufacturing.Comment", "Kommentare im Fertigungsbereich"),
        ("Microsoft.Manufacturing.Document", "Fertigungsdokumente"),
        ("Microsoft.Manufacturing.Family", "Fertigungsfamilien"),
        ("Microsoft.Manufacturing.Forecast", "Fertigungsprognosen"),
        ("Microsoft.Manufacturing.Integration", "Integrationen im Fertigungsbereich"),
        ("Microsoft.Manufacturing.Journal", "Fertigungsjournale"),
        ("Microsoft.Manufacturing.MachineCenter", "Maschinenzentren in der Fertigung"),
        ("Microsoft.Manufacturing.Planning", "Fertigungsplanung"),
        ("Microsoft.Manufacturing.ProductionBOM", "Produktionsstücklisten"),
        ("Microsoft.Manufacturing.Reports", "Berichte zur Fertigung"),
        ("Microsoft.Manufacturing.RoleCenters", "Rollencenter für Fertigung"),
        ("Microsoft.Manufacturing.Routing", "Arbeitspläne in der Fertigung"),
        ("Microsoft.Manufacturing.Setup", "Einrichtung der Fertigung"),
        ("Microsoft.Manufacturing.StandardCost", "Standardkosten in der Fertigung"),
        ("Microsoft.Manufacturing.WorkCenter", "Arbeitszentren in der Fertigung"),
        ("Microsoft.Pricing.Asset", "Preisfindung für Anlagen"),
        ("Microsoft.Pricing.Calculation", "Preisberechnung"),
        ("Microsoft.Pricing.PriceList", "Preislistenverwaltung"),
        ("Microsoft.Pricing.Reports", "Berichte zur Preisfindung"),
        ("Microsoft.Pricing.Source", "Preisquellen"),
        ("Microsoft.Pricing.Worksheet", "Arbeitsblätter zur Preisfindung"),
        ("Microsoft.Projects.Project.Analysis", "Projektanalysen"),
        ("Microsoft.Projects.Project.Archive", "Projektarchivierung"),
        ("Microsoft.Projects.Project.Job", "Projektaufträge"),
        ("Microsoft.Projects.Project.Journal", "Projektjournale"),
        ("Microsoft.Projects.Project.Ledger", "Projekthauptbuch"),
        ("Microsoft.Projects.Project.Planning", "Projektplanung"),
        ("Microsoft.Projects.Project.Posting", "Projektbuchungen"),
        ("Microsoft.Projects.Project.Pricing", "Projektpreisfindung"),
        ("Microsoft.Projects.Project.Reports", "Projektberichte"),
        ("Microsoft.Projects.Project.Setup", "Einrichtung des Projektbereichs"),
        ("Microsoft.Projects.Project.WIP", "Work in Progress im Projektbereich"),
        ("Microsoft.Projects.Resources.Analysis", "Analyse von Projektressourcen"),
        ("Microsoft.Projects.Resources.Journal", "Journale für Projektressourcen"),
        ("Microsoft.Projects.Resources.Ledger", "Hauptbuch für Projektressourcen"),
        ("Microsoft.Projects.Resources.Pricing", "Preisfindung für Projektressourcen"),
        ("Microsoft.Projects.Resources.Reports", "Berichte zu Projektressourcen"),
        ("Microsoft.Projects.Resources.Resource", "Projektressourcenverwaltung"),
        ("Microsoft.Projects.Resources.Setup", "Einrichtung der Projektressourcen"),
        ("Microsoft.Projects.RoleCenters", "Rollencenter für Projekte"),
        ("Microsoft.Projects.TimeSheet", "Projekt-Zeiterfassung"),
        ("Microsoft.Purchases.Analysis", "Einkaufsanalysen"),
        ("Microsoft.Purchases.Archive", "Archivierung von Einkaufsbelegen"),
        ("Microsoft.Purchases.Comment", "Kommentare im Einkaufsbereich"),
        ("Microsoft.Purchases.Document", "Einkaufsdokumente"),
        ("Microsoft.Purchases.History", "Einkaufshistorie"),
        ("Microsoft.Purchases.Payables", "Verbindlichkeiten im Einkauf"),
        ("Microsoft.Purchases.Posting", "Buchungen im Einkauf"),
        ("Microsoft.Purchases.Pricing", "Preisfindung im Einkauf"),
        ("Microsoft.Purchases.Remittance", "Zahlungsavis im Einkauf"),
        ("Microsoft.Purchases.Reports", "Berichte zum Einkauf"),
        ("Microsoft.Purchases.RoleCenters", "Rollencenter für Einkauf"),
        ("Microsoft.Purchases.Setup", "Einrichtung des Einkaufsbereichs"),
        ("Microsoft.Purchases.Vendor", "Lieferantenverwaltung"),
        ("Microsoft.RoleCenters", "Allgemeine Rollencenter"),
        ("Microsoft.Sales.Analysis", "Verkaufsanalysen"),
        ("Microsoft.Sales.Archive", "Archivierung von Verkaufsbelegen"),
        ("Microsoft.Sales.Comment", "Kommentare im Verkaufsbereich"),
        ("Microsoft.Sales.Customer", "Kundenverwaltung"),
        ("Microsoft.Sales.Document", "Verkaufsdokumente"),
        ("Microsoft.Sales.FinanceCharge", "Finanzierungsgebühren im Verkauf"),
        ("Microsoft.Sales.History", "Verkaufshistorie"),
        ("Microsoft.Sales.Peppol", "PEPPOL-Integration im Verkauf"),
        ("Microsoft.Sales.Posting", "Buchungen im Verkauf"),
        ("Microsoft.Sales.Pricing", "Preisfindung im Verkauf"),
        ("Microsoft.Sales.Receivables", "Forderungen aus Lieferungen und Leistungen"),
        ("Microsoft.Sales.Reminder", "Zahlungserinnerungen im Verkauf"),
        ("Microsoft.Sales.Reports", "Berichte zum Verkauf"),
        ("Microsoft.Sales.RoleCenters", "Rollencenter für Verkauf"),
        ("Microsoft.Sales.Setup", "Einrichtung des Verkaufsbereichs"),
        ("Microsoft.Service.Analysis", "Serviceanalysen"),
        ("Microsoft.Service.Archive", "Archivierung von Servicebelegen"),
        ("Microsoft.Service.BaseApp", "Service-Basisfunktionen"),
        ("Microsoft.Service.CashFlow", "Servicebezogene Cashflow-Funktionen"),
        ("Microsoft.Service.Comment", "Kommentare im Servicebereich"),
        ("Microsoft.Service.Contract", "Serviceverträge"),
        ("Microsoft.Service.Customer", "Servicekundenverwaltung"),
        ("Microsoft.Service.Document", "Servicedokumente"),
        ("Microsoft.Service.Email", "E-Mail-Kommunikation im Service"),
        ("Microsoft.Service.History", "Servicehistorie"),
        ("Microsoft.Service.Item", "Serviceartikelverwaltung"),
        ("Microsoft.Service.Ledger", "Servicehauptbuch"),
        ("Microsoft.Service.Loaner", "Leihstellungen im Service"),
        ("Microsoft.Service.Maintenance", "Wartung im Servicebereich"),
        ("Microsoft.Service.Posting", "Buchungen im Servicebereich"),
        ("Microsoft.Service.Pricing", "Preisfindung im Service"),
        ("Microsoft.Service.Reports", "Berichte zum Service"),
        ("Microsoft.Service.Resources", "Servicebezogene Ressourcen"),
        ("Microsoft.Service.RoleCenters", "Rollencenter für Service"),
        ("Microsoft.Service.Setup", "Einrichtung des Servicebereichs"),
        ("Microsoft.Shared.Report", "Geteilte Berichte und Reportings"),
        ("Microsoft.System.Threading", "Nebenläufigkeit und Threading"),
        ("Microsoft.Upgrade", "Upgrade- und Migrationsfunktionen"),
        ("Microsoft.Utilities", "Hilfsfunktionen und Utilities"),
        ("Microsoft.Warehouse.ADCS", "Automatisierte Datenerfassung im Lager"),
        ("Microsoft.Warehouse.Activity", "Lageraktivitäten"),
        ("Microsoft.Warehouse.Activity.History", "Historie der Lageraktivitäten"),
        ("Microsoft.Warehouse.Availability", "Lagerverfügbarkeit"),
        ("Microsoft.Warehouse.Comment", "Kommentare im Lagerbereich"),
        ("Microsoft.Warehouse.CrossDock", "Cross-Docking im Lager"),
        ("Microsoft.Warehouse.Document", "Lagerdokumente"),
        ("Microsoft.Warehouse.History", "Lagerhistorie"),
        ("Microsoft.Warehouse.InternalDocument", "Interne Lagerdokumente"),
        ("Microsoft.Warehouse.InventoryDocument", "Bestandsdokumente im Lager"),
        ("Microsoft.Warehouse.Journal", "Lagerjournale"),
        ("Microsoft.Warehouse.Ledger", "Lagerhauptbuch"),
        ("Microsoft.Warehouse.Posting", "Buchungen im Lagerbereich"),
        ("Microsoft.Warehouse.Reports", "Berichte zum Lager"),
        ("Microsoft.Warehouse.Request", "Lageranforderungen"),
        ("Microsoft.Warehouse.RoleCenters", "Rollencenter für Lager"),
        ("Microsoft.Warehouse.Setup", "Einrichtung des Lagerbereichs"),
        ("Microsoft.Warehouse.Structure", "Lagerstruktur"),
        ("Microsoft.Warehouse.Tracking", "Lagerverfolgung"),
        ("Microsoft.Warehouse.Worksheet", "Lagerarbeitsblätter"),
        ("Microsoft.costaccounting.Reports", "Berichte zur Kostenrechnung (Legacy)"),
        ("Microsoft.eServices.OnlineMap", "Online-Kartendienste"),
        ("System.AI", "Künstliche Intelligenz und Machine Learning"),
        ("System.Apps", "Systemanwendungen und App-Management"),
        ("System.Automation", "Automatisierungsfunktionen"),
        ("System.Azure.Identity", "Azure-Identitätsdienste"),
        ("System.DataAdministration", "Datenadministration und -management"),
        ("System.DateTime", "Datum- und Zeitfunktionen"),
        ("System.Device", "Geräteverwaltung"),
        ("System.Diagnostics", "Diagnose- und Überwachungsfunktionen"),
        ("System.EMail", "E-Mail-Kommunikation"),
        ("System.Email", "E-Mail-Kommunikation"),
        ("System.Environment", "Systemumgebung und Konfiguration"),
        ("System.Environment.Configuration", "Konfiguration der Systemumgebung"),
        ("System.Feedback", "Feedback- und Rückmeldungsfunktionen"),
        ("System.Globalization", "Globalisierung und Lokalisierung"),
        ("System.IO", "Datei- und Datenzugriff"),
        ("System.Integration", "Systemintegration"),
        ("System.Integration.PowerBI", "Power BI-Integration auf Systemebene"),
        ("System.Media", "Medienverwaltung"),
        ("System.Privacy", "Datenschutz und Privatsphäre"),
        ("System.Reflection", "Reflexion und Metadaten"),
        ("System.Security.AccessControl", "Zugriffssteuerung und Sicherheit"),
        ("System.Security.Authentication", "Authentifizierungsfunktionen"),
        ("System.Security.Encryption", "Verschlüsselung und Sicherheit"),
        ("System.Security.User", "Benutzerverwaltung und -sicherheit"),
        ("System.Telemetry", "Telemetrie und Überwachung"),
        ("System.TestTools", "Testwerkzeuge und Testautomatisierung"),
        ("System.TestTools.CodeCoverage", "Testabdeckung und Coverage"),
        ("System.TestTools.TestRunner", "Testausführung"),
        ("System.Text", "Textverarbeitung"),
        ("System.Threading", "Nebenläufigkeit und Threading"),
        ("System.Tooling", "Entwicklungswerkzeuge"),
        ("System.Utilities", "Systemnahe Hilfsfunktionen"),
        ("System.Visualization", "Visualisierung und Darstellung"),
        ("System.Xml", "XML-Verarbeitung"),
        # KUMAVISION Base (KBA)
        ("UDI", "Unique Device Identification (KUMAVISION base/KBA)"),
        ("Call", "Servicetickets und Anrufe (KUMAVISION base/KBA)"),
        ("LIF", "Etikettenmanagement/handling (KUMAVISION base/KBA)"),
        ("OrderQuote", "Angebots- und Auftragsverwaltung (KUMAVISION base/KBA)"),
        ("InventorySummary", "Bestandsübersicht (KUMAVISION base/KBA)"),
        ("Common", "KUMAVISION Basiskomponenten. Root Namespace"),
        # HC/MTC-spezifisch       
        ("ECE", "Elektronischer Datenaustausch (HC/MTC)"),
        ("MDR", "Medical Device Regulation (HC/MTC)"),
        ("Common", "Gemeinsame Komponenten für MTC/HC"),
    ]
    # allowed_namespaces_with_desc = [
    #     ("Warehouse", "Funktionen rund um Lager und Logistik"),
    #     ("Utilities", "Hilfsfunktionen und allgemeine Werkzeuge"),
    #     ("System", "Systemnahe Funktionen und Basiskomponenten"),
    #     ("Service", "Dienstleistungs- und Service-bezogene Funktionen"),
    #     ("Sales", "Vertriebs- und Verkaufsfunktionen"),
    #     ("RoleCenters", "Rollencenter und Benutzeroberflächen"),
    #     ("Purchases", "Einkaufsfunktionen"),
    #     ("Projects", "Projektmanagement und zugehörige Funktionen"),
    #     ("Profile", "Profileinstellungen und Benutzerprofile"),
    #     ("Pricing", "Preisfindung und Preisverwaltung"),
    #     ("OtherCapabilities", "Sonstige Fähigkeiten und Funktionen"),
    #     ("Manufacturing", "Fertigungs- und Produktionsfunktionen"),
    #     ("Invoicing", "Rechnungsstellung und Fakturierung"),
    #     ("Inventory", "Bestandsverwaltung und Lagerhaltung"),
    #     ("Integration", "Integration mit anderen Systemen"),
    #     ("HumanResources", "Personalverwaltung und HR-Funktionen"),
    #     ("Foundation", "Grundlagen und Basiselemente"),
    #     ("FixedAssets", "Anlagenbuchhaltung und Verwaltung"),
    #     ("Finance", "Finanzbuchhaltung und Controlling"),
    #     ("EServices", "Elektronische Dienste und Schnittstellen"),
    #     ("EDocument", "Elektronische Dokumente"),
    #     ("CRM", "Customer Relationship Management"),
    #     ("CostAccounting", "Kostenrechnung"),
    #     ("CashFlow", "Liquiditätsplanung und Zahlungsströme"),
    #     ("Bank", "Bankfunktionen und Zahlungsverkehr"),
    #     ("Assembly", "Montage und Zusammenbau"),
    #     ("API", "Programmierschnittstellen"),
    #     # KUMAVISION Base (KBA)
    #     ("UDI", "Unique Device Identification (KUMAVISION base/KBA)"),
    #     ("Call", "Servicetickets und Anrufe (KUMAVISION base/KBA)"),
    #     ("LIF", "Etikettenmanagement/handling (KUMAVISION base/KBA)"),
    #     ("OrderQuote", "Angebots- und Auftragsverwaltung (KUMAVISION base/KBA)"),
    #     ("InventorySummary", "Bestandsübersicht (KUMAVISION base/KBA)"),
    #     ("Common", "KUMAVISION Basiskomponenten. Root Namespace"),
    #     # HC/MTC-spezifisch       
    #     ("ECE", "Elektronischer Datenaustausch (HC/MTC)"),
    #     ("MDR", "Medical Device Regulation (HC/MTC)"),
    #     ("Common", "Gemeinsame Komponenten für MTC/HC"),
    # ]
    allowed_namespaces = [ns for ns, desc in allowed_namespaces_with_desc]
    # Prompt mit Namespace-Beschreibungen
    prompt = (
        "Du bist ein Experte für Microsoft Dynamics 365 Business Central AL-Entwicklung und die Vergabe von Namespaces.\n"
        "Analysiere das folgende AL-Objekt und schlage einen passenden Namespace vor. "
        "Beziehe dich dabei auf die Namenskonventionen der Microsoft Base Application. "
        "Wenn im Standard (Base Application) für ein Objekt oder dessen Funktionalität bereits ein Namespace wie z.B. 'System', 'Sales', etc. verwendet wird, "
        "sollen die HC- und MTC-Objekte möglichst denselben Namespace verwenden. "
        "Die Entscheidung für den Namespace soll sich vorrangig an den Objekten der Base Application orientieren.\n"
        "Falls ein anderer Namespace sinnvoller ist, begründe dies nachvollziehbar.\n"
        "Die Begründung (\"reason\") und alle Alternativen im JSON-Output müssen ausschließlich auf DEUTSCH formuliert sein.\n"
        "Du darfst ausschließlich einen Namespace aus folgender Liste verwenden (keinen anderen):\n"
        "WICHTIG: Gib im Feld \"namespace\" ausschließlich den Kurznamen (z.B. 'EServices', 'EDocument', 'Finance', 'Inventory', etc.) aus der Liste an – NICHT den vollständigen Namespace-Pfad wie 'Microsoft.EServices.EDocument'!\n"
        "Beispiel für den Output:\n"
        '{"namespace": "EServices", "reason": "...", "alternatives": [{"namespace": "EDocument", "reason": "..."}]}\n'
    )
    prompt += "\n".join([f"- {ns}: {desc}" for ns, desc in allowed_namespaces_with_desc])
    prompt += (
        "\nFalls keiner dieser Namespaces fachlich passt, wähle 'Custom' und begründe dies ausführlich.\n"
        f"\nObjekttyp: {object_type}\n"
        f"Objektname: {object_name}\n"
        f"AL-Code:\n{al_content}\n"
    )
    if context_objects:
        prompt += "\nKontextobjekte:\n"
        for ctx in context_objects:
            prompt += (
                f"- Name: {ctx.get('object_name','')}, Typ: {ctx.get('object_type','')}, "
                f"Namespace: {ctx.get('namespace','')}, Verzeichnis: {ctx.get('directory','')}\n"
            )
    prompt += (
        "\nDeine Aufgabe:\n"
        "- Analysiere, ob und wie das Objekt oder ähnliche Objekte in der Base Application einem bestimmten Namespace zugeordnet sind.\n"
        "- Schlage einen passenden Namespace aus der Liste der erlaubten Namespaces vor (bevorzugt den Namespace der Base Application, falls vorhanden).\n"
        "- Begründe deine Entscheidung ausführlich in deutscher Sprache.\n"
        "- Schlage, falls sinnvoll, alternative Namespaces vor und begründe diese Alternativen in deutscher Sprache.\n"
        "Gib das Ergebnis als JSON im folgenden Format zurück (ALLE Begründungen auf DEUTSCH!):\n"
        '{"namespace": "...", "reason": "...", "alternatives": [{"namespace": "...", "reason": "..."}]}'
        "Deine Empfehlung:"
    )
    llm = AzureChatOpenAI(
        openai_api_key=OPENAI_API_KEY,
        azure_endpoint=OPENAI_API_BASE,
        openai_api_version=OPENAI_API_VERSION,
        deployment_name=OPENAI_DEPLOYMENT,
        temperature=0.7,
        max_tokens=800,
    )
    messages = [
        SystemMessage(content="Du bist ein erfahrener AL-Entwickler und Namespace-Experte."),
        HumanMessage(content=prompt)
    ]
    response = llm(messages)
    return response.content

def print_namespace_result(result: str):
    import json
    import re

    console = Console(width=120)
    # Extrahiere JSON aus der Antwort
    match = re.search(r'\{.*\}', result, re.DOTALL)
    if not match:
        console.print("[red]Keine gültige JSON-Antwort erhalten.[/red]")
        print(result)
        return
    try:
        data = json.loads(match.group(0))
    except Exception:
        console.print("[red]Fehler beim Parsen der JSON-Antwort.[/red]")
        print(result)
        return

    ns = data.get("namespace", "")
    reason = data.get("reason", "")
    alternatives = data.get("alternatives", [])

    console.print("\n" + "="*60)
    console.print("[bold green]NAMESPACE-ANALYSE-ERGEBNIS[/bold green]".center(60))
    console.print("="*60)
    console.print(f"[bold]Namespace Vorschlag:[/bold] [green]{ns}[/green]")
    console.print(f"[bold]Begründung:[/bold]\n{reason}\n")
    if alternatives:
        console.print("[bold blue]Alternativen:[/bold blue]")
        for alt in alternatives:
            console.print(f"- [cyan]{alt.get('namespace','')}[/cyan]: {alt.get('reason','')}")
    console.print("="*60 + "\n")

def agent_analyse_references(obj_dict, references):
    """Analysiere jede Referenz einzeln mit LLM und sammle die Namespace-Empfehlungen."""
    ref_contexts = []
    for ref_name in references:
        ref_found = find_object_file(obj_dict, ref_name)
        if not ref_found:
            continue
        ref_type, ref_obj_name, ref_info = ref_found
        al_content = read_file_content(ref_info["filepath"])
        # Kurzes Prompt für Referenzanalyse
        prompt = (
            f"Analysiere das folgende AL-Objekt und gib den Namespace als JSON zurück.\n"
            f"Objekttyp: {ref_type}\n"
            f"Objektname: {ref_obj_name}\n"
            f"AL-Code:\n{al_content}\n"
            'Gib das Ergebnis als JSON im Format: {"namespace": "..."}'
        )
        # Hinweis: invoke statt __call__ verwenden (Deprecation)
        llm = AzureChatOpenAI(
            openai_api_key=OPENAI_API_KEY,
            azure_endpoint=OPENAI_API_BASE,
            openai_api_version=OPENAI_API_VERSION,
            deployment_name=OPENAI_DEPLOYMENT,
            temperature=0.3,
            max_tokens=200,
        )
        messages = [
            SystemMessage(content="Du bist ein erfahrener AL-Entwickler und Namespace-Experte."),
            HumanMessage(content=prompt)
        ]
        try:
            response = llm.invoke(messages)
            import re, json
            match = re.search(r'\{.*\}', response.content, re.DOTALL)
            ns = ""
            if match:
                try:
                    data = json.loads(match.group(0))
                    ns = data.get("namespace", "")
                except Exception:
                    ns = ""
            ref_contexts.append({
                "object_type": ref_type,
                "object_name": ref_obj_name,
                "namespace": ns,
                "directory": ref_info.get("directory"),
                "filepath": ref_info.get("filepath"),
            })
        except Exception as ex:
            # Fehler ignorieren, Referenz wird nicht als Kontext genutzt
            continue
    return ref_contexts

def main():
    print("Starte parallele Indexierung...")
    obj_dict = parallel_scan_al_files(SEARCH_ROOTS)
    print(f"{len(obj_dict)} Objekte gefunden.")

    # Objekt suchen
    found = find_object_file(obj_dict, OBJECT_NAME_TO_REVIEW)
    if not found:
        print(f"Objekt '{OBJECT_NAME_TO_REVIEW}' nicht gefunden.")
        return
    object_type, obj_name, info = found
    al_content = read_file_content(info["filepath"])

    # 1. KI-Analyse: Nur mit Hauptobjekt
    print(f"Analysiere Objekt: {object_type} {obj_name}")
    result = langchain_analyse(object_type, obj_name, al_content, context_objects=[])

    # 2. Referenzen extrahieren
    references = extract_references_from_al(al_content)
    context_objs = []
    for ref_name in references:
        ref_found = find_object_file(obj_dict, ref_name)
        if ref_found:
            ref_type, ref_obj_name, ref_info = ref_found
            context_objs.append({
                "object_type": ref_type,
                "object_name": ref_obj_name,
                "namespace": ref_info.get("namespace"),
                "directory": ref_info.get("directory"),
                "filepath": ref_info.get("filepath"),
            })

    # Agent-Variante: Für jede Referenz eine eigene LLM-Analyse (nur bei vielen oder komplexen Referenzen sinnvoll)
    if context_objs:
        print(f"Starte Agent-Analyse für {len(context_objs)} Referenzen...")
        agent_ref_contexts = agent_analyse_references(obj_dict, references)
        # Kombiniere Kontextobjekte aus Index und Agent-Analysen
        # (Agent-Kontext hat ggf. bessere Namespace-Infos)
        for agent_ctx in agent_ref_contexts:
            for ctx in context_objs:
                if ctx["object_name"].lower() == agent_ctx["object_name"].lower():
                    ctx["namespace"] = agent_ctx["namespace"]
        result = langchain_analyse(object_type, obj_name, al_content, context_objs)

    print_namespace_result(result)

if __name__ == "__main__":
    main()
