import tempfile
import unittest
from pathlib import Path

from backend.agents.catalogue import Catalogue
from backend.agents.node_executor import Executor
from backend.agents.nodes import Nodes
from backend.models import SparrowConfig
from backend.storage import Storage


class StorageUsageTests(unittest.IsolatedAsyncioTestCase):
    """The Storage page's disk meter: Sparrow's share of each disk."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Storage(str(Path(self.temp.name) / "server"))
        await self.storage.load_all()
        await self.storage.save_config(SparrowConfig())
        self.nodes = Nodes(self.storage, lambda: None)

    def test_library_usage_sums_verified_files_per_disk(self):
        self.assertEqual(self.nodes.library_usage(), {})  # No catalogue yet.
        catalogue = Catalogue(self.storage, self.nodes)
        catalogue.save_asset("a", "local", "library", "A/A.mkv", {"size_bytes": 4_000})
        catalogue.save_asset("b", "local", "library", "B/B.mkv", {"size_bytes": 6_000})
        catalogue.save_asset("c", "node-1", "library-2", "C/C.mkv", {"size_bytes": 500})
        self.assertEqual(
            self.nodes.library_usage(),
            {("local", "library"): 10_000, ("node-1", "library-2"): 500},
        )

    def test_folders_on_one_disk_report_the_same_disk(self):
        root = Path(self.temp.name)
        (root / "library").mkdir()
        (root / "staging").mkdir()
        executor = Executor(
            root / "node", {"library": str(root / "library"), "staging": str(root / "staging")}
        )
        roots = {r["id"]: r for r in executor.capabilities()["roots"]}
        self.assertTrue(roots["library"]["disk"])
        self.assertEqual(roots["library"]["disk"], roots["staging"]["disk"])


if __name__ == "__main__":
    unittest.main()
