import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.package_function import package


class PackageTests(unittest.TestCase):
    def test_deployment_zip_has_runtime_query_and_no_local_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = (
                "function_app.py", "host.json", "requirements.txt", "esj_api/__init__.py",
                "esj_api/queries/epre.graphql", "local.settings.json", "respuesta_epre.txt",
                "esj_api/__pycache__/old.pyc", "tests/test_data.py", ".venv/secret.py",
            )
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test", encoding="utf-8")
            destination = root / "dist" / "function.zip"
            package(root, destination)
            with ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "function_app.py", "host.json", "requirements.txt", "esj_api/__init__.py",
                    "esj_api/queries/epre.graphql",
                })
