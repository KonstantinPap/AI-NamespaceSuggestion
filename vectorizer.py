"""
Vectorizer Module

This module handles the vectorization of data using LanceDB and a local Ollama instance.
"""

import lancedb
from typing import List, Dict
import requests
from tqdm import tqdm
import hashlib
import os

# Constants
LANCEDB_PATH = "./lancedb"  # Lokaler Pfad zur LanceDB-Datenbank
OLLAMA_URL = "http://localhost:11434/api/embeddings"
OLLAMA_MODEL = "mxbai-embed-large:latest"
FILE_EXTENSION_FILTERS = [".al", ".json"]  # Erlaubte Dateiendungen

# Initialize LanceDB
def initialize_lancedb() -> lancedb.LanceDB:
    """
    Initialize the LanceDB database.

    Returns:
        lancedb.LanceDB: LanceDB instance.
    """
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
    table = db.create_table(
        "namespace_vectors",
        schema={
            "id": lancedb.ScalarType.STRING,
            "content": lancedb.ScalarType.STRING,
            "embedding": lancedb.VectorType.FLOAT32(1024),  # mxbai-embed-large: 1024-dim
            "filename": lancedb.ScalarType.STRING,
            "directory": lancedb.ScalarType.STRING,
            "content_hash": lancedb.ScalarType.STRING,
        },
        overwrite=False  # Nicht überschreiben, sondern ergänzen/aktualisieren
    )

    for item in tqdm(data, desc="Vektorisieren", unit="Dokument"):
        content_hash = compute_content_hash(item["content"])
        # Prüfe, ob bereits ein Eintrag mit gleichem filename und content_hash existiert
        existing = table.search(
            (table["filename"] == item.get("filename", "")) &
            (table["content_hash"] == content_hash)
        ).to_list()
        if existing:
            continue  # Datei und Inhalt sind unverändert, überspringen

        # Optional: Lösche alten Eintrag mit gleichem filename, falls vorhanden
        table.delete(table["filename"] == item.get("filename", ""))

        embedding = generate_embedding(item["content"])
        table.insert({
            "id": item["id"],
            "content": item["content"],
            "embedding": embedding,
            "filename": item.get("filename", ""),
            "directory": item.get("directory", ""),
            "content_hash": content_hash,
        })

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
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if any(fname.lower().endswith(ext) for ext in extensions):
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
                except Exception as e:
                    print(f"Fehler beim Lesen von {full_path}: {e}")
    return result

def main():
    root_dir = "./"  # Passe das Startverzeichnis ggf. an
    print(f"Starte Vektorisierung ab: {root_dir} (nur {', '.join(FILE_EXTENSION_FILTERS)})")
    data = collect_files(root_dir, FILE_EXTENSION_FILTERS)
    print(f"{len(data)} Dateien gefunden. Starte Vektorisierung...")
    vectorize_data(data)
    print("Vektorisierung abgeschlossen.")

if __name__ == "__main__":
    main()