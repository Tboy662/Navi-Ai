"""Navi's auto-update: finds a newer Navi on GitHub, downloads it, checks it and installs it.

Off unless "Install updates automatically" is ticked in Settings > Updates (or you press Check now there).

What it trusts, and nothing else:
  * Only HTTPS, with the certificate checked, and only these hosts: api.github.com, github.com and GitHub's
    download hosts (a redirect anywhere else is refused).
  * Only releases of Tboy662/Navi-Ai, the newest non-draft, non-prerelease one, tagged vMAJOR.MINOR.PATCH.
  * Each release carries navi-update.json, written by packaging/release.py: the version, the file name, its size and
    its SHA-256. The download must match that size and hash exactly, and the hash GitHub itself records for the file
    when there is one, or it's deleted and never run.
  * Only a newer version than the one running. The installer double-checks that too, so it never downgrades Navi.
The installer is then run as "Navi-Setup.exe --update": it swaps in the new program files, keeps your keys, settings
and data, and opens Navi again.
"""
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "Tboy662/Navi-Ai"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
SETUP_NAME = "Navi-Setup.exe"
INFO_NAME = "navi-update.json"
TAG_RE = re.compile(r"v([0-9]{1,4})\.([0-9]{1,4})\.([0-9]{1,4})")  # always fullmatch: ASCII digits, no trailing newline
SHA_RE = re.compile(r"[0-9a-f]{64}")
MAX_JSON = 1 << 20  # 1 MB: the release list and navi-update.json are tiny
MAX_SETUP = 1 << 30  # 1 GB: Navi-Setup.exe is about 160 MB
CHECK_EVERY = 24 * 3600
TIMEOUT = 30
FROZEN = getattr(sys, "frozen", False)
IS_WINDOWS = sys.platform == "win32"


class UpdateError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code  # the http status, when GitHub answered with an error


def parse_version(text):
    # "1.2.3" or "v1.2.3" -> (1, 2, 3); None for anything else
    m = TAG_RE.fullmatch(text if str(text).startswith("v") else "v" + str(text))
    return tuple(int(p) for p in m.groups()) if m else None


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


# ---------------------------------------------------------------------------------------------------------------
# HTTPS that only goes to GitHub
# ---------------------------------------------------------------------------------------------------------------
def check_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or (p.hostname or "").lower() not in ALLOWED_HOSTS or p.port not in (None, 443) or p.username:
        raise UpdateError("refused to connect to " + (p.hostname or url))
    return url


