import csv
import os
import sys
import openai
import lancedb
import pyarrow as pa
import numpy as np
import hashlib

# -------------------- KONSTANTEN --------------------
OBJECT_NAME_TO_REVIEW = "KVSMEDCLLCMBGeneralMgtSub"  # <--- Setze hier den gewünschten Objektnamen
HC_ROOT = "C:/Repos/DevOps/HC-Work/Product_MED/Product_MED_AL/app/"
MTC_ROOT = "C:/Repos/DevOps/MTC-Work/Product_MED_Tech365/Product_MED_Tech/app/"

CSV_PATH = "namespace_suggestions.csv"
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_KEY = os.environ.get("AZURE_OPENAI_KEY")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

LANCEDB_PATH = "./lancedb"
LANCEDB_TABLE = "namespace_vectors"
# ----------------------------------------------------

def load_csv_data(csv_path):
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def find_object_rows(rows, object_name):
    object_name = object_name.strip().lower()
    matches = []
    for row in rows:
        if (row.get("ObjectName HC", "").strip().lower() == object_name or
            row.get("ObjectName MTC", "").strip().lower() == object_name):
            matches.append(row)
    return matches

def retrieve_context(object_type, object_name, top_k=3):
    """Hole die ähnlichsten Objekte aus LanceDB als Kontext für RAG."""
    db = lancedb.connect(LANCEDB_PATH)
    table = db.open_table(LANCEDB_TABLE)
    # Embedding: Dummy-Embedding (hier ggf. echtes Embedding einfügen)
    # Für echtes Embedding: OpenAI Embedding API oder eigene Methode
    # Hier: Nutze object_type + object_name als Text
    import numpy as np
    query_text = f"{object_type} {object_name}"
    # Dummy: zeros, in Produktion: echtes Embedding
    query_emb = np.zeros(1024, dtype=np.float32).tolist()
    try:
        results = table.search(query_emb).limit(top_k).to_list()
        return results
    except Exception:
        return []

def build_rag_prompt(object_name, object_rows, al_content, context_objects=None, ref_contexts=None):
    context = ""
    for idx, row in enumerate(object_rows, 1):
        context += (
            f"\n--- Kontext {idx} ---\n"
            f"Objekttyp: {row.get('ObjectType','')}\n"
            f"ObjectName HC: {row.get('ObjectName HC','')}\n"
            f"ObjectName MTC: {row.get('ObjectName MTC','')}\n"
        )
    # Kontextobjekte aus LanceDB (RAG)
    rag_context = ""
    if context_objects:
        for i, ctx in enumerate(context_objects, 1):
            rag_context += (
                f"\n--- Ähnliches Objekt {i} ---\n"
                f"Objekttyp: {ctx.get('object_type','')}\n"
                f"Objektname: {ctx.get('object_name','')}\n"
                f"Namespace: {ctx.get('namespace','')}\n"
                f"Dateiname: {ctx.get('filename','')}\n"
                f"Verzeichnis: {ctx.get('directory','')}\n"
            )
    prompt = (
        "Du bist ein Experte für Microsoft Dynamics 365 Business Central AL-Entwicklung und die Vergabe von Namespaces.\n"
        "Die Microsoft Base Application bildet die Grundlage für alle weiteren Lösungen. "
        "Wenn im Standard (Base Application) für ein Objekt oder dessen Funktionalität bereits ein Namespace wie z.B. 'ApplicationAreas', 'System', 'Sales', etc. verwendet wird, "
        "dann sollen die HC- und MTC-Objekte diesem Vorbild folgen und möglichst denselben Namespace verwenden. "
        "Die Entscheidung für den Namespace soll sich daher vorrangig an den Objekten der Base Application orientieren.\n"
        "Nur wenn es einen triftigen fachlichen Grund gibt, darf davon abgewichen werden – dieser Grund muss dann klar und nachvollziehbar begründet werden.\n"
        "WICHTIG: Dies ist eine komplett NEUE Analyse. Ignoriere alle früheren Entscheidungen zu diesem Objekt.\n"
        "Formuliere eine NEUE, EIGENSTÄNDIGE Begründung ohne Bezug auf frühere Analysen oder Formulierungen.\n"
        "Deine Begründung soll sich inhaltlich und stilistisch deutlich von früheren Begründungen unterscheiden.\n"
        "\n"
        "ACHTUNG: Die Begründung (\"reason\") und alle Alternativen im JSON-Output müssen AUSSCHLIESSLICH auf DEUTSCH formuliert sein.\n"
        "\n"
        f"Hier sind die grundlegenden Objektinformationen:\n{context}\n"
        f"Hier sind ähnliche Objekte aus der Base Application oder KBA (Kontext für RAG):\n{rag_context}\n"
        f"\nHier ist der vollständige AL-Code des Objekts:\n\n{al_content}\n"
        "Deine Aufgabe:\n"
        "- Analysiere, ob und wie das Objekt oder ähnliche Objekte in der Base Application einem bestimmten Namespace zugeordnet sind.\n"
        "- Schlage einen passenden Namespace aus der Liste der erlaubten Namespaces vor (bevorzugt den Namespace der Base Application, falls vorhanden).\n"
        "- Begründe deine Entscheidung ausführlich mit neuen Argumenten und in deinen eigenen Worten in deutscher Sprache.\n"
        "- Schlage, falls sinnvoll, alternative Namespaces vor und begründe diese Alternativen in deutscher Sprache.\n"
        "Gib das Ergebnis als JSON im folgenden Format zurück (ALLE Begründungen auf DEUTSCH!):\n"
        '{"namespace": "...", "reason": "...", "alternatives": [{"namespace": "...", "reason": "..."}]}'
        "Deine Empfehlung:"
    )
    if ref_contexts:
        prompt += "\nKontext zu referenzierten Objekten:\n"
        for i, ctx in enumerate(ref_contexts, 1):
            prompt += (
                f"\n--- Referenziertes Objekt {i} ---\n"
                f"Objekttyp: {ctx.get('object_type','')}\n"
                f"Objektname: {ctx.get('object_name','')}\n"
                f"Namespace: {ctx.get('namespace','')}\n"
                f"Dateiname: {ctx.get('filename','')}\n"
                f"Verzeichnis: {ctx.get('directory','')}\n"
            )
    return prompt

