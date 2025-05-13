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
    allowed_namespaces = [
          # Microsoft Standard
             "Warehouse", 
             "Utilities", 
             "System", 
             "Service", 
             "Sales", 
             "RoleCenters", 
             "Purchases", 
             "Projects", 
             "Profile",
                     "Pricing", "OtherCapabilities", "Manufacturing", "Invoicing", "Inventory", "Integration", "HumanResources",
    "Foundation", "FixedAssets", "Finance", "eServices", "CRM", "CostAccounting", "CashFlow", "Bank", "Assembly", "API",
    # KUMAVISION Base (KBA)
    "UDI", "Call", "LIF", "OrderQuote", "InventorySummary", "Common",
    # HC/MTC-spezifisch
    "EDocuments", "ECE", "MDR"
    ]
    prompt = (
        "Du bist ein Experte für Microsoft Dynamics 365 Business Central AL-Entwicklung und die Vergabe von Namespaces.\n"
        "Analysiere das folgende AL-Objekt und schlage einen passenden Namespace vor. "
        "Beziehe dich dabei auf die Namenskonventionen der Microsoft Base Application. "
        "Wenn im Standard (Base Application) für ein Objekt oder dessen Funktionalität bereits ein Namespace wie z.B. 'ApplicationAreas', 'System', 'Sales', etc. verwendet wird, "
        "sollen die HC- und MTC-Objekte möglichst denselben Namespace verwenden. "
        "Die Entscheidung für den Namespace soll sich vorrangig an den Objekten der Base Application orientieren.\n"
        "Falls ein anderer Namespace sinnvoller ist, begründe dies nachvollziehbar.\n"
        "Die Begründung (\"reason\") und alle Alternativen im JSON-Output müssen ausschließlich auf DEUTSCH formuliert sein.\n"
        "Du darfst ausschließlich einen Namespace aus folgender Liste verwenden (keinen anderen):\n"
        f"{', '.join(allowed_namespaces)}\n"
        "Falls keiner dieser Namespaces fachlich passt, wähle 'Custom' und begründe dies ausführlich.\n"
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