class _GitHubOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(urllib.parse.urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener():
    ctx = ssl.create_default_context()  # checks the certificate and host name (Navi injects the Windows store)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _GitHubOnlyRedirects())


def _open(url, accept):
    req = urllib.request.Request(check_url(url), headers={"Accept": accept, "User-Agent": "Navi-updater"})
    try:
        resp = _opener().open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub answered {e.code}", e.code) from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(f"couldn't reach GitHub ({getattr(e, 'reason', e)})") from None
    check_url(resp.geturl())  # belt and braces: where we actually ended up
    return resp


def _read_limited(resp, limit):
    data = resp.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("answer too big")
    return data


def get_json(url, accept="application/vnd.github+json"):
    with _open(url, accept) as resp:
        try:
            return json.loads(_read_limited(resp, MAX_JSON).decode("utf-8"))
        except ValueError:
            raise UpdateError("GitHub's answer wasn't readable") from None


# ---------------------------------------------------------------------------------------------------------------
# checking
# ---------------------------------------------------------------------------------------------------------------
def _asset(release, name):
    for a in release.get("assets") or []:
        if isinstance(a, dict) and a.get("name") == name and a.get("state", "uploaded") == "uploaded":
            return a
    return None


def check(current_version):
    """Returns the newer release as a dict (version, url, size, sha256, notes), or None when Navi is up to date."""
    try:
        release = get_json(LATEST_URL)
    except UpdateError as e:
        if e.code == 404:  # no release published yet
            return None
        raise
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not TAG_RE.fullmatch(tag):
        raise UpdateError("the newest release has an odd version name")
    version = tag[1:]
    if not is_newer(version, current_version):
        return None
    setup, info_asset = _asset(release, SETUP_NAME), _asset(release, INFO_NAME)
    if not setup or not info_asset:
        raise UpdateError(f"version {version} is missing {SETUP_NAME} or {INFO_NAME}")
    # only the standard download links for this repo and tag: the names are fixed, so the urls are too
    base = DOWNLOAD_PREFIX + tag + "/"
    info = get_json(base + INFO_NAME, accept="application/octet-stream")
    if not isinstance(info, dict):
        raise UpdateError(f"{INFO_NAME} isn't readable")
    sha = str(info.get("sha256", "")).lower()
    size = info.get("size")
    if info.get("version") != version or info.get("file") != SETUP_NAME:
        raise UpdateError(f"{INFO_NAME} is for a different version or file")
    if not SHA_RE.fullmatch(sha) or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_SETUP:
        raise UpdateError(f"{INFO_NAME} has no usable hash or size")
    if setup.get("size") != size:
        raise UpdateError("the download's size doesn't match the published size")
    digest = setup.get("digest")  # GitHub's own hash of the file, "sha256:..." (older releases don't have one)
    if isinstance(digest, str) and digest.startswith("sha256:") and digest[7:].lower() != sha:
        raise UpdateError("GitHub's hash of the download doesn't match the published hash")
    notes = release.get("body") if isinstance(release.get("body"), str) else ""
    return {"version": version, "url": base + SETUP_NAME, "size": size, "sha256": sha, "notes": notes[:2000]}


# ---------------------------------------------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------------------------------------------
def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(update, folder, progress=lambda fraction: None):
    """Downloads Navi-Setup.exe into folder and checks it. Returns its path; a bad download is deleted."""
    url, size, sha = update["url"], update["size"], update["sha256"]
    if not url.startswith(DOWNLOAD_PREFIX) or not SHA_RE.fullmatch(sha) or not parse_version(update["version"]):
        raise UpdateError("bad update details")
    os.makedirs(folder, exist_ok=True)
    final = os.path.join(folder, f"Navi-Setup-{update['version']}.exe")
    if os.path.isfile(final) and os.path.getsize(final) == size and file_sha256(final) == sha:
        return final  # already downloaded and checked
    part = final + ".part"
    h, done = hashlib.sha256(), 0
    try:
        with _open(url, "application/octet-stream") as resp, open(part, "wb") as out:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                done += len(block)
                if done > size:
                    raise UpdateError("the download is bigger than it should be")
                h.update(block)
                out.write(block)
                progress(done / size)
        if done != size:
            raise UpdateError("the download stopped early")
        if h.hexdigest() != sha:
            raise UpdateError("the download doesn't match its published hash, so it was thrown away")
        os.replace(part, final)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    write_pending(folder, {"version": update["version"], "file": os.path.basename(final), "sha256": sha, "size": size})
    return final


# ---------------------------------------------------------------------------------------------------------------
# a downloaded update waiting to be installed
# ---------------------------------------------------------------------------------------------------------------
PENDING = "pending.json"


def write_pending(folder, data):
    with open(os.path.join(folder, PENDING), "w", encoding="utf-8") as f:
        json.dump(data, f)


def pending(folder, current_version):
    """The checked installer waiting in folder (a path), or None. Old or broken leftovers are cleaned up."""
    try:
        with open(os.path.join(folder, PENDING), encoding="utf-8") as f:
            data = json.load(f)
        name = data["file"]
        if data.get("tried"):  # an install of it already started once and Navi is still older: don't loop on it
            raise ValueError("tried")
        if not (isinstance(name, str) and re.fullmatch(r"Navi-Setup-[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}\.exe", name)
                and name == f"Navi-Setup-{data['version']}.exe" and is_newer(data["version"], current_version)):
            raise ValueError("stale")
        path = os.path.join(folder, name)
        if os.path.getsize(path) != data["size"] or file_sha256(path) != str(data["sha256"]).lower():
            raise ValueError("changed")
        return path
    except (OSError, ValueError, KeyError, TypeError):
        clean(folder)
        return None


def clean(folder):
    # removes downloaded installers (after an update, or when they're broken or old)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name == PENDING or name.startswith("Navi-Setup-"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def install(path, install_folder):
    """Starts the checked installer in update mode. Navi should quit right after (the installer closes it anyway)."""
    if not IS_WINDOWS:
        raise UpdateError("updates install on Windows only")
    folder = os.path.dirname(path)
    try:
        with open(os.path.join(folder, PENDING), encoding="utf-8") as f:
            data = json.load(f)
        data["tried"] = True  # one try per download, so a broken update can't restart Navi over and over
        write_pending(folder, data)
    except (OSError, ValueError, TypeError):
        raise UpdateError("the downloaded update's details are missing") from None
    subprocess.Popen([path, "--update"], cwd=install_folder, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def can_self_update(app_folder, install_folder):
    # only the installed Navi.exe updates itself. A copy run from source (python voice.py) uses git instead
    if not (FROZEN and IS_WINDOWS):
        return False
    same = os.path.normcase(os.path.realpath(app_folder)) == os.path.normcase(os.path.realpath(install_folder))
    return same and os.path.isfile(os.path.join(install_folder, "navi_install.json"))


def install_pending_at_start(app_folder, install_folder, current_version, settings_file):
    """When Navi opens: if auto-update is on and a checked update is waiting, starts installing it and returns True
    (Navi then quits and the installer opens the new one). Returns False to start Navi normally."""
    if not can_self_update(app_folder, install_folder):
        return False
    try:
        with open(settings_file, encoding="utf-8") as f:
            if json.load(f).get("auto_update") is not True:
                return False
    except (OSError, ValueError, AttributeError):
        return False
    path = pending(os.path.join(install_folder, "updates"), current_version)
    if not path:
        return False
    try:
        install(path, install_folder)
    except (UpdateError, OSError) as e:
        print("update err; {0}".format(e))
        return False
    return True
