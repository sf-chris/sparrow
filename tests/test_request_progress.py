"""The app's one-line reading of a request's downloads."""

import unittest

from backend.agents.product_api import transfer_summary
from backend.models import Download, DownloadStatus


def download(status, progress=0.0, eta=-1, speed=0):
    return Download(
        id=f"dl-{status.value}-{progress}",
        name="hidden release name",
        magnet_url="",
        status=status,
        progress=progress,
        eta_seconds=eta,
        download_speed=speed,
    )


class TransferSummaryTest(unittest.TestCase):
    def test_nothing_downloading_yet_is_none(self):
        self.assertIsNone(transfer_summary([]))
        # A removed (stalled) copy doesn't count while a new one is found.
        self.assertIsNone(transfer_summary([download(DownloadStatus.ERROR, 0.3)]))

    def test_downloading_reports_progress_time_left_and_speed(self):
        summary = transfer_summary(
            [download(DownloadStatus.DOWNLOADING, 0.42, eta=380, speed=2_000_000)]
        )
        self.assertEqual(
            summary,
            {"state": "downloading", "progress": 0.42, "eta_seconds": 380, "speed": 2_000_000},
        )
        self.assertNotIn("hidden", str(summary))

    def test_several_copies_average_and_wait_for_the_slowest(self):
        summary = transfer_summary(
            [
                download(DownloadStatus.DOWNLOADING, 0.2, eta=600, speed=1),
                download(DownloadStatus.COMPLETED, 1.0),
                download(DownloadStatus.ORGANIZED, 1.0),
            ]
        )
        self.assertEqual(summary["state"], "downloading")
        self.assertEqual(summary["progress"], 0.6)
        self.assertEqual(summary["eta_seconds"], 600)

    def test_landed_copies_are_being_checked_then_done(self):
        self.assertEqual(
            transfer_summary([download(DownloadStatus.COMPLETED, 1.0)])["state"], "checking"
        )
        self.assertEqual(
            transfer_summary([download(DownloadStatus.ORGANIZED, 1.0)])["state"], "done"
        )
        self.assertEqual(
            transfer_summary([download(DownloadStatus.PAUSED, 0.5)])["state"], "paused"
        )


if __name__ == "__main__":
    unittest.main()
