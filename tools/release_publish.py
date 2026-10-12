#!/usr/bin/env python3
"""Publish an immutable package release through the GitHub REST API.

The module separates the state machine from its ``gh api`` transport so its
failure handling can be tested without a GitHub repository.  A published
release is evidence, never a thing a retry is allowed to replace.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import vmod_cache


class PublishError(Exception):
    pass


class ApiError(PublishError):
    def __init__(self, message: str, status: int | None = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}


def tag_name(engine: dict, target: str) -> str:
    return f"{engine['id']}-r{engine['package_revision']}-{target}"


def asset_digests(directory: Path) -> dict[str, str]:
    return {path.name: vmod_cache.sha256_file(path)
            for path in sorted(directory.iterdir()) if path.is_file()}


class GhApi:
    """Small authenticated ``gh api`` adapter.  Errors are never absence."""

    def __init__(self, repo: str, *, sleep=time.sleep, monotonic=time.monotonic, max_wait=4_800):
        self.repo = repo
        self.sleep = sleep
        self.monotonic = monotonic
        self.max_wait = max_wait
        self.deadline = monotonic() + max_wait
        self.secondary_delay = 60.0

    @staticmethod
    def _headers(text: str) -> dict[str, str]:
        return {key.lower(): value.strip() for key, value in
                (line.split(":", 1) for line in text.splitlines() if ":" in line)}

    def _retry_delay(self, result) -> float | None:
        stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")
        stdout = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else (result.stdout or "")
        text = (stderr + stdout).lower()
        headers = self._headers(stderr + "\n" + stdout)
        status = re.search(r"HTTP(?:/\S+)? (\d{3})", stderr + "\n" + stdout)
        if not status or int(status.group(1)) not in (403, 429):
            return None
        retry_after = headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        if headers.get("x-ratelimit-remaining") == "0":
            try:
                return max(1.0, float(headers["x-ratelimit-reset"]) - time.time() + 1)
            except (KeyError, ValueError):
                return None
        if "secondary rate limit" in text or "rate limit exceeded" in text:
            delay = self.secondary_delay
            self.secondary_delay = min(self.secondary_delay * 2, 600.0)
            return delay
        return None

    def _run(self, command: list[str], **kwargs):
        output_path = kwargs.pop("output_path", None)
        while True:
            if output_path is None:
                result = subprocess.run(command, **kwargs)
            else:
                with Path(output_path).open("wb") as output:
                    result = subprocess.run(command, stdout=output, **kwargs)
            if not result.returncode:
                self.secondary_delay = 60.0
                return result
            delay = self._retry_delay(result)
            if delay is None or self.monotonic() + delay > self.deadline:
                return result
            print(f"GitHub rate limit; waiting {delay:.0f}s before retry", file=sys.stderr)
            self.sleep(delay)

    @staticmethod
    def _body(output: str | bytes) -> str:
        text = output.decode(errors="replace") if isinstance(output, bytes) else output
        return re.split(r"\r?\n\r?\n", text, maxsplit=1)[-1]

    def request(self, method: str, endpoint: str, body: dict | None = None) -> object:
        command = ["gh", "api", "--include", "-X", method, endpoint]
        if body is not None:
            command += ["--input", "-"]
        result = self._run(command, input=json.dumps(body) if body is not None else None,
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            match = re.search(r"HTTP (\d{3})", stderr)
            raise ApiError(f"GitHub API {method} {endpoint} failed: {stderr.strip()}",
                           int(match.group(1)) if match else None, self._headers(stderr))
        try:
            output = self._body(result.stdout)
            return json.loads(output) if output.strip() else None
        except json.JSONDecodeError as exc:
            raise PublishError(f"GitHub API {method} {endpoint} returned invalid JSON") from exc

    def upload(self, release: dict, asset: Path) -> dict:
        template = release.get("upload_url")
        if not isinstance(template, str):
            raise PublishError("draft release has no upload URL")
        endpoint = template.split("{", 1)[0] + "?name=" + quote(asset.name, safe="")
        result = self._run(["gh", "api", "--include", "-X", "POST", endpoint,
                            "-H", "Content-Type: application/octet-stream", "--input", str(asset)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode:
            stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            raise PublishError(f"GitHub asset upload {asset.name} failed: {stderr.strip()}")
        try:
            uploaded = json.loads(self._body(result.stdout))
        except json.JSONDecodeError as exc:
            raise PublishError(f"GitHub asset upload {asset.name} returned invalid JSON") from exc
        if not isinstance(uploaded, dict):
            raise PublishError(f"GitHub asset upload {asset.name} returned no asset")
        return uploaded

    def delete_asset(self, asset_id: int) -> None:
        self.request("DELETE", f"repos/{self.repo}/releases/assets/{asset_id}")


def _release_assets(release: dict, *, allow_starter: bool = False) -> dict[str, dict]:
    result = {}
    for asset in release.get("assets", []):
        name = asset.get("name")
        if not isinstance(name, str) or name in result:
            raise PublishError(f"release {release.get('tag_name')} has duplicate or invalid asset names")
        if asset.get("state") != "uploaded" and not (allow_starter and asset.get("state") == "starter"):
            raise PublishError(f"release {release.get('tag_name')} asset {name} is not uploaded")
        result[name] = asset
    return result


def _digest(api: GhApi, asset: dict) -> str:
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        return digest[7:]
    url = asset.get("url")
    if not url:
        raise PublishError(f"release asset {asset.get('name', '<unknown>')} has no API URL")
    # Asset API downloads are binary. gh writes them to stdout; use its bytes
    # instead of trusting a checksum file uploaded beside the package.
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary) / "asset"
        result = api._run(["gh", "api", "-H", "Accept: application/octet-stream", url],
                          output_path=destination, stderr=subprocess.PIPE)
        if result.returncode:
            stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
            raise PublishError(f"cannot download release asset {asset.get('name')}: {stderr.strip()}")
        return vmod_cache.sha256_file(destination)


def _verify_assets(api: GhApi, release: dict, expected: dict[str, str]) -> None:
    assets = _release_assets(release)
    if set(assets) != set(expected):
        raise PublishError(f"release {release['tag_name']} has asset names {sorted(assets)}, expected {sorted(expected)}")
    for name, expected_digest in expected.items():
        actual = _digest(api, assets[name])
        if actual != expected_digest:
            raise PublishError(f"release {release['tag_name']} asset {name} digest differs; bump package_revision")


def _resolve_tag(api: GhApi, tag: str) -> str | None:
    try:
        ref = api.request("GET", f"repos/{api.repo}/git/ref/tags/{tag}")
    except ApiError as exc:
        # gh reports a missing ref as a 404. Other errors stay fatal; this
        # narrow distinction prevents permission outages looking like absence.
        if exc.status != 404:
            raise
        return None
    assert isinstance(ref, dict)
    obj = ref["object"]
    while obj["type"] == "tag":
        annotated = api.request("GET", f"repos/{api.repo}/git/tags/{obj['sha']}")
        assert isinstance(annotated, dict)
        obj = annotated["object"]
    if obj["type"] != "commit":
        raise PublishError(f"tag {tag} resolves to {obj['type']}, not a commit")
    return obj["sha"]


def _releases(api: GhApi) -> list[dict]:
    releases = []
    page = 1
    while True:
        batch = api.request("GET", f"repos/{api.repo}/releases?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise PublishError("GitHub releases endpoint returned a non-list")
        releases.extend(batch)
        if len(batch) < 100:
            return releases
        page += 1


def _find_release(releases: list[dict], tag: str) -> dict | None:
    matches = [release for release in releases if release.get("tag_name") == tag]
    if len(matches) > 1:
        raise PublishError(f"multiple releases use tag {tag}")
    return matches[0] if matches else None


def _release_by_id(api: GhApi, release_id: int) -> dict:
    release = api.request("GET", f"repos/{api.repo}/releases/{release_id}")
    if not isinstance(release, dict):
        raise PublishError(f"GitHub release {release_id} returned no release")
    return release


def _create_tag(api: GhApi, tag: str, commit: str) -> None:
    try:
        api.request("POST", f"repos/{api.repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": commit})
    except PublishError:
        # An accepted create can be followed by a lost response. Re-read once;
        # any different ref remains a revision conflict.
        if _resolve_tag(api, tag) != commit:
            raise


def publish(api: GhApi, tag: str, commit: str, title: str, body: str, directory: Path,
            sibling_prefix: str | None = None, *, immutable_configured: bool = False,
            releases: list[dict] | None = None) -> dict:
    """Create/resume a draft, prove its bytes, then publish it immutably."""
    expected = asset_digests(directory)
    if not expected:
        raise PublishError("refusing to publish an empty asset set")
    if not immutable_configured:
        raise PublishError("IMMUTABLE_RELEASES_CONFIGURED=true is required before publication")
    releases = _releases(api) if releases is None else releases
    if sibling_prefix:
        for sibling in releases:
            sibling_tag = sibling.get("tag_name", "")
            if sibling_tag.startswith(sibling_prefix) and sibling_tag != tag:
                sibling_commit = _resolve_tag(api, sibling_tag)
                if sibling_commit != commit:
                    raise PublishError(
                        f"revision wave {sibling_prefix} already uses {sibling_commit}, expected {commit}; "
                        "bump package_revision"
                    )
    release = _find_release(releases, tag)
    resolved = _resolve_tag(api, tag)
    if release and not release.get("draft"):
        if resolved != commit:
            raise PublishError(f"published tag {tag} resolves to {resolved}, expected {commit}; bump package_revision")
        _verify_assets(api, release, expected)
        if release.get("immutable") is not True or release.get("prerelease") is True:
            raise PublishError(f"published release {tag} is not immutable stable release")
        return release
    if resolved is None:
        _create_tag(api, tag, commit)
        resolved = _resolve_tag(api, tag)
    if resolved != commit:
        raise PublishError(f"tag {tag} resolves to {resolved}, expected {commit}; bump package_revision")
    if release is None:
        last_error = None
        for _ in range(3):
            try:
                created = api.request("POST", f"repos/{api.repo}/releases",
                                      {"tag_name": tag, "target_commitish": commit, "name": title,
                                       "body": body, "draft": True})
                if not isinstance(created, dict):
                    raise PublishError("GitHub create release returned no release")
                release = created
                break
            except PublishError as exc:
                last_error = exc
                releases = _releases(api)
                release = _find_release(releases, tag)
                if release is not None:
                    break
        if release is None:
            raise last_error or PublishError(f"could not create draft {tag}")
    if not release.get("draft"):
        return publish(api, tag, commit, title, body, directory, sibling_prefix,
                       immutable_configured=immutable_configured, releases=releases)
    assets = _release_assets(release, allow_starter=True)
    unexpected = set(assets) - set(expected)
    if unexpected:
        raise PublishError(f"draft {tag} has unexpected assets: {sorted(unexpected)}")
    for name, digest in expected.items():
        asset = assets.get(name)
        if asset is not None:
            if asset.get("state") == "starter":
                try:
                    api.delete_asset(int(asset["id"]))
                except PublishError:
                    release = _release_by_id(api, int(release["id"]))
            elif _digest(api, asset) == digest:
                continue
            else:
                raise PublishError(f"draft {tag} asset {name} digest differs; bump package_revision")
        last_error = None
        for _ in range(3):
            try:
                uploaded = api.upload(release, directory / name)
                if uploaded.get("state") != "uploaded" or uploaded.get("name") != name or _digest(api, uploaded) != digest:
                    raise PublishError(f"upload of {name} did not return the expected uploaded asset")
                assets[name] = uploaded
                break
            except PublishError as exc:
                last_error = exc
            refreshed = _release_by_id(api, int(release["id"]))
            if not refreshed.get("draft"):
                raise PublishError(f"draft {tag} disappeared while uploading")
            candidate = _release_assets(refreshed, allow_starter=True).get(name)
            if candidate is not None and candidate.get("state") == "uploaded":
                if _digest(api, candidate) != digest:
                    raise PublishError(f"draft {tag} asset {name} digest differs; bump package_revision")
                release = refreshed
                break
            if candidate is not None and candidate.get("state") == "starter":
                api.delete_asset(int(candidate["id"]))
            release = refreshed
        else:
            raise last_error or PublishError(f"upload of {name} did not appear in draft {tag}")
    # Successful upload responses are verified above. A final read catches an
    # external change before publishing without scanning the release history.
    release = _release_by_id(api, int(release["id"]))
    if not release.get("draft"):
        raise PublishError(f"draft {tag} disappeared while uploading")
    _verify_assets(api, release, expected)
    if _resolve_tag(api, tag) != commit:
        raise PublishError(f"tag {tag} changed before publication; bump package_revision")
    last_error = None
    for _ in range(3):
        try:
            api.request("PATCH", f"repos/{api.repo}/releases/{release['id']}", {"draft": False})
        except PublishError as exc:
            last_error = exc
        release = _release_by_id(api, int(release["id"]))
        if not release.get("draft"):
            break
    else:
        raise last_error or PublishError(f"release {tag} remained a draft")
    if release is None or release.get("draft") or release.get("immutable") is not True or release.get("prerelease") is True:
        raise PublishError(f"release {tag} did not become an immutable published release")
    if _resolve_tag(api, tag) != commit:
        raise PublishError(f"tag {tag} changed during publication")
    _verify_assets(api, release, expected)
    return release


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--sibling-prefix")
    parser.add_argument("--immutable-releases-configured", action="store_true")
    args = parser.parse_args(argv)
    try:
        publish(GhApi(args.repo), args.tag, args.commit, args.title, args.body_file.read_text(), args.assets,
                args.sibling_prefix, immutable_configured=args.immutable_releases_configured)
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
