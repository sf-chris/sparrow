from __future__ import annotations

import unittest

from backend.main import _legacy_active_downloads
from backend.models import Download, DownloadStatus


class LegacyBoundaryTests(unittest.TestCase):
    def test_legacy_poller_never_touches_agent_managed_downloads(self) -> None:
        agent_download = Download(
            id="agent", name="Agent", magnet_url="magnet:?xt=agent",
            torrent_hash="a" * 40, status=DownloadStatus.DOWNLOADING,
            metadata={"agent_managed": True},
        )
        legacy_download = Download(
            id="legacy", name="Legacy", magnet_url="magnet:?xt=legacy",
            torrent_hash="b" * 40, status=DownloadStatus.DOWNLOADING,
        )

        selected = _legacy_active_downloads([agent_download, legacy_download])

        self.assertEqual([download.id for download in selected], ["legacy"])
