import unittest
from unittest.mock import patch

import upload
import variant_bandit


class ScheduledUploadRegressionTest(unittest.TestCase):
    def test_scheduled_run_always_attempts_one_upload(self):
        """A low cross-channel cadence must not suppress a scheduled DA run."""
        with (
            patch.object(upload, "DA_CLIENT_ID", "client"),
            patch.object(upload, "DA_CLIENT_SECRET", "secret"),
            patch.object(upload, "DA_ACCESS_TOKEN", "access"),
            patch.object(upload, "DA_REFRESH_TOKEN", "refresh"),
            patch.object(upload, "GDRIVE_FOLDER_ID", "folder"),
            patch.object(
                variant_bandit,
                "posts_this_run",
                side_effect=AssertionError("scheduled upload consulted cadence"),
            ),
            patch.object(upload, "load_uploaded_log", return_value={"files": []}),
            patch.object(upload, "get_valid_token", return_value=("access", "refresh")),
            patch.object(upload, "save_tokens_file"),
            patch.object(upload, "save_uploaded_log"),
            patch.object(upload, "download_media", return_value=["sample.png"]),
            patch.object(upload, "generate_tags", return_value=["fitness"]),
            patch("pool_loader.as_insights", return_value={}),
            patch("trending.get_trending_tags", return_value=[]),
            patch.object(upload, "bandit_pick", return_value=("Scheduled title", "variant")),
            patch.object(upload, "build_description", return_value=("NSFW", "description")),
            patch.object(upload, "upload_to_stash", return_value=("item-id", "success")) as stash,
            patch.object(
                upload,
                "publish_from_stash",
                return_value={"url": "https://example.invalid/post", "deviationid": "deviation-id"},
            ),
            patch.object(upload, "log_post"),
            patch.object(upload, "write_status") as write_status,
        ):
            result = upload.main()

        self.assertEqual(result, 0)
        stash.assert_called_once()
        write_status.assert_called_once_with("posted", 0)


if __name__ == "__main__":
    unittest.main()
