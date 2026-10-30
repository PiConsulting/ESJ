"""Genera un ZIP de código para revisión, sin settings, datos ni dependencias locales."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def package(root: Path, destination: Path) -> list[str]:
    files = [root / name for name in ("function_app.py", "host.json", "requirements.txt")]
    files.extend(sorted(path for path in (root / "esj_api").rglob("*")
                        if path.is_file() and path.suffix in {".py", ".graphql"}
                        and "__pycache__" not in path.parts))
    destination.parent.mkdir(parents=True, exist_ok=True)
    names = []
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(root).as_posix()
            archive.write(path, name)
            names.append(name)
    return names


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    output = root / "dist" / "esj-function.zip"
    names = package(root, output)
    print(f"Paquete: {output}")
    print(f"Archivos: {len(names)}. Requiere build remoto para instalar requirements.txt.")
