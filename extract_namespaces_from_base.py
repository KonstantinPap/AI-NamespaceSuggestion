import os
import re

BASE_ROOT = r"C:/Repos/Github/StefanMaron/MSDyn365BC.Code.History/BaseApp/Source/Base Application/"

NAMESPACE_PATTERN = re.compile(r'(?:Namespace\s*=\s*"([\w\d_.]+)"|namespace\s+([\w\d_.]+)\s*;)', re.IGNORECASE)

def extract_namespaces_from_file(filepath):
    namespaces = set()
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            m = NAMESPACE_PATTERN.search(line)
            if m:
                ns = m.group(1) or m.group(2)
                if ns:
                    namespaces.add(ns)
    return namespaces

def main():
    all_namespaces = set()
    for dirpath, _, filenames in os.walk(BASE_ROOT):
        for f in filenames:
            if f.lower().endswith(".al"):
                fp = os.path.join(dirpath, f)
                try:
                    all_namespaces.update(extract_namespaces_from_file(fp))
                except Exception:
                    continue
    sorted_namespaces = sorted(all_namespaces)
    print("# Kopiere das Ergebnis in allowed_namespaces_with_desc")
    print("allowed_namespaces_with_desc = [")
    for ns in sorted_namespaces:
        print(f'    ("{ns}", ""),')
    print("]")

if __name__ == "__main__":
    main()
