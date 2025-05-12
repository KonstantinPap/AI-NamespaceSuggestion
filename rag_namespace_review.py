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
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4-turbo")

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

def build_rag_prompt(object_name, object_rows):
    context = ""
    for idx, row in enumerate(object_rows, 1):
        context += (
            f"\n--- Kontext {idx} ---\n"
            f"Objekttyp: {row.get('ObjectType','')}\n"
            f"ObjectName HC: {row.get('ObjectName HC','')}\n"
            f"ObjectName MTC: {row.get('ObjectName MTC','')}\n"
            f"Namespace Vorschlag: {row.get('Namespace Vorschlag','')}\n"
            f"Namespace Begründung: {row.get('Namespace Begründung','')}\n"
            f"Alternative Namespace Vorschlag: {row.get('Alternative Namespace Vorschlag','')}\n"
            f"Alternative Namespace Begründung: {row.get('Alternative Namespace Begründung','')}\n"
            f"Analyse: {row.get('Analyse','')}\n"
        )
    prompt = (
        f"Du bist ein Experte für Microsoft Dynamics 365 Business Central AL-Entwicklung und Namespace-Vergabe.\n"
        f"Für das Objekt '{object_name}' sollen alle bisherigen Namespace-Vorschläge und Analysen kritisch geprüft werden.\n"
        f"Nutze alle folgenden Kontextinformationen, um eine fundierte, neue Empfehlung für den Namespace zu geben.\n"
        f"Wenn du einen anderen Namespace als bisher vorgeschlagen für sinnvoller hältst, begründe dies ausführlich.\n"
        f"Gib das Ergebnis als JSON im Format:\n"
        f'{{"namespace": "...", "reason": "...", "alternatives": [{{"namespace": "...", "reason": "..."}}]}}\n'
        f"{context}\n"
        f"Deine Empfehlung:"
    )
    return prompt

def query_azure_openai(prompt):
    client = openai.AzureOpenAI(
        api_key=AZURE_OPENAI_KEY,
        api_version="2025-04-14",
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
    )
    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": "Du bist ein erfahrener AL-Entwickler und Namespace-Experte."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
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

    def pretty(label, value, color=None):
        # ANSI Farben: gelb=33, grün=32, rot=31, blau=34, fett=1
        if color:
            return f"\033[{color}m{label}: {value}\033[0m"
        return f"{label}: {value}"

    print("\n" + "="*60)
    print(" NAMESPACE-ANALYSE-ERGEBNIS ".center(60, "="))
    print("="*60)
    for k in [
        "ObjectType", "ObjectName HC", "ObjectName MTC", "Dateiname",
        "Namespace Vorschlag", "Namespace Begründung",
        "Alternative Namespace Vorschlag", "Alternative Namespace Begründung"
    ]:
        v = row.get(k, "")
        if k.startswith("Namespace Vorschlag"):
            print(pretty(k, v, color="32;1"))  # grün fett
        elif k.startswith("Alternative Namespace"):
            print(pretty(k, v, color="34"))   # blau
        else:
            print(pretty(k, v, color="1"))    # fett

    # Zeige Unterschiede zum bisherigen Vorschlag (falls vorhanden)
    if previous_row:
        print("\n" + "-"*60)
        print(" Unterschied zum bisherigen Vorschlag ".center(60, "-"))
        print("-"*60)
        for k in ["Namespace Vorschlag", "Namespace Begründung"]:
            old = previous_row.get(k, "")
            new = row.get(k, "")
            if old != new:
                print(pretty(f"{k} (alt)", old, color="31"))  # rot
                print(pretty(f"{k} (neu)", new, color="32"))  # grün
            else:
                print(pretty(f"{k}", new, color="32"))
        print("-"*60)

    # Analyse-Text ggf. übersetzen, falls englisch
    analyse = row.get("Analyse", "")
    if analyse.strip():
        print("\n" + "-"*60)
        print(" Analyse ".center(60, "-"))
        print("-"*60)
        # Prüfe, ob Text englisch ist (sehr grob)
        if analyse and any(w in analyse.lower() for w in ["namespace", "reason", "suggest", "alternatives"]):
            try:
                import requests
                resp = requests.post(
                    "https://api-free.deepl.com/v2/translate",
                    data={
                        "auth_key": os.environ.get("DEEPL_API_KEY", ""),
                        "text": analyse,
                        "target_lang": "DE"
                    }
                )
                if resp.ok and "text" in resp.json()["translations"][0]:
                    analyse_de = resp.json()["translations"][0]["text"]
                    print(fill(analyse_de, width=100))
                else:
                    print(fill(analyse, width=100))
            except Exception:
                print(fill(analyse, width=100))
        else:
            print(fill(analyse, width=100))
        print("-"*60)
    print("="*60 + "\n")

def file_hash(filepath):
    """Berechnet den SHA256-Hash einer Datei."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
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
        # Prüfe, ob sich der Hash geändert hat
        filepath = find_al_file(object_name, HC_ROOT) or find_al_file(object_name, MTC_ROOT)
        if not filepath:
            print(f"Objekt '{object_name}' ist bereits in der CSV. Ausgabe der bisherigen Analyse:")
            rows = load_csv_data(CSV_PATH)
            object_rows = find_object_rows(rows, object_name)
            for row in object_rows:
                print_namespace_result(row)
            return
        current_hash = file_hash(filepath)
        csv_hash = get_csv_hash(CSV_PATH, object_name)
        if current_hash == csv_hash:
            print(f"Objekt '{object_name}' ist bereits in der CSV und unverändert. Ausgabe der bisherigen Analyse:")
            rows = load_csv_data(CSV_PATH)
            object_rows = find_object_rows(rows, object_name)
            for row in object_rows:
                print_namespace_result(row)
            return
        else:
            print(f"Objekt '{object_name}' wurde geändert. RAG-Analyse und CSV/Vektor-Update wird durchgeführt.")
            # Merke bisherigen Vorschlag für Vergleich
            rows = load_csv_data(CSV_PATH)
            previous_row = next((r for r in rows if r.get("ObjectName HC", "").strip().lower() == object_name.lower()
                                 or r.get("ObjectName MTC", "").strip().lower() == object_name.lower()), None)
    else:
        previous_row = None

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

    # Namespace-Analyse durchführen (Prompt bauen und Azure OpenAI abfragen)
    prompt = build_rag_prompt(obj_name, [{
        "ObjectType": object_type,
        "ObjectName HC": obj_name,
        "ObjectName MTC": "",
        "Namespace Vorschlag": "",
        "Namespace Begründung": "",
        "Alternative Namespace Vorschlag": "",
        "Alternative Namespace Begründung": "",
        "Analyse": ""
    }])
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
            alt_ns = "; ".join([a.get("namespace", "") for a in data.get("alternatives", [])])
            alt_reason = "; ".join([a.get("reason", "") for a in data.get("alternatives", [])])
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
    # Wenn schon in CSV, dann Zeile ersetzen, sonst anhängen
    if exists_in_csv(CSV_PATH, obj_name):
        update_csv_row(CSV_PATH, obj_name, row, fieldnames)
    else:
        append_to_csv(CSV_PATH, row, fieldnames)
    print_namespace_result(row, previous_row)

if __name__ == "__main__":
    main()