def query_azure_openai(prompt):
    client = openai.AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        api_version="2025-04-14",
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
    )
    system_message = "Du bist ein erfahrener AL-Entwickler und Namespace-Experte. Bei jeder Anfrage lieferst du eine NEUE, EIGENSTÄNDIGE Analyse mit FRISCHEN Begründungen und Formulierungen."
    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7,
        max_tokens=800
    )
    return response.choices[0].message.content

def exists_in_csv(csv_path, object_name):
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("ObjectName HC", "").strip().lower() == object_name.lower() or
                row.get("ObjectName MTC", "").strip().lower() == object_name.lower()):
                return True
    return False

def find_al_file(object_name, root):
    # Suche nach .al-Dateien, die den Objekt-Namen enthalten
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.lower().endswith(".al") and object_name.lower() in f.lower():
                return os.path.join(dirpath, f)
    return None

def extract_object_info_from_file(filepath):
    import re
    OBJECT_PATTERN = re.compile(r'^(table|page|codeunit|report|xmlport|query|enum|interface|controladdin|pageextension|tableextension|enumextension|profile|dotnet|entitlement|permissionset|permissionsetextension|reportextension|enumvalue|entitlementset|entitlementsetextension)\s+(\d+)?\s*"?([\w\d_]+)"?', re.IGNORECASE)
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            match = OBJECT_PATTERN.match(line.strip())
            if match:
                obj_type = match.group(1)
                obj_name = match.group(3)
                return obj_type, obj_name
    return None, None

def add_to_lancedb(object_id, object_type, object_name, namespace, filename, directory):
    db = lancedb.connect(LANCEDB_PATH)
    table_schema = pa.schema([
        ("object_id", pa.string()),
        ("object_type", pa.string()),
        ("object_name", pa.string()),
        ("namespace", pa.string()),
        ("filename", pa.string()),
        ("directory", pa.string()),
        ("vector", pa.list_(pa.float32()))
    ])
    try:
        table = db.open_table(LANCEDB_TABLE)
    except Exception:
        table = db.create_table(LANCEDB_TABLE, schema=table_schema)
    # Dummy-Embedding (hier ggf. echtes Embedding einfügen)
    vector = np.zeros(1024, dtype=np.float32).tolist()
    table.add([
        {
            "object_id": object_id,
            "object_type": object_type,
            "object_name": object_name,
            "namespace": namespace,
            "filename": filename,
            "directory": directory,
            "vector": vector
        }
    ])

