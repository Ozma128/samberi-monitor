"""
Утилита для создания чистого архива проекта для загрузки на сервер Selectel.
Запуск: python deploy/pack_for_server.py
"""

import os
import zipfile

def create_server_bundle():
    zip_filename = "samberi_monitoring_server.zip"
    exclude_dirs = {".git", ".system_generated", "__pycache__", "venv", ".pytest_cache"}
    exclude_extensions = {".pyc", ".log", ".tmp"}

    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as z:
        for foldername, subfolders, filenames in os.walk(root_dir):
            # Фильтруем лишние папки
            subfolders[:] = [d for d in subfolders if d not in exclude_dirs]
            
            for filename in filenames:
                if any(filename.endswith(ext) for ext in exclude_extensions):
                    continue
                if filename == zip_filename:
                    continue
                
                filepath = os.path.join(foldername, filename)
                arcname = os.path.relpath(filepath, root_dir)
                z.write(filepath, arcname)
                
    size_kb = round(os.path.getsize(zip_filename)/1024, 1)
    print(f"[OK] Gotovo! Sozdan arhiv dlya servera: {zip_filename} ({size_kb} KB)")

if __name__ == "__main__":
    create_server_bundle()
