import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from backend.storage import Storage
from backend.models import LibraryItem, MediaType


class StorageMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_sqlite_does_not_resurrect_stale_json(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(temp)
            await storage.load_all()
            item = LibraryItem(
                id="old", title="Old entry", media_type=MediaType.MOVIE, path="/missing"
            )
            (Path(temp) / "library.json").write_text(json.dumps([item.to_dict()]))
            restarted = Storage(temp)
            await restarted.load_all()
            self.assertEqual(restarted.get_library(), [])

    async def test_corrupt_database_is_preserved_and_not_replaced_by_json(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Storage(temp)
            await storage.load_all()
            with storage._connect() as db:
                db.execute(
                    "INSERT INTO library_items VALUES ('broken','not-json','Broken','movie',0)"
                )
            (Path(temp) / "library.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "preserved"):
                await Storage(temp).load_all()
            with storage._connect() as db:
                self.assertEqual(
                    db.execute("SELECT data FROM library_items").fetchone()["data"],
                    "not-json",
                )

    async def test_interrupted_json_import_retries_without_losing_source_records(self):
        with tempfile.TemporaryDirectory() as temp:
            item = LibraryItem(
                id="existing",
                title="Existing title",
                media_type=MediaType.MOVIE,
                path="/missing",
            )
            (Path(temp) / "library.json").write_text(json.dumps([item.to_dict()]))
            storage = Storage(temp)
            storage._save_config_sqlite = AsyncMock(
                side_effect=OSError("simulated interrupted migration")
            )
            with self.assertRaises(OSError):
                await storage.load_all()
            restarted = Storage(temp)
            await restarted.load_all()
            self.assertEqual(restarted.get_library()[0].id, "existing")
            self.assertIn("existing", (Path(temp) / "library.json").read_text())

    async def test_legacy_snapshot_and_malformed_json_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "library.json"
            source.write_text("{broken")
            storage = Storage(temp)
            copies = list((Path(temp) / "backups").glob("*/library.json"))
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_text(), "{broken")
            with self.assertRaisesRegex(ValueError, "preserved"):
                await storage.load_all()
            self.assertEqual(source.read_text(), "{broken")
