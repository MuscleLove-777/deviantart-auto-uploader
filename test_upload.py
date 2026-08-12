import unittest
import json
import tempfile
from pathlib import Path
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

    def test_invalid_refresh_token_stops_before_media_download(self):
        """Permanent OAuth failure must not download media or rewrite stale tokens."""
        with (
            patch.object(upload, "DA_CLIENT_ID", "client"),
            patch.object(upload, "DA_CLIENT_SECRET", "secret"),
            patch.object(upload, "DA_ACCESS_TOKEN", "expired-access"),
            patch.object(upload, "DA_REFRESH_TOKEN", "invalid-refresh"),
            patch.object(upload, "GDRIVE_FOLDER_ID", "folder"),
            patch.object(upload, "load_uploaded_log", return_value={"files": []}),
            patch.object(
                upload,
                "get_valid_token",
                side_effect=upload.AuthenticationError("refresh_token is invalid"),
            ),
            patch.object(upload, "save_tokens_file") as save_tokens,
            patch.object(upload, "discard_run_tokens") as discard_tokens,
            patch.object(upload, "download_media") as download_media,
            patch.object(upload, "write_status") as write_status,
        ):
            result = upload.main()

        self.assertEqual(result, 2)
        save_tokens.assert_not_called()
        discard_tokens.assert_called_once_with()
        download_media.assert_not_called()
        write_status.assert_called_once_with("auth_error")

    @patch.object(upload.requests, "post")
    def test_refresh_failure_raises_instead_of_returning_stale_tokens(self, post):
        response = post.return_value
        response.status_code = 400
        response.json.return_value = {
            "error": "invalid_request",
            "error_description": "The refresh_token is invalid.",
        }

        with self.assertRaises(upload.AuthenticationError):
            upload.refresh_access_token("expired-access", "invalid-refresh")

        self.assertEqual(post.call_args.kwargs["timeout"], 30)

    def test_retry_prefers_tokens_rotated_in_same_workflow_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = Path(tmpdir) / "tokens.json"
            token_path.write_text(json.dumps({
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
            }), encoding="utf-8")
            with patch.object(upload, "TOKENS_FILE", str(token_path)):
                tokens = upload.load_run_tokens("stale-access", "stale-refresh")

        self.assertEqual(tokens, ("rotated-access", "rotated-refresh"))


if __name__ == "__main__":
    unittest.main()
