import csv
import os
import sys
import openai
import lancedb
import pyarrow as pa
import numpy as np
import hashlib

# -------------------- KONSTANTEN --------------------
OBJECT_NAME_TO_REVIEW = "KVSMEDApplicationAreaMgt"  # <--- Setze hier den gewünschten Objektnamen
HC_ROOT = "/home/kosta/Repos/DevOps/Product_MED/Product_MED_AL/app/"
MTC_ROOT = "/home/kosta/Repos/DevOps/Product_MED_Tech365/Product_MED_Tech/app/"

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

def build_rag_prompt(object_name, object_rows, context_objects=None):
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
        "Deine Aufgabe:\n"
        "- Analysiere, ob und wie das Objekt oder ähnliche Objekte in der Base Application einem bestimmten Namespace zugeordnet sind.\n"
        "- Schlage einen passenden Namespace aus der Liste der erlaubten Namespaces vor (bevorzugt den Namespace der Base Application, falls vorhanden).\n"
        "- Begründe deine Entscheidung ausführlich mit neuen Argumenten und in deinen eigenen Worten in deutscher Sprache.\n"
        "- Schlage, falls sinnvoll, alternative Namespaces vor und begründe diese Alternativen in deutscher Sprache.\n"
        "Gib das Ergebnis als JSON im folgenden Format zurück (ALLE Begründungen auf DEUTSCH!):\n"
        '{"namespace": "...", "reason": "...", "alternatives": [{"namespace": "...", "reason": "..."}]}'
        "Deine Empfehlung:"
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

def main():
    if len(sys.argv) >= 2:
        object_name = sys.argv[1]
    else:
        object_name = OBJECT_NAME_TO_REVIEW
        print(f"Kein Objektname als Argument übergeben, verwende Konstante: '{OBJECT_NAME_TO_REVIEW}'")

    # Prüfe, ob Objekt bereits in CSV existiert
    if exists_in_csv(CSV_PATH, object_name):
        print(f"Objekt '{object_name}' ist bereits in der CSV. Ausgabe der bisherigen Analyse:")
        rows = load_csv_data(CSV_PATH)
        object_rows = find_object_rows(rows, object_name)
        for row in object_rows:
            print_namespace_result(row)
        return

    # Objekt ist NEU: Suche Datei in HC und MTC
    filepath = find_al_file(object_name, HC_ROOT) or find_al_file(object_name, MTC_ROOT)
    if not filepath:
        print(f"Keine .al-Datei für Objekt '{object_name}' gefunden.")
        sys.exit(1)
    directory = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    object_type, obj_name = extract_object_info_from_file(filepath)
    if not object_type or not obj_name:
        print(f"Konnte Objektinformationen aus Datei '{filepath}' nicht extrahieren.")
        sys.exit(1)

    # --- RAG: Kontextobjekte aus LanceDB holen ---
    context_objects = retrieve_context(object_type, obj_name, top_k=3)

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
        context_objects=context_objects
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
    append_to_csv(CSV_PATH, row, fieldnames)
    print_namespace_result(row)

if __name__ == "__main__":
    main()
