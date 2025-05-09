"""
Vectorizer Module

This module handles the vectorization of data using LanceDB and a local Ollama instance.
"""

import lancedb
import pyarrow as pa  # Add this import for schema types
from typing import List, Dict
import requests
from tqdm import tqdm
import hashlib
import os
import time

# Constants
LANCEDB_PATH = "./lancedb"  # Lokaler Pfad zur LanceDB-Datenbank
OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_MODEL = "mxbai-embed-large:latest"
FILE_EXTENSION_FILTERS = [".al", ".json"]  # Erlaubte Dateiendungen

# Mehrere Root-Dirs als Liste
ROOT_DIRS = [
    r"C:/Repos/DevOps/HC-Work/Product_KBA/Product_KBA_BC_AL/app",
    r"C:/Repos/DevOps/HC-Work/Product_MED/Product_MED_AL/app",
    r"C:/Repos/DevOps/MTC-Work/Product_MED_Tech365/Product_MED_Tech/app",
    r"C:/Repos/Github/Microsoft/StefanMaron/MSDyn365BC.Code.History"
]

# Initialize LanceDB
def initialize_lancedb() -> lancedb.db.DBConnection:
    return lancedb.connect(LANCEDB_PATH)

def compute_content_hash(content: str) -> str:
    """
    Compute a SHA256 hash for the given content.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

# Vectorize Data
def vectorize_data(data: List[Dict[str, str]]) -> None:
    """
    Vectorize the provided data and store it in LanceDB.

    Args:
        data (List[Dict[str, str]]): List of objects to vectorize.
            Jeder Eintrag sollte zusätzlich 'filename' und 'directory' enthalten.
    """
    db = initialize_lancedb()
    
    # Use pyarrow.Schema instead of dict
    table_schema = pa.schema([
        ("id", pa.string()),
        ("content", pa.string()),
        ("embedding", pa.list_(pa.float32(), 1024)),  # mxbai-embed-large: 1024-dim
        ("filename", pa.string()),
        ("directory", pa.string()),
        ("content_hash", pa.string()),
    ])
    
    try:
        # Try to open existing table
        table = db.open_table("namespace_vectors")
    except:
        # Create new table if it doesn't exist
        table = db.create_table("namespace_vectors", schema=table_schema)

    start_time = time.time()
    with tqdm(total=len(data), desc="Vektorisieren", unit="Dokument") as pbar:
        for idx, item in enumerate(data, 1):
            content_hash = compute_content_hash(item["content"])
            # Use pandas to filter for existence
            df = table.to_pandas()
            exists = (
                (df["filename"] == item.get("filename", "")) &
                (df["content_hash"] == content_hash)
            ).any()
            if exists:
                pbar.update(1)
                continue

            # Use filter string for delete
            del_filter_str = f"filename = '{item.get('filename', '')}'"
            table.delete(where=del_filter_str)

            embedding = generate_embedding(item["content"])
            table.add([{
                "id": item["id"],
                "content": item["content"],
                "embedding": embedding,
                "filename": item.get("filename", ""),
                "directory": item.get("directory", ""),
                "content_hash": content_hash,
            }])

            elapsed = time.time() - start_time
            avg_time = elapsed / idx
            remaining = avg_time * (len(data) - idx)
            pbar.set_postfix_str(f"Ø {avg_time:.2f}s, Rest {remaining/60:.1f}min")
            pbar.update(1)

def generate_embedding(content: str) -> List[float]:
    """
    Generate an embedding for the given content using the local Ollama instance.

    Args:
        content (str): The content to embed.

    Returns:
        List[float]: The embedding vector.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": content
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        # Ollama gibt das Embedding unter "embedding" zurück
        embedding = result.get("embedding")
        if not embedding or not isinstance(embedding, list):
            raise ValueError("No embedding returned from Ollama.")
        return embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return [0.0] * 1024  # Fallback auf Nullvektor

def collect_files(root_dir: str, extensions: list) -> List[Dict[str, str]]:
    """
    Sammelt rekursiv alle Dateien mit den angegebenen Extensions ab root_dir.
    Gibt eine Liste von Dicts mit id, content, filename, directory zurück.
    """
    result = []
    file_count = 0
    dir_count = 0
    
    print(f"Sammle Dateien mit Endungen {', '.join(extensions)}...")
    
    # Get total directory count for better progress indication
    total_dirs = sum([len(dirpath) for dirpath, _, _ in os.walk(root_dir)])
    
    for dirpath, _, filenames in os.walk(root_dir):
        dir_count += 1
        if dir_count % 10 == 0:  # Show progress every 10 directories
            print(f"Verarbeite Verzeichnis {dir_count}/{total_dirs}: {os.path.relpath(dirpath, root_dir)}")
        
        for fname in filenames:
            if any(fname.lower().endswith(ext) for ext in extensions):
                file_count += 1
                full_path = os.path.join(dirpath, fname)
                try:
                    with open(full_path, encoding="utf-8") as f:
                        content = f.read()
                    result.append({
                        "id": os.path.relpath(full_path, root_dir),
                        "content": content,
                        "filename": fname,
                        "directory": os.path.relpath(dirpath, root_dir)
                    })
                    
                    if file_count % 50 == 0:  # Show progress every 50 files
                        print(f"Dateien gefunden: {file_count} (aktuell: {fname})")
                        
                except Exception as e:
                    print(f"Fehler beim Lesen von {full_path}: {e}")
    
    print(f"Dateisammlung abgeschlossen. Insgesamt {file_count} Dateien gefunden in {dir_count} Verzeichnissen.")
    return result

def main():
    all_data = []
    print("Starte Vektorisierung für folgende Verzeichnisse:")
    for root_dir in ROOT_DIRS:
        print(f"  - {root_dir} (nur {', '.join(FILE_EXTENSION_FILTERS)})")
        data = collect_files(root_dir, FILE_EXTENSION_FILTERS)
        print(f"{len(data)} Dateien gefunden in {root_dir}.")
        all_data.extend(data)
    print(f"Insgesamt {len(all_data)} Dateien gefunden. Starte Vektorisierung...")
    vectorize_data(all_data)
    print("Vektorisierung abgeschlossen.")

if __name__ == "__main__":
    main()