def append_to_csv(csv_path, row, fieldnames):
    write_header = not os.path.exists(csv_path) or os.stat(csv_path).st_size == 0
    with open(csv_path, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

def update_csv_row(csv_path, object_name, new_row, fieldnames):
    """Aktualisiert eine Zeile in der CSV (wenn Hash sich geändert hat)."""
    rows = []
    updated = False
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("ObjectName HC", "").strip().lower() == object_name.lower() or
                row.get("ObjectName MTC", "").strip().lower() == object_name.lower()):
                rows.append(new_row)
                updated = True
            else:
                rows.append(row)
    if updated:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    return updated

def print_namespace_result(row, previous_row=None):
    import difflib
    from textwrap import fill
    import re
    from rich.console import Console
    from rich.markdown import Markdown

    console = Console(width=120)

    def pretty(label, value, color=None):
        # ANSI Farben: gelb=33, grün=32, rot=31, blau=34, fett=1
        if color:
            return f"\033[{color}m{label}: {value}\033[0m"
        return f"{label}: {value}"

    # Entferne add_linebreaks und markdown_to_console, nutze stattdessen rich

    # Erstelle eine Kopie des Rows mit übersetzten Werten
    translated_row = dict(row)
    
    print("\n" + "="*60)
    print(" NAMESPACE-ANALYSE-ERGEBNIS ".center(60, "="))
    print("="*60)
    
    # Zeige übersetzte Werte an
    for k in [
        "ObjectType", "ObjectName HC", "ObjectName MTC", "Dateiname",
        "Namespace Vorschlag", "Namespace Begründung",
        "Alternative Namespace Vorschlag", "Alternative Namespace Begründung"
    ]:
        v = translated_row.get(k, "")
        if k.startswith("Namespace Vorschlag"):
            print(pretty(k, v, color="32;1"))
        elif k.startswith("Alternative Namespace"):
            print(pretty(k, v, color="34"))
        else:
            print(pretty(k, v, color="1"))

    # Zeige ehemaligen Lauf, falls vorhanden (nur einen, kein Loop)
    previous = None
    try:
        rows = load_csv_data(CSV_PATH)
        object_name = row.get("ObjectName HC", "") or row.get("ObjectName MTC", "")
        for r in rows:
            if (r.get("ObjectName HC", "").strip().lower() == object_name.strip().lower() or
                r.get("ObjectName MTC", "").strip().lower() == object_name.strip().lower()):
                previous = r
                break
    except Exception:
        pass

    if previous:
        print("\n" + "-"*60)
        print(" Ergebnisse des bisherigen Durchlaufs ".center(60, "-"))
        print("-"*60)
        print(pretty("Ehemaliger Namespace Vorschlag", previous.get("Namespace Vorschlag", ""), color="33"))
        print(pretty("Ehemalige Namespace Begründung", previous.get("Namespace Begründung", ""), color="33"))
        print(pretty("Ehemalige Namespace Alternativen", previous.get("Alternative Namespace Vorschlag", ""), color="33"))
        print(pretty("Ehemalige Alternativen Begründung", previous.get("Alternative Namespace Begründung", ""), color="33"))
        print("-"*60)

    # Zeige Unterschiede zum bisherigen Vorschlag (falls vorhanden)
    if previous_row:
        print("\n" + "-"*60)
        print(" Unterschied zum bisherigen Vorschlag ".center(60, "-"))
        print("-"*60)
        for k, label in [
            ("Namespace Vorschlag", "Namespace Vorschlag"),
            ("Namespace Begründung", "Namespace Begründung"),
            ("Alternative Namespace Vorschlag", "Alternative Namespace Vorschlag"),
            ("Alternative Namespace Begründung", "Alternative Namespace Begründung"),
        ]:
            old = previous_row.get(k, "")
            new = translated_row.get(k, "")
            if old != new:
                print(pretty(f"{label} (neu)", new, color="32"))
        print("-"*60)

    # Analyse-Text ggf. übersetzen, falls englisch
    analyse = row.get("Analyse", "")
    if analyse.strip():
        print("\n" + "-"*60)
        print(" Analyse ".center(60, "-"))
        print("-"*60)
        # Füge Zeilenumbrüche für Markdown-Listen und Überschriften ein
        def add_md_linebreaks(md: str) -> str:
            import re
            # Vor ### Überschriften
            md = re.sub(r'(?<!\n)\s*(###)', r'\n\1', md)
            # Vor Listenpunkten mit vier Leerzeichen und Bindestrich
            md = re.sub(r'(?<!\n)(\n?)( {4}-)', r'\n\2', md)
            # Vor nummerierten Listen mit zwei Leerzeichen und Ziffer und Punkt
            md = re.sub(r'(?<!\n)( {2}\d+\.)', r'\n\1', md)
            return md

        analyse_md = add_md_linebreaks(analyse)
        console.print(Markdown(analyse_md, hyperlinks=True), soft_wrap=False)
        print("-"*60)
    print("="*60 + "\n")

