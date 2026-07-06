#!/usr/bin/env python3
"""
AppImage Updater - fetch AppImages from GitHub releases or direct URLs and install
via install-appimage.

Reads app configurations from appimages.csv and updates existing installs under
/opt/{App_Name}/.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = SCRIPT_DIR / "appimages.csv"
DOWNLOAD_DIR = SCRIPT_DIR / "downloads"
GITHUB_TOKEN_FILE = SCRIPT_DIR / "github_token.env"
GITHUB_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BACKOFF_SEC = 2.0
GITHUB_API_DELAY_SEC = 1.0


class Logger:
    use_colors = True

    @classmethod
    def _print(cls, message: str, color: str = "") -> None:
        if cls.use_colors and color:
            print(f"{color}{message}\033[0m")
        else:
            print(message)

    @classmethod
    def info(cls, message: str) -> None:
        cls._print(message, "\033[0;34m")

    @classmethod
    def success(cls, message: str) -> None:
        cls._print(message, "\033[0;32m")

    @classmethod
    def warning(cls, message: str) -> None:
        cls._print(message, "\033[1;33m")

    @classmethod
    def error(cls, message: str) -> None:
        cls._print(message, "\033[0;31m")

    @classmethod
    def dim(cls, message: str) -> None:
        cls._print(message, "\033[2m")

    @classmethod
    def header(cls, message: str) -> None:
        cls.newline()
        cls.info("=" * 43)
        cls.info(message)
        cls.info("=" * 43)

    @classmethod
    def newline(cls) -> None:
        print()


@dataclass
class AppImageEntry:
    github_repo: str
    name: str
    download_url: str
    asset_match: str
    enabled: bool

    @property
    def source_label(self) -> str:
        if self.download_url:
            return self.download_url
        return self.github_repo

    @property
    def uses_direct_download(self) -> bool:
        return bool(self.download_url)

    @property
    def owner_repo(self) -> tuple[str, str]:
        slug = normalize_github_repo(self.github_repo)
        owner, repo = slug.split("/", 1)
        return owner, repo

    @property
    def install_dir(self) -> Path:
        return Path("/opt") / self.name

    @property
    def installed_binary(self) -> Path:
        return self.install_dir / self.name

    @property
    def desktop_file(self) -> Path:
        return Path("/usr/share/applications") / f"{self.name}.desktop"

    def desktop_exec_binary(self) -> Path | None:
        if not self.desktop_file.is_file():
            return None

        for line in self.desktop_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("Exec="):
                continue
            exec_line = line[5:].strip()
            if not exec_line:
                return None
            binary = exec_line.split()[0]
            path = Path(binary)
            if path.is_file():
                return path
            return None

        return None

    def legacy_appimage(self) -> Path:
        return self.install_dir / f"{self.name}.AppImage"

    def needs_layout_normalize(self, matched_binary: Path | None = None) -> bool:
        canonical = self.installed_binary.resolve()

        if self.legacy_appimage().is_file():
            return True

        exec_path = self.desktop_exec_binary()
        if exec_path is not None and exec_path.resolve() != canonical:
            return True

        if matched_binary is not None and matched_binary.resolve() != canonical:
            return True

        return False

    def list_installed_binaries(self) -> list[Path]:
        seen: set[Path] = set()
        candidates: list[Path] = []

        def add(path: Path | None) -> None:
            if path is None or not path.is_file():
                return
            resolved = path.resolve()
            if resolved in seen:
                return
            seen.add(resolved)
            candidates.append(resolved)

        add(self.install_dir / self.name)
        add(self.desktop_exec_binary())
        add(self.legacy_appimage())

        if self.install_dir.is_dir():
            for path in sorted(self.install_dir.glob("*.AppImage")):
                add(path)

        return candidates

    def find_installed_binary(self) -> Path | None:
        binaries = self.list_installed_binaries()
        return binaries[0] if binaries else None

    def validate(self) -> list[str]:
        errors: list[str] = []
        has_github = bool(self.github_repo.strip())
        has_url = bool(self.download_url.strip())

        if not has_github and not has_url:
            errors.append(f"{self.name or 'row'}: github_repo or download_url is required")
        if has_github and has_url:
            errors.append(f"{self.name}: specify either github_repo or download_url, not both")
        if not self.name.strip():
            errors.append("name is empty")
        elif "/" in self.name or self.name in (".", ".."):
            errors.append(f"{self.name}: invalid application name")
        if has_github:
            try:
                normalize_github_repo(self.github_repo)
            except ValueError as exc:
                errors.append(f"{self.name}: {exc}")
        if has_url:
            parsed = urllib.parse.urlparse(self.download_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(f"{self.name}: invalid download_url: {self.download_url!r}")
        return errors


@dataclass
class UpdateResult:
    name: str
    success: bool
    message: str
    skipped: bool = False


def normalize_github_repo(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("github_repo is empty")

    match = re.search(r"github\.com/([^/]+)/([^/#?]+)", value)
    if match:
        return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"

    if re.fullmatch(r"[^/]+/[^/]+", value):
        return value

    raise ValueError(f"invalid github_repo: {value!r}")


def parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_entries(csv_path: Path) -> Iterator[AppImageEntry]:
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            Logger.error("CSV file is empty or has no headers")
            return

        required = {"name"}
        missing = required - set(reader.fieldnames)
        if missing:
            Logger.error(f"CSV missing required columns: {', '.join(sorted(missing))}")
            return

        for row_num, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            github_repo = (row.get("github_repo") or "").strip()
            download_url = (row.get("download_url") or "").strip()
            if not name and not github_repo and not download_url:
                continue

            entry = AppImageEntry(
                github_repo=github_repo,
                name=name,
                download_url=download_url,
                asset_match=(row.get("asset_match") or "").strip(),
                enabled=parse_bool(row.get("enabled"), default=True),
            )
            errors = entry.validate()
            if errors:
                for error in errors:
                    Logger.warning(f"Row {row_num}: {error}")
                continue
            yield entry


def get_enabled_entries(csv_path: Path, repo_filter: str | None = None) -> Iterator[AppImageEntry]:
    filter_value = repo_filter.strip() if repo_filter else None
    normalized_github_filter = None
    if filter_value:
        try:
            normalized_github_filter = normalize_github_repo(filter_value)
        except ValueError:
            normalized_github_filter = None

    for entry in load_entries(csv_path):
        if not entry.enabled:
            Logger.dim(f"  Skipping disabled app: {entry.name}")
            continue

        if filter_value is not None:
            matches = entry.name == filter_value
            if not matches and entry.download_url:
                matches = entry.download_url == filter_value
            if not matches and entry.github_repo:
                try:
                    matches = normalize_github_repo(entry.github_repo) == normalized_github_filter
                except ValueError:
                    matches = entry.github_repo == filter_value
            if not matches:
                continue

        yield entry


def detect_ubuntu_asset_match() -> str:
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return "ubuntu22.04"

    version_id = ""
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION_ID="):
            version_id = line.split("=", 1)[1].strip().strip('"')
            break

    if version_id:
        return f"ubuntu{version_id}"
    return "ubuntu22.04"


ARCHITECTURE_MARKERS = ("x86_64", "amd64", "aarch64", "arm64", "i686", "armv7l", "riscv64")


def detect_system_arch_patterns() -> list[str]:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return ["x86_64", "amd64"]
    if machine in ("aarch64", "arm64"):
        return ["aarch64", "arm64"]
    return [machine]


def filter_assets_by_architecture(assets: list[dict]) -> list[dict]:
    """Prefer assets for the current CPU architecture when names include arch markers."""
    arch_patterns = detect_system_arch_patterns()
    with_arch: list[dict] = []
    without_arch: list[dict] = []

    for asset in assets:
        name_lower = asset["name"].lower()
        if any(marker in name_lower for marker in ARCHITECTURE_MARKERS):
            with_arch.append(asset)
        else:
            without_arch.append(asset)

    if not with_arch:
        return assets

    arch_matches = [
        asset for asset in with_arch
        if any(pattern in asset["name"].lower() for pattern in arch_patterns)
    ]
    if arch_matches:
        return arch_matches

    if without_arch:
        Logger.dim(
            f"  No {platform.machine()} arch-specific asset; using generic AppImage candidate(s)"
        )
        return without_arch

    raise RuntimeError(
        f"no AppImage asset for architecture {platform.machine()}; candidates: "
        + ", ".join(asset["name"] for asset in with_arch)
    )


def default_request_headers() -> dict[str, str]:
    headers = {"User-Agent": "update-appimages"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def load_github_token() -> None:
    """Load GITHUB_TOKEN from github_token.env when not already in the environment."""
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return
    if not GITHUB_TOKEN_FILE.is_file():
        return

    try:
        lines = GITHUB_TOKEN_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if not line.startswith("GITHUB_TOKEN="):
            continue
        token = line.split("=", 1)[1].strip().strip('"').strip("'")
        if token:
            os.environ["GITHUB_TOKEN"] = token
        return


def truncate_error_body(body: str, limit: int = 200) -> str:
    body = " ".join(body.split())
    if len(body) <= limit:
        return body
    return body[: limit - 3] + "..."


def urlopen_with_retries(
    request: urllib.request.Request,
    *,
    timeout: int = 60,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_sec: float = DEFAULT_RETRY_BACKOFF_SEC,
    verbose: bool = False,
):
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_HTTP_CODES or attempt >= max_retries:
                body = truncate_error_body(exc.read().decode("utf-8", errors="replace"))
                raise RuntimeError(
                    f"HTTP error {exc.code} for {request.full_url}: {body}"
                ) from exc

            wait = retry_backoff_sec * (2 ** (attempt - 1))
            Logger.warning(
                f"  HTTP {exc.code} from GitHub, retrying in {wait:.0f}s "
                f"({attempt}/{max_retries})..."
            )
            if verbose:
                Logger.dim(f"  URL: {request.full_url}")
            time.sleep(wait)
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= max_retries:
                raise RuntimeError(f"Network error for {request.full_url}: {exc}") from exc

            wait = retry_backoff_sec * (2 ** (attempt - 1))
            Logger.warning(
                f"  Network error, retrying in {wait:.0f}s ({attempt}/{max_retries}): {exc}"
            )
            time.sleep(wait)

    raise RuntimeError(f"Request failed for {request.full_url}: {last_error}")


def filename_from_response(url: str, headers) -> str:
    content_disposition = headers.get("Content-Disposition")
    if content_disposition:
        match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition, re.I)
        if match:
            return urllib.parse.unquote(match.group(1))

    filename = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])
    if filename:
        return filename
    return "download.AppImage"


def resolve_direct_download(url: str, verbose: bool = False) -> dict:
    request = urllib.request.Request(url, method="HEAD", headers=default_request_headers())
    try:
        with urlopen_with_retries(request, timeout=60, verbose=verbose) as response:
            final_url = response.geturl()
            filename = filename_from_response(final_url, response.headers)
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 405}:
            body = truncate_error_body(exc.read().decode("utf-8", errors="replace"))
            raise RuntimeError(f"Could not resolve download URL {url}: HTTP {exc.code}: {body}") from exc
        final_url = url
        filename = filename_from_response(url, exc.headers)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not resolve download URL {url}: {exc}") from exc

    if verbose:
        Logger.dim(f"  Resolved URL: {final_url}")
        Logger.dim(f"  Filename: {filename}")

    return {
        "name": filename,
        "browser_download_url": final_url,
        "version_label": filename.removesuffix(".AppImage").removesuffix(".appimage"),
    }


def github_request(url: str, verbose: bool = False) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        **default_request_headers(),
    }

    request = urllib.request.Request(url, headers=headers)
    with urlopen_with_retries(request, timeout=90, verbose=verbose) as response:
        return json.load(response)


def pick_appimage_asset(release: dict, asset_match: str) -> dict:
    assets = [
        asset for asset in release.get("assets", [])
        if asset.get("name", "").lower().endswith(".appimage")
    ]
    if not assets:
        raise RuntimeError("no AppImage assets found in latest release")

    assets = filter_assets_by_architecture(assets)
    if len(assets) == 1:
        Logger.dim(f"  Selected by architecture ({platform.machine()}): {assets[0]['name']}")
        return assets[0]

    def find_match(pattern: str) -> dict | None:
        pattern_lower = pattern.lower()
        matches = [asset for asset in assets if pattern_lower in asset["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(
                f"multiple AppImage assets match {pattern!r}: "
                + ", ".join(asset["name"] for asset in matches)
            )
        return None

    if asset_match:
        asset = find_match(asset_match)
        if asset is not None:
            return asset
        Logger.warning(f"No asset matched {asset_match!r}; trying fallbacks")

    auto_match = detect_ubuntu_asset_match()
    asset = find_match(auto_match)
    if asset is not None:
        return asset

    asset = find_match("ubuntu22.04")
    if asset is not None:
        Logger.warning(f"Using ubuntu22.04 fallback asset: {asset['name']}")
        return asset

    if len(assets) == 1:
        Logger.warning(f"Using only available AppImage asset: {assets[0]['name']}")
        return assets[0]

    raise RuntimeError(
        "could not choose AppImage asset; candidates: "
        + ", ".join(asset["name"] for asset in assets)
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_sha256_digest(asset: dict) -> str | None:
    digest = asset.get("digest", "")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest.removeprefix("sha256:")
    return None


def any_binary_matches_hash(binaries: list[Path], target_hash: str, verbose: bool = False) -> Path | None:
    for path in binaries:
        file_hash = sha256_file(path)
        if verbose:
            Logger.dim(f"  Compare {path.name}: {file_hash}")
        if file_hash == target_hash:
            return path
    return None


def download_asset(asset: dict, destination: Path, verbose: bool = False) -> None:
    download_from_url(asset["browser_download_url"], destination, verbose=verbose)


def download_from_url(url: str, destination: Path, verbose: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        Logger.dim(f"  Downloading {url}")

    request = urllib.request.Request(url, headers=default_request_headers())
    with urlopen_with_retries(request, timeout=300, verbose=verbose) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)

    destination.chmod(0o755)


def should_use_sandbox(desktop_file: Path) -> bool:
    if not desktop_file.is_file():
        return True

    for line in desktop_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("Exec="):
            continue
        exec_line = line[5:].strip()
        return "--no-sandbox" not in exec_line

    return True


def get_real_user() -> str:
    sudo_user = os.environ.get("SUDO_USER", "").strip()
    if sudo_user and sudo_user != "root":
        return sudo_user
    return os.environ.get("USER", "root")


def find_existing_icon(entry: AppImageEntry) -> Path | None:
    app_dir = entry.install_dir
    name = entry.name

    if entry.desktop_file.is_file():
        for line in entry.desktop_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("Icon="):
                continue
            raw = line[5:].strip()
            if not raw:
                break
            icon_path = Path(raw) if Path(raw).is_absolute() else app_dir / raw
            if icon_path.is_file():
                return icon_path
            break

    for ext in (".png", ".svg", ".xpm", ".ico"):
        candidate = app_dir / f"{name}{ext}"
        if candidate.is_file():
            return candidate
    return None


def write_desktop_entry(
    entry: AppImageEntry,
    dest_binary: Path,
    icon_path: Path,
    use_sandbox: bool,
) -> None:
    exec_line = (
        f"Exec={dest_binary}\n"
        if use_sandbox
        else f"Exec={dest_binary} --no-sandbox\n"
    )
    display_name = " ".join(
        word[:1].upper() + word[1:] if word else word for word in entry.name.split()
    )
    desktop_entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={display_name}\n"
        f"{exec_line}"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        f"StartupWMClass={entry.name.lower()}\n"
        "Categories=Utility;\n"
    )
    entry.desktop_file.write_text(desktop_entry)


def chown_recursive(path: Path, user: str) -> None:
    shutil.chown(path, user=user, group=user)
    if path.is_dir():
        for child in path.rglob("*"):
            shutil.chown(child, user=user, group=user)


def update_desktop_database() -> None:
    if shutil.which("update-desktop-database") is None:
        return
    subprocess.run(["update-desktop-database"], check=False)


def install_appimage_uses_canonical_name() -> bool:
    script = shutil.which("install-appimage")
    if not script:
        return False
    return "dest_appimage = app_dir / name" in Path(script).read_text(encoding="utf-8")


def install_canonical_appimage(
    entry: AppImageEntry,
    source_appimage: Path,
    dry_run: bool,
    verbose: bool,
) -> None:
    dest_binary = entry.installed_binary
    legacy_binary = entry.legacy_appimage()
    use_sandbox = should_use_sandbox(entry.desktop_file)
    source_appimage = source_appimage.resolve()

    if verbose or dry_run:
        Logger.dim(f"  Install to: {dest_binary}")
        if legacy_binary.is_file() and legacy_binary.resolve() != dest_binary.resolve():
            Logger.dim(f"  Remove legacy: {legacy_binary}")

    if dry_run:
        return

    if os.geteuid() != 0:
        raise RuntimeError("install must be run as root (use sudo)")

    entry.install_dir.mkdir(parents=True, exist_ok=True)

    if source_appimage.resolve() != dest_binary.resolve():
        shutil.copy2(source_appimage, dest_binary)
    dest_binary.chmod(0o755)

    if legacy_binary.is_file() and legacy_binary.resolve() != dest_binary.resolve():
        legacy_binary.unlink()

    icon_path = find_existing_icon(entry)
    if icon_path is None:
        raise RuntimeError(
            f"No icon found under {entry.install_dir}; install an icon before updating"
        )

    write_desktop_entry(entry, dest_binary, icon_path, use_sandbox)
    chown_recursive(entry.install_dir, get_real_user())
    update_desktop_database()
    Logger.dim(f"  Updated desktop entry at {entry.desktop_file}")


def normalize_install_layout(
    entry: AppImageEntry,
    source_appimage: Path,
    dry_run: bool,
    verbose: bool,
) -> None:
    Logger.dim(f"  Normalizing install layout to {entry.installed_binary}")
    install_canonical_appimage(entry, source_appimage, dry_run=dry_run, verbose=verbose)


def run_install_appimage(
    entry: AppImageEntry,
    appimage_path: Path,
    dry_run: bool,
    verbose: bool,
) -> None:
    use_sandbox = should_use_sandbox(entry.desktop_file)

    if install_appimage_uses_canonical_name():
        if shutil.which("install-appimage") is None:
            raise RuntimeError("install-appimage not found in PATH")

        command = ["install-appimage", "--name", entry.name]
        if use_sandbox:
            command.append("--sandbox")
        command.append(str(appimage_path))

        if verbose or dry_run:
            Logger.dim(f"  Command: {' '.join(command)}")

        if dry_run:
            return

        if os.geteuid() != 0:
            raise RuntimeError("install-appimage must be run as root (use sudo)")

        result = subprocess.run(command, capture_output=True, text=True)
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "install-appimage failed"
            raise RuntimeError(stderr)
        return

    install_canonical_appimage(entry, appimage_path, dry_run=dry_run, verbose=verbose)


def fetch_appimage_asset(entry: AppImageEntry, verbose: bool = False) -> tuple[dict, str]:
    if entry.uses_direct_download:
        asset = resolve_direct_download(entry.download_url, verbose=verbose)
        return asset, asset["version_label"]

    owner, repo = entry.owner_repo
    api_url = GITHUB_API.format(owner=owner, repo=repo)
    if verbose:
        Logger.dim(f"  API: {api_url}")

    release = github_request(api_url, verbose=verbose)
    tag_name = release.get("tag_name", "unknown")
    asset_match = entry.asset_match or detect_ubuntu_asset_match()
    asset = pick_appimage_asset(release, asset_match)
    return asset, tag_name


def update_app(entry: AppImageEntry, dry_run: bool = False, verbose: bool = False) -> UpdateResult:
    Logger.info(f"Processing: {entry.name} ({entry.source_label})")

    installed_binaries = entry.list_installed_binaries()
    if not installed_binaries:
        message = f"Not installed under {entry.install_dir}; skipping"
        Logger.warning(f"  {message}")
        return UpdateResult(entry.name, True, message, skipped=True)

    installed_binary = installed_binaries[0]
    asset, version_label = fetch_appimage_asset(entry, verbose=verbose)
    Logger.dim(f"  Release: {version_label}")
    Logger.dim(f"  Asset: {asset['name']}")
    if verbose:
        Logger.dim(f"  Installed binary: {installed_binary}")
        if len(installed_binaries) > 1:
            Logger.dim(
                "  Other installed candidates: "
                + ", ".join(path.name for path in installed_binaries[1:])
            )

    remote_digest = asset_sha256_digest(asset)
    if remote_digest:
        matched = any_binary_matches_hash(installed_binaries, remote_digest, verbose=verbose)
        if matched is not None:
            if entry.needs_layout_normalize(matched):
                normalize_install_layout(entry, matched, dry_run=dry_run, verbose=verbose)
                if dry_run:
                    return UpdateResult(
                        entry.name,
                        True,
                        f"[DRY RUN] Would normalize install layout to {entry.installed_binary}",
                    )
                return UpdateResult(
                    entry.name,
                    True,
                    f"Already up to date ({version_label}); normalized to {entry.name}",
                )

            message = f"Already up to date ({version_label})"
            Logger.success(f"  {message}")
            if verbose:
                Logger.dim(f"  Matched installed file: {matched}")
            return UpdateResult(entry.name, True, message, skipped=True)

    if dry_run:
        run_install_appimage(entry, Path(asset["name"]), dry_run=True, verbose=True)
        return UpdateResult(entry.name, True, f"[DRY RUN] Would update to {asset['name']}")

    download_path = DOWNLOAD_DIR / f"{entry.name}-{asset['name']}"
    download_asset(asset, download_path, verbose=verbose)

    downloaded_hash = sha256_file(download_path)
    if verbose:
        Logger.dim(f"  Download SHA256:  {downloaded_hash}")

    matched = any_binary_matches_hash(installed_binaries, downloaded_hash, verbose=verbose)
    if matched is not None:
        download_path.unlink(missing_ok=True)
        if entry.needs_layout_normalize(matched):
            normalize_install_layout(entry, matched, dry_run=False, verbose=verbose)
            message = f"Already up to date ({version_label}); normalized to {entry.name}"
            Logger.success(f"  {message}")
            return UpdateResult(entry.name, True, message)

        message = f"Already up to date ({version_label})"
        Logger.success(f"  {message}")
        if verbose:
            Logger.dim(f"  Matched installed file: {matched}")
        return UpdateResult(entry.name, True, message, skipped=True)

    if verbose:
        Logger.dim(f"  Sandbox flag: {'--sandbox' if should_use_sandbox(entry.desktop_file) else '(default --no-sandbox)'}")

    run_install_appimage(entry, download_path, dry_run=False, verbose=verbose)
    message = f"Updated to {version_label} ({asset['name']})"
    Logger.success(f"  {message}")
    return UpdateResult(entry.name, True, message)


def print_summary(results: list[UpdateResult]) -> None:
    if not results:
        Logger.warning("No applications were processed")
        return

    Logger.header("Update Summary")
    updated = skipped = failed = 0

    for result in results:
        if not result.success:
            status = "FAIL"
            failed += 1
            Logger.error(f"{result.name:<20}  {status:<8}  {result.message}")
        elif result.skipped:
            status = "SKIP"
            skipped += 1
            Logger.warning(f"{result.name:<20}  {status:<8}  {result.message}")
        else:
            status = "OK"
            updated += 1
            Logger.success(f"{result.name:<20}  {status:<8}  {result.message}")

    Logger.newline()
    Logger.info(
        f"Total: {len(results)} | Updated: {updated} | Skipped: {skipped} | Failed: {failed}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update AppImage applications from GitHub releases or direct URLs using appimages.csv.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
CSV format:
  github_repo,name,download_url,asset_match,enabled
  bambulab/BambuStudio,BambuStudio,,,true
  ,LMStudio,https://lmstudio.ai/download/latest/linux/x64,,true

Use github_repo for GitHub releases, or download_url for direct/latest links.
Provide exactly one of github_repo or download_url per row.

Examples:
  %(prog)s
  %(prog)s --dry-run
  %(prog)s --repo bambulab/BambuStudio
  %(prog)s --repo LMStudio
  %(prog)s --csv /opt/update-all/appimages.csv --verbose
        """,
    )
    parser.add_argument(
        "--csv", "-c",
        type=Path,
        default=DEFAULT_CSV,
        help="Path to appimages.csv (default: appimages.csv beside this script)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Resolve releases and show actions without downloading or installing",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed progress",
    )
    parser.add_argument(
        "--repo",
        help="Update only one CSV entry (github_repo slug, download_url, or App_Name)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_github_token()
    if args.no_color:
        Logger.use_colors = False

    Logger.header("AppImage Updater")

    if args.dry_run:
        Logger.warning("DRY RUN MODE - No downloads or installs will be performed")

    if not args.csv.exists():
        Logger.error(f"CSV file not found: {args.csv}")
        return 1

    if shutil.which("install-appimage") is None and not args.dry_run:
        Logger.error("install-appimage not found in PATH")
        return 1

    if os.geteuid() != 0 and not args.dry_run:
        Logger.error("This script must be run as root for installs (use sudo)")
        return 1

    Logger.info(f"Loading applications from: {args.csv}")
    Logger.newline()

    results: list[UpdateResult] = []
    count = 0

    try:
        entries = list(get_enabled_entries(args.csv, repo_filter=args.repo))
        if not os.environ.get("GITHUB_TOKEN", "").strip():
            github_count = sum(1 for entry in entries if not entry.uses_direct_download)
            if github_count > 1:
                Logger.dim(
                    "Tip: set GITHUB_TOKEN in the environment or in "
                    f"{GITHUB_TOKEN_FILE} (chmod 600) to reduce GitHub API issues"
                )

        for index, entry in enumerate(entries):
            count += 1
            try:
                results.append(update_app(entry, dry_run=args.dry_run, verbose=args.verbose))
            except Exception as exc:
                message = str(exc)
                Logger.error(f"  {message}")
                results.append(UpdateResult(entry.name, False, message))
            Logger.newline()

            if (
                index + 1 < len(entries)
                and not entry.uses_direct_download
                and not entries[index + 1].uses_direct_download
            ):
                time.sleep(GITHUB_API_DELAY_SEC)
    except FileNotFoundError as exc:
        Logger.error(str(exc))
        return 1
    except KeyboardInterrupt:
        Logger.warning("\nInterrupted by user")
        return 130

    if count == 0:
        if args.repo:
            Logger.warning(f"No enabled CSV entry matched --repo {args.repo!r}")
        else:
            Logger.warning("No enabled applications found in CSV file")
        return 0

    print_summary(results)
    failures = sum(1 for result in results if not result.success)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
