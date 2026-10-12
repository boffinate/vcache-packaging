"""Standard-library tests for the immutable release publisher."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import release_publish
import release_assemble


class FakeApi:
    repo = "owner/repo"

    def __init__(self, *, immutable=True, commit="a" * 40, release=None):
        self.immutable = immutable
        self.commit = commit
        self.release = release
        self.tagged = release is not None
        self.calls = []

    def request(self, method, endpoint, body=None):
        self.calls.append((method, endpoint, body))
        if endpoint.endswith("/immutable-releases"):
            return {"enabled": self.immutable}
        if endpoint.endswith("/releases?per_page=100&page=1"):
            return [] if self.release is None else [self.release]
        if endpoint.endswith("/releases/1") and method == "GET":
            return self.release
        if "releases?per_page=100" in endpoint:
            return []
        if "/git/ref/tags/" in endpoint:
            if not self.tagged:
                raise release_publish.ApiError("not found", 404)
            return {"object": {"type": "commit", "sha": self.commit}}
        if endpoint.endswith("/git/refs") and method == "POST":
            self.tagged = True
            return {}
        if endpoint.endswith("/releases") and method == "POST":
            self.release = {"id": 1, "tag_name": body["tag_name"], "draft": True,
                            "upload_url": "https://uploads.github.test/release/1/assets{?name,label}", "assets": []}
            return self.release
        if "/releases/1" in endpoint and method == "PATCH":
            self.release["draft"] = False
            self.release["immutable"] = True
            return self.release
        raise AssertionError((method, endpoint, body))

    def upload(self, release, asset):
        uploaded = {"id": len(release["assets"]) + 1, "name": asset.name,
                    "state": "uploaded", "digest": "sha256:" + hashlib.sha256(asset.read_bytes()).hexdigest()}
        release["assets"].append(uploaded)
        return uploaded

    def delete_asset(self, asset_id):
        self.release["assets"] = [a for a in self.release["assets"] if a["id"] != asset_id]


class PublisherTests(unittest.TestCase):
    def test_publishes_complete_draft_and_checks_immutable_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"a")
            api = FakeApi()
            release_publish.publish(api, "vinyl-9-r1-debian", api.commit, "title", "body", directory, immutable_configured=True)
            self.assertTrue(api.release["immutable"])
            self.assertFalse(any("immutable-releases" in endpoint for _, endpoint, _ in api.calls))

    def test_rejects_disabled_immutability_before_tag_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"a")
            api = FakeApi(immutable=False)
            with self.assertRaisesRegex(release_publish.PublishError, "IMMUTABLE_RELEASES_CONFIGURED"):
                release_publish.publish(api, "tag", api.commit, "title", "body", directory)
            self.assertFalse(any(method == "POST" for method, _, _ in api.calls))

    def test_published_release_with_different_bytes_requires_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"new")
            api = FakeApi(release={"id": 1, "tag_name": "tag", "draft": False, "immutable": True,
                                   "assets": [{"id": 1, "name": "a.deb", "state": "uploaded",
                                               "digest": "sha256:" + hashlib.sha256(b"old").hexdigest()}]})
            with self.assertRaisesRegex(release_publish.PublishError, "bump package_revision"):
                release_publish.publish(api, "tag", api.commit, "title", "body", directory, immutable_configured=True)

    def test_published_release_with_wrong_tag_commit_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"same")
            api = FakeApi(commit="b" * 40, release={"id": 1, "tag_name": "tag", "draft": False,
                                                     "immutable": True,
                                                     "assets": [{"id": 1, "name": "a.deb", "state": "uploaded",
                                                                 "digest": "sha256:" + hashlib.sha256(b"same").hexdigest()}]})
            with self.assertRaisesRegex(release_publish.PublishError, "expected"):
                release_publish.publish(api, "tag", "a" * 40, "title", "body", directory, immutable_configured=True)
            self.assertFalse(any(method == "POST" for method, _, _ in api.calls))

    def test_prerelease_is_never_accepted_as_a_published_package_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"same")
            api = FakeApi(release={"id": 1, "tag_name": "tag", "draft": False, "immutable": True,
                                   "prerelease": True,
                                   "assets": [{"id": 1, "name": "a.deb", "state": "uploaded",
                                               "digest": "sha256:" + hashlib.sha256(b"same").hexdigest()}]})
            with self.assertRaisesRegex(release_publish.PublishError, "stable"):
                release_publish.publish(api, "tag", api.commit, "title", "body", directory,
                                        immutable_configured=True)

    def test_lost_upload_response_is_reconciled_from_the_draft(self):
        class LostUpload(FakeApi):
            def __init__(self):
                super().__init__()
                self.lost = True

            def upload(self, release, asset):
                return super().upload(release, asset)
                if self.lost:
                    self.lost = False
                    raise release_publish.PublishError("connection closed after upload")

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"a")
            api = LostUpload()
            release_publish.publish(api, "tag", api.commit, "title", "body", directory, immutable_configured=True)
            self.assertFalse(api.release["draft"])

    def test_starter_asset_is_repaired_only_while_the_release_is_a_draft(self):
        class StarterUpload(FakeApi):
            def __init__(self):
                super().__init__()
                self.once = True

            def upload(self, release, asset):
                if self.once:
                    self.once = False
                    release["assets"].append({"id": 99, "name": asset.name, "state": "starter"})
                    raise release_publish.PublishError("upload interrupted")
                return super().upload(release, asset)

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"a")
            api = StarterUpload()
            release_publish.publish(api, "tag", api.commit, "title", "body", directory, immutable_configured=True)
            self.assertEqual([asset["state"] for asset in api.release["assets"]], ["uploaded"])

    def test_lost_publish_response_is_reconciled_from_release_state(self):
        class LostPublish(FakeApi):
            def __init__(self):
                super().__init__()
                self.lost = True

            def request(self, method, endpoint, body=None):
                result = super().request(method, endpoint, body)
                if method == "PATCH" and self.lost:
                    self.lost = False
                    raise release_publish.PublishError("connection closed after publish")
                return result

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "a.deb").write_bytes(b"a")
            api = LostPublish()
            release_publish.publish(api, "tag", api.commit, "title", "body", directory, immutable_configured=True)
            self.assertTrue(api.release["immutable"])

    def test_transport_uses_upload_url_binary_content_without_clobber(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "a+b.deb"
            asset.write_bytes(b"asset")
            api = release_publish.GhApi("owner/repo")
            completed = subprocess.CompletedProcess([], 0, b"{}", b"")
            with mock.patch("release_publish.subprocess.run", return_value=completed) as run:
                api.upload({"upload_url": "https://uploads.github.test/release/1/assets{?name,label}"}, asset)
            command = run.call_args.args[0]
            self.assertIn("https://uploads.github.test/release/1/assets?name=a%2Bb.deb", command)
            self.assertIn("Content-Type: application/octet-stream", command)
            self.assertNotIn("--clobber", command)

    def test_transport_retries_explicit_quota_but_not_an_auth_403(self):
        waits = []
        api = release_publish.GhApi("owner/repo", sleep=waits.append, monotonic=lambda: 0, max_wait=10)
        limited = subprocess.CompletedProcess([], 1, "", "HTTP 429\nretry-after: 2\nsecondary rate limit")
        success = subprocess.CompletedProcess([], 0, "{}", "")
        with mock.patch("release_publish.subprocess.run", side_effect=[limited, success]) as run:
            self.assertEqual(api.request("GET", "repos/owner/repo"), {})
        self.assertEqual(waits, [2.0])
        auth = subprocess.CompletedProcess([], 1, "", "HTTP 403\nBad credentials")
        with mock.patch("release_publish.subprocess.run", return_value=auth) as run:
            with self.assertRaises(release_publish.ApiError):
                api.request("GET", "repos/owner/repo")
        self.assertEqual(run.call_count, 1)

    def test_transport_does_not_exceed_its_shared_wait_budget(self):
        api = release_publish.GhApi("owner/repo", sleep=lambda _: None, monotonic=lambda: 0, max_wait=1)
        limited = subprocess.CompletedProcess([], 1, b"", b"HTTP 429\nretry-after: 2\nsecondary rate limit")
        with mock.patch("release_publish.subprocess.run", return_value=limited) as run:
            with self.assertRaises(release_publish.ApiError):
                api.request("GET", "repos/owner/repo")
        self.assertEqual(run.call_count, 1)

    def test_primary_limit_headers_on_stdout_are_retried(self):
        waits = []
        api = release_publish.GhApi("owner/repo", sleep=waits.append, monotonic=lambda: 0, max_wait=10)
        limited = subprocess.CompletedProcess([], 1,
            b"HTTP/2 403\r\nX-RateLimit-Remaining: 0\r\nX-RateLimit-Reset: 102\r\n\r\n", b"gh: API rate limit exceeded")
        with mock.patch("release_publish.time.time", return_value=100), \
             mock.patch("release_publish.subprocess.run", side_effect=[limited, subprocess.CompletedProcess([], 0, "{}", "")]):
            self.assertEqual(api.request("GET", "repos/owner/repo"), {})
        self.assertEqual(waits, [3.0])

    def test_binary_upload_retries_quota_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "a.deb"
            asset.write_bytes(b"asset")
            waits = []
            api = release_publish.GhApi("owner/repo", sleep=waits.append, monotonic=lambda: 0, max_wait=10)
            limited = subprocess.CompletedProcess([], 1, b"HTTP/2 429\r\nRetry-After: 2\r\n\r\n", b"rate limited")
            uploaded = subprocess.CompletedProcess([], 0, b'{"name":"a.deb","state":"uploaded"}', b"")
            with mock.patch("release_publish.subprocess.run", side_effect=[limited, uploaded]):
                self.assertEqual(api.upload({"upload_url": "https://uploads.test/{?name}"}, asset)["name"], "a.deb")
            self.assertEqual(waits, [2.0])

    def test_wait_budget_is_shared_across_requests(self):
        class Clock:
            value = 0
            def __call__(self): return self.value
            def sleep(self, delay): self.value += delay
        clock = Clock()
        api = release_publish.GhApi("owner/repo", sleep=clock.sleep, monotonic=clock, max_wait=3)
        limited = subprocess.CompletedProcess([], 1, "", "HTTP 429\nretry-after: 2\nsecondary rate limit")
        with mock.patch("release_publish.subprocess.run", side_effect=[limited, subprocess.CompletedProcess([], 0, "{}", ""), limited]) as run:
            self.assertEqual(api.request("GET", "one"), {})
            with self.assertRaises(release_publish.ApiError):
                api.request("GET", "two")
        self.assertEqual(run.call_count, 3)

    def test_include_headers_are_removed_without_truncating_json_body(self):
        api = release_publish.GhApi("owner/repo")
        response = subprocess.CompletedProcess([], 0,
            b"HTTP/2 200\r\nX-RateLimit-Remaining: 9\r\n\r\n{\"body\": \"line1\\n\\nline2\"}", b"")
        with mock.patch("release_publish.subprocess.run", return_value=response):
            self.assertEqual(api.request("GET", "repos/owner/repo"), {"body": "line1\n\nline2"})

    def test_remote_digest_hashes_binary_body_without_interpreting_headers(self):
        payload = b"HTTP/2 200\r\n\r\nbytes\x00\n\nmore"

        class Download:
            def _run(self, command, *, output_path, stderr):
                Path(output_path).write_bytes(payload)
                return subprocess.CompletedProcess(command, 0, None, b"")

        self.assertEqual(release_publish._digest(Download(), {"name": "a", "url": "asset"}),
                         hashlib.sha256(payload).hexdigest())

    def test_failed_binary_download_bytes_are_not_hashed_after_retry(self):
        waits = []
        api = release_publish.GhApi("owner/repo", sleep=waits.append, monotonic=lambda: 0, max_wait=10)
        failed = subprocess.CompletedProcess([], 1, None, b"HTTP 429\nretry-after: 1")
        succeeded = subprocess.CompletedProcess([], 0, None, b"")
        def run(command, stdout=None, **kwargs):
            if not hasattr(run, "count"):
                run.count = 0
            run.count += 1
            stdout.write(b"failed" if run.count == 1 else b"good")
            return failed if run.count == 1 else succeeded
        with mock.patch("release_publish.subprocess.run", side_effect=run):
            self.assertEqual(release_publish._digest(api, {"name": "a", "url": "asset"}),
                             hashlib.sha256(b"good").hexdigest())
        self.assertEqual(waits, [1.0])

    def test_assembly_rejects_missing_attestation_before_catalog_load(self):
        with mock.patch.dict(os.environ, {"IMMUTABLE_RELEASES_CONFIGURED": ""}), \
             mock.patch("release_assemble.matrix.load_catalog") as load_catalog:
            with self.assertRaises(SystemExit):
                release_assemble.main()
        load_catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
