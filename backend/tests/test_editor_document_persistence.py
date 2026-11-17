"""Stage 3 editor documents persist through the existing project store."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class EditorDocumentPersistenceTests(unittest.TestCase):
    def test_transcript_and_design_survive_a_backend_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                __import__("app.main", fromlist=["settings"]).settings,
                project_data_dir=Path(temp_dir) / "projects",
            )
            with patch("app.main.settings", settings):
                with TestClient(app) as first_server:
                    project = first_server.post(
                        "/api/v1/projects", json={"name": "Persistencia del editor"}
                    ).json()
                    document = {
                        "transcription": {
                            "language": "es",
                            "segments": [{
                                "id": "segment-a", "start": 0.0, "end": 1.4,
                                "text": "Hola mundo",
                                "words": [
                                    {"id": "word-a", "text": "Hola ", "start": 0.0, "end": 0.5},
                                    {"id": "word-b", "text": "mundo", "start": 0.5, "end": 1.4},
                                ],
                            }],
                        },
                        "design": {
                            "fontFamily": "Montserrat Black", "activeWordColor": "#ffff00",
                            "positionX": 61, "positionY": 77, "maxWidth": 80,
                        },
                    }
                    saved = first_server.patch(
                        f"/api/v1/projects/{project['id']}", json={"document": document}
                    )
                    self.assertEqual(saved.status_code, 200, saved.text)

                with TestClient(app) as restarted_server:
                    restored = restarted_server.get(f"/api/v1/projects/{project['id']}")
                    self.assertEqual(restored.status_code, 200)
                    restored_document = restored.json()["document"]
                    self.assertEqual(restored_document["transcription"], document["transcription"])
                    self.assertEqual(restored_document["design"], document["design"])


if __name__ == "__main__":
    unittest.main()
