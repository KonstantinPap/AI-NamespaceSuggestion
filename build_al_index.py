import os
import re
import json
from pathlib import Path
from typing import Dict, Tuple, Optional, List
import threading

SEARCH_ROOTS = [
    "C:/Repos/DevOps/HC-Work/Product_MED/Product_MED_AL/app/",
    "C:/Repos/DevOps/MTC-Work/Product_MED_Tech365/Product_MED_Tech/app/",
    # "C:/Repos/Github/StefanMaron/MSDyn365BC.Code.History/", 
    "C:/Repos/Github/StefanMaron/MSDyn365BC.Code.History/BaseApp/Source/Base Application/",
    # namespace System.Privacy;    
    "C:/Repos/DevOps/HC-Work/Product_KBA/Product_KBA_BC_AL/app/",
]

OBJECT_PATTERN = re.compile(
    r'^(table|page|codeunit|report|xmlport|query|enum|interface|controladdin|pageextension|tableextension|enumextension|profile|dotnet|entitlement|permissionset|permissionsetextension|reportextension|enumvalue|entitlementset|entitlementsetextension)\s+(\d+)?\s*"?([\w\d_]+)"?',
    re.IGNORECASE
)
NAMESPACE_PATTERN = re.compile(
    r'(?:Namespace\s*=\s*"([\w\d_.]+)"|namespace\s+([\w\d_.]+)\s*;)', re.IGNORECASE
)

def scan_al_file(filepath: str) -> Optional[Tuple[Tuple[str, str], Dict]]:
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
                    namespace = n.group(1) or n.group(2)
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
    al_files = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for f in filenames:
                if f.lower().endswith(".al"):
                    al_files.append(os.path.join(dirpath, f))
    result = {}
    total = len(al_files)
    print(f"Scanne {total} AL-Dateien ...", end="", flush=True)
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
            f.result()
            if idx % 500 == 0 or idx == total:
                print(".", end="", flush=True)
    print(" fertig.")
    return result

AL_INDEX_JSON = "al_index.json"

def normalize_path(path):
    return path.replace("\\", "/")

def main():
    print("Baue AL-Index ...")
    obj_dict = parallel_scan_al_files(SEARCH_ROOTS)
    # Tupel-Keys in Strings serialisieren und Pfade vereinheitlichen
    serializable_dict = {}
    for k, v in obj_dict.items():
        v["directory"] = normalize_path(v["directory"])
        v["filepath"] = normalize_path(v["filepath"])
        serializable_dict["|".join(k)] = v
    with open(AL_INDEX_JSON, "w", encoding="utf-8") as f:
        json.dump(serializable_dict, f, ensure_ascii=False, indent=2)
    print(f"Index gespeichert in {AL_INDEX_JSON} ({len(obj_dict)} Objekte).")

if __name__ == "__main__":
    main()