def file_hash(filepath):
    """Berechnet den SHA256-Hash einer Datei."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break;
            h.update(chunk)
    return h.hexdigest()

def get_csv_hash(csv_path, object_name):
    """Liest den Hash aus der CSV für das Objekt, falls vorhanden."""
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("ObjectName HC", "").strip().lower() == object_name.lower() or
                row.get("ObjectName MTC", "").strip().lower() == object_name.lower()):
                return row.get("Hash", "")
    return ""

def extract_explicit_namespace_from_file(filepath):
    """Versucht, einen expliziten Namespace aus der Datei zu extrahieren (z.B. als Attribut oder Kommentar)."""
    import re
    NAMESPACE_PATTERN = re.compile(r'Namespace\s*=\s*"([\w\d_]+)"', re.IGNORECASE)
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            match = NAMESPACE_PATTERN.search(line)
            if match:
                return match.group(1)
    return None

def extract_extension_base_object(filepath):
    """Extrahiert bei Extension-Objekten die Basisklasse (z.B. extends "Customer List")."""
    import re
    EXTENSION_PATTERN = re.compile(r'(pageextension|tableextension|enumextension|reportextension)\s+\d*\s*"?([\w\d_]+)"?\s+extends\s+"?([\w\d_ ]+)"?', re.IGNORECASE)
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            match = EXTENSION_PATTERN.match(line.strip())
            if match:
                return match.group(1), match.group(3)
    return None, None

def get_namespace_from_base_object(base_object_name, lancedb_path=LANCEDB_PATH, lancedb_table=LANCEDB_TABLE):
    """Sucht den Namespace der Basisklasse via RAG (LanceDB)."""
    db = lancedb.connect(lancedb_path)
    table = db.open_table(lancedb_table)
    # Dummy-Embedding für Suche, in Produktion: echtes Embedding
    query_emb = np.zeros(1024, dtype=np.float32).tolist()
    try:
        results = table.search(query_emb).limit(10).to_list()
        # Suche nach exaktem Namen
        for obj in results:
            if obj.get("object_name", "").strip().lower() == base_object_name.strip().lower():
                return obj.get("namespace", None)
    except Exception:
        pass
    return None

def build_objectname_to_path_map(roots):
    """Erstellt ein Dictionary: object_name_lower -> filepath für alle .al-Dateien in den angegebenen Roots."""
    import re
    OBJECT_PATTERN = re.compile(r'^(table|page|codeunit|report|xmlport|query|enum|interface|controladdin|pageextension|tableextension|enumextension|profile|dotnet|entitlement|permissionset|permissionsetextension|reportextension|enumvalue|entitlementset|entitlementsetextension)\s+(\d+)?\s*"?([\w\d_]+)"?', re.IGNORECASE)
    name_to_path = {}
    total_files = 0
    parsed_files = 0
    
    print(f"Starte Indexierung der AL-Dateien in: {roots}")
    
    for root in roots:
        if not os.path.exists(root):
            print(f"WARNUNG: Pfad existiert nicht: {root}")
            continue
            
        print(f"Durchsuche Verzeichnis: {root}")
        for dirpath, _, filenames in os.walk(root):
            al_files = [f for f in filenames if f.lower().endswith(".al")]
            total_files += len(al_files)
            
            for f in al_files:
                filepath = os.path.join(dirpath, f)
                try:
                    with open(filepath, encoding="utf-8") as file:
                        # Suche nach der ersten Zeile mit Objektdefinition
                        content = file.read()
                        
                        # Suche nach dem Objektnamen in der Datei
                        for line in content.split('\n'):
                            line = line.strip()
                            if not line or line.startswith("//"):
                                continue
                            match = OBJECT_PATTERN.match(line)
                            if match:
                                obj_type = match.group(1)
                                obj_name = match.group(3)
                                
                                if obj_name:
                                    parsed_files += 1
                                    obj_name_lower = obj_name.lower()
                                    name_to_path[obj_name_lower] = filepath
                                    # Debug: Objekt gefunden
                                    # print(f"Objekt gefunden: {obj_name} in {filepath}")
                                break
                except Exception as e:
                    print(f"Fehler beim Lesen von {filepath}: {e}")
                    continue
    
    print(f"Indexierung abgeschlossen: {parsed_files} Objekte in {total_files} AL-Dateien gefunden.")
    return name_to_path

def find_al_file_by_partial_name(object_name, roots):
    """Fallback-Methode: Suche nach Dateinamen, die den Objektnamen enthalten."""
    print(f"Fallback-Suche für '{object_name}' nach Dateinamen...")
    for root in roots:
        if not os.path.exists(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if f.lower().endswith(".al") and object_name.lower() in f.lower():
                    filepath = os.path.join(dirpath, f)
                    print(f"Datei gefunden (Fallback): {filepath}")
                    return filepath
    return None

def extract_referenced_objects_from_al(content):
    """
    Extrahiert referenzierte Objekte (z.B. Table, Page, Codeunit) aus AL-Code.
    Gibt eine Liste von (object_type, object_name) zurück.
    """
    import re
    patterns = [
        re.compile(r'(Table|Page|Codeunit|Report|XmlPort|Query|Enum)\s*::\s*"?([\w\d_]+)"?', re.IGNORECASE),
        re.compile(r'(Table|Page|Codeunit|Report|XmlPort|Query|Enum)\s*\(\s*"?([\w\d_]+)"?\s*\)', re.IGNORECASE)
    ]
    refs = set()
    for pat in patterns:
        for m in pat.findall(content):
            refs.add((m[0].lower(), m[1]))
    return list(refs)

def retrieve_context_for_references(refs, top_k=2):
    """Holt Kontextobjekte aus LanceDB für alle referenzierten Objekte."""
    db = lancedb.connect(LANCEDB_PATH)
    table = db.open_table(LANCEDB_TABLE)
    context_objs = []
    for obj_type, obj_name in refs:
        query_emb = np.zeros(1024, dtype=np.float32).tolist()
        try:
            results = table.search(query_emb).limit(top_k).to_list()
            # Filter auf exakten Namen
            for obj in results:
                if obj.get("object_name", "").strip().lower() == obj_name.strip().lower():
                    context_objs.append(obj)
        except Exception:
            continue
    return context_objs

def main():
    if len(sys.argv) >= 2:
        object_name = sys.argv[1]
    else:
        object_name = OBJECT_NAME_TO_REVIEW
        print(f"Kein Objektname als Argument übergeben, verwende Konstante: '{OBJECT_NAME_TO_REVIEW}'")

    # Prüfe, ob Objekt bereits in CSV existiert
    already_in_csv = exists_in_csv(CSV_PATH, object_name)
    previous_row = None
    if already_in_csv:
        rows = load_csv_data(CSV_PATH)
        object_rows = find_object_rows(rows, object_name)
        if object_rows:
            previous_row = object_rows[0]  # Nur das erste Ergebnis für Vergleich

    # Baue ein Dictionary object_name_lower -> filepath
    objectname_to_path = build_objectname_to_path_map([HC_ROOT, MTC_ROOT])
    
    # Debug: Zeige alle gefundenen Objekte an (optional, für große Repositories auskommentieren)
    # print("\nGefundene Objekte:")
    # for name, path in objectname_to_path.items():
    #     print(f"  - {name}: {path}")
    
    print(f"\nSuche nach Objekt: '{object_name}' (Lowercase: '{object_name.lower()}')")
    
    # Suche nach Objektname im Dictionary (Case-insensitive)
    filepath = objectname_to_path.get(object_name.lower())
    
    # Fallback: Wenn keine Datei gefunden, versuche direkte Dateinamensuche
    if not filepath:
        print(f"Objekt '{object_name}' nicht im Index gefunden. Versuche Fallback-Suche...")
        filepath = find_al_file_by_partial_name(object_name, [HC_ROOT, MTC_ROOT])
        
    if not filepath:
        print(f"FEHLER: Keine .al-Datei für Objekt '{object_name}' gefunden.")
        print(f"Verfügbare Pfade überprüfen: HC_ROOT={HC_ROOT}, MTC_ROOT={MTC_ROOT}")
        print("Diese Pfade müssen auf Ihrer Maschine existieren und gültige AL-Dateien enthalten.")
        sys.exit(1)
        
    print(f"Datei gefunden: {filepath}")
    
    directory = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    object_type, obj_name = extract_object_info_from_file(filepath)
    if not object_type or not obj_name:
        print(f"Konnte Objektinformationen aus Datei '{filepath}' nicht extrahieren.")
        sys.exit(1)

    # --- Schritt 1: Expliziten Namespace erkennen ---
    explicit_ns = extract_explicit_namespace_from_file(filepath)
    if explicit_ns:
        print(f"Expliziter Namespace in Datei gefunden: {explicit_ns}")
        ns = explicit_ns
        ns_reason = "Namespace wurde explizit im AL-Code angegeben."
        alt_ns = ""
        alt_reason = ""
        answer = f'{{"namespace": "{ns}", "reason": "{ns_reason}", "alternatives": []}}'
        # In Vektor-DB aufnehmen
        add_to_lancedb(
            object_id=f"{object_type}|{obj_name}",
            object_type=object_type,
            object_name=obj_name,
            namespace=ns,
            filename=filename,
            directory=directory
        )
        # In CSV aufnehmen
        current_hash = file_hash(filepath)
        fieldnames = [
            "ObjectType",
            "ObjectName HC",
            "ObjectName MTC",
            "Namespace Vorschlag",
            "Namespace Begründung",
            "Alternative Namespace Vorschlag",
            "Alternative Namespace Begründung",
            "Dateiname",
            "Analyse",
            "Hash"
        ]
        row = {
            "ObjectType": object_type,
            "ObjectName HC": obj_name,
            "ObjectName MTC": "",
            "Namespace Vorschlag": ns,
            "Namespace Begründung": ns_reason,
            "Alternative Namespace Vorschlag": alt_ns,
            "Alternative Namespace Begründung": alt_reason,
            "Dateiname": filename,
            "Analyse": answer,
            "Hash": current_hash
        }
        print_namespace_result(row, previous_row)
        if not already_in_csv:
            append_to_csv(CSV_PATH, row, fieldnames)
        return

    # --- Schritt 2: Extension-Typ? Dann Basisklasse analysieren ---
    ext_type, base_object = extract_extension_base_object(filepath)
    if ext_type and base_object:
        base_ns = get_namespace_from_base_object(base_object)
        if base_ns:
            print(f"Namespace der Basisklasse '{base_object}' gefunden: {base_ns}")
            ns = base_ns
            ns_reason = f"Namespace wurde von der Basisklasse '{base_object}' übernommen."
            alt_ns = ""
            alt_reason = ""
            answer = f'{{"namespace": "{ns}", "reason": "{ns_reason}", "alternatives": []}}'
            # In Vektor-DB aufnehmen
            add_to_lancedb(
                object_id=f"{object_type}|{obj_name}",
                object_type=object_type,
                object_name=obj_name,
                namespace=ns,
                filename=filename,
                directory=directory
            )
            # In CSV aufnehmen
            current_hash = file_hash(filepath)
            fieldnames = [
                "ObjectType",
                "ObjectName HC",
                "ObjectName MTC",
                "Namespace Vorschlag",
                "Namespace Begründung",
                "Alternative Namespace Vorschlag",
                "Alternative Namespace Begründung",
                "Dateiname",
                "Analyse",
                "Hash"
            ]
            row = {
                "ObjectType": object_type,
                "ObjectName HC": obj_name,
                "ObjectName MTC": "",
                "Namespace Vorschlag": ns,
                "Namespace Begründung": ns_reason,
                "Alternative Namespace Vorschlag": alt_ns,
                "Alternative Namespace Begründung": alt_reason,
                "Dateiname": filename,
                "Analyse": answer,
                "Hash": current_hash
            }
            print_namespace_result(row, previous_row)
            if not already_in_csv:
                append_to_csv(CSV_PATH, row, fieldnames)
            return

    # --- RAG: Kontextobjekte aus LanceDB holen ---
    context_objects = retrieve_context(object_type, obj_name, top_k=3)

    # AL-Datei-Inhalt laden
    with open(filepath, encoding="utf-8") as f:
        al_content = f.read()
    # Referenzen extrahieren
    refs = extract_referenced_objects_from_al(al_content)
    # Kontext für Referenzen holen (nur BaseApp/KBA, ggf. filtern)
    ref_contexts = retrieve_context_for_references(refs)

    # Namespace-Analyse durchführen (Prompt bauen und Azure OpenAI abfragen)
    prompt = build_rag_prompt(
        obj_name,
        [{
            "ObjectType": object_type,
            "ObjectName HC": obj_name,
            "ObjectName MTC": "",
            "Namespace Vorschlag": "",
            "Namespace Begründung": "",
            "Alternative Namespace Vorschlag": "",
            "Alternative Namespace Begründung": "",
            "Analyse": ""
        }],
        al_content=al_content,
        context_objects=context_objects,
        ref_contexts=ref_contexts
    )
    
    print("Sende folgende Anfrage an Azure OpenAI...\n")
    print(prompt[:1000] + "\n...")  # Nur die ersten 1000 Zeichen anzeigen
    print("\n--- Antwort von Azure OpenAI ---\n")
    answer = query_azure_openai(prompt)
    print(answer)

    # Namespace aus Antwort extrahieren (vereinfachte Extraktion)
    import re, json
    match = re.search(r'\{.*\}', answer, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            ns = data.get("namespace", "")
            ns_reason = data.get("reason", "")
            
            # Übersetze sofort die Begründung mit AI statt DeepL
            ns_reason_de = ns_reason
            alt_reasons_de = []
            
            alt_ns = []
            alt_reason = []
            for a in data.get("alternatives", []):
                alt_ns.append(a.get("namespace", ""))
                translated = a.get("reason", "")
                alt_reasons_de.append(translated)
                alt_reason.append(translated)
            
            alt_ns = "; ".join(alt_ns)
            alt_reason = "; ".join(alt_reasons_de)
            
        except Exception:
            ns, ns_reason, alt_ns, alt_reason = "", "", "", ""
    else:
        ns, ns_reason, alt_ns, alt_reason = "", "", "", ""

    # In Vektor-DB aufnehmen
    add_to_lancedb(
        object_id=f"{object_type}|{obj_name}",
        object_type=object_type,
        object_name=obj_name,
        namespace=ns,
        filename=filename,
        directory=directory
    )

    # In CSV aufnehmen (nur wenn noch nicht vorhanden)
    current_hash = file_hash(filepath)
    fieldnames = [
        "ObjectType",
        "ObjectName HC",
        "ObjectName MTC",
        "Namespace Vorschlag",
        "Namespace Begründung",
        "Alternative Namespace Vorschlag",
        "Alternative Namespace Begründung",
        "Dateiname",
        "Analyse",
        "Hash"
    ]
    row = {
        "ObjectType": object_type,
        "ObjectName HC": obj_name,
        "ObjectName MTC": "",
        "Namespace Vorschlag": ns,
        "Namespace Begründung": ns_reason,
        "Alternative Namespace Vorschlag": alt_ns,
        "Alternative Namespace Begründung": alt_reason,
        "Dateiname": filename,
        "Analyse": answer,
        "Hash": current_hash
    }
    print_namespace_result(row, previous_row)
    if not already_in_csv:
        append_to_csv(CSV_PATH, row, fieldnames)

if __name__ == "__main__":
    main()
