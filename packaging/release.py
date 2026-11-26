"""Publishes a new Navi version on GitHub, so installed copies with auto-update on pick it up.

    1. Raise VERSION in navi_setup.py (like 1.0.0 -> 1.0.1), commit it and push it to main.
    2. py -3.14 packaging/release.py            builds dist/Navi-Setup.exe, writes dist/navi-update.json and uploads both
       py -3.14 packaging/release.py --no-build   uses the Navi-Setup.exe already in dist

Uploading needs a GitHub token, asked for when the script runs (or set NAVI_RELEASE_TOKEN for that one terminal).
Make it at github.com/settings/personal-access-tokens: a fine-grained token, only the Navi-Ai repository,
permission Contents: Read and write. It's used for this upload only, never saved or printed.
Without a token, press Enter at the prompt and the script says how to upload the two files on github.com by hand.

navi-update.json holds the version, the file name, its size and its SHA-256. Navi's updater (updater.py) installs an
update only when the download matches it exactly (and matches the hash GitHub records for the upload).
"""
import getpass
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
sys.path.insert(0, ROOT)
try:
    import truststore  # the Windows certificate store, like Navi itself (needed with antivirus https scanning)
    truststore.inject_into_ssl()
except ImportError:
    pass
import navi_setup  # noqa: E402
import updater  # noqa: E402

REPO = updater.REPO
API = f"https://api.github.com/repos/{REPO}"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def sha256(path):
    return updater.file_sha256(path)


def api(method, url, token, body=None, data=None, content_type="application/json"):
    host = urllib.parse.urlsplit(url).hostname
    if host not in ("api.github.com", "uploads.github.com") or not url.startswith("https://"):
        sys.exit("refusing to send the token to " + str(host))
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": content_type,
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Navi-release"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"GitHub said {e.code} to {method} {url.split('?')[0]}: {detail}")


def main():
    argv = sys.argv[1:]
    version = navi_setup.VERSION
    if not updater.parse_version(version):
        sys.exit(f"VERSION in navi_setup.py ({version}) must look like 1.2.3")
    tag = "v" + version
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("Commit your changes first, so the release matches a commit on GitHub.")
    head = git("rev-parse", "HEAD")
    git("fetch", "origin", "main", "--tags")
    if subprocess.run(["git", "merge-base", "--is-ancestor", head, "origin/main"], cwd=ROOT).returncode != 0:
        sys.exit("Push this commit to main first (git push), so the release points at code that's on GitHub.")
    if git("tag", "--list", tag):
        sys.exit(f"{tag} already exists. Raise VERSION in navi_setup.py for a new release.")

    setup = os.path.join(DIST, updater.SETUP_NAME)
    if "--no-build" not in argv:
        subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "build_windows.py")], cwd=ROOT, check=True)
    if not os.path.isfile(setup):
        sys.exit(f"{setup} isn't there. Run without --no-build.")
    info = {"version": version, "file": updater.SETUP_NAME, "size": os.path.getsize(setup), "sha256": sha256(setup),
            "commit": head}
    info_path = os.path.join(DIST, updater.INFO_NAME)
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"\nNavi {version}: {updater.SETUP_NAME}, {info['size'] / 1e6:.0f} MB, SHA-256 {info['sha256']}")

    token = os.environ.get("NAVI_RELEASE_TOKEN") or getpass.getpass(
        "GitHub token to upload the release (Enter to upload by hand instead): ").strip()
    if not token:
        print(f"\nUpload by hand:\n  1. Open https://github.com/{REPO}/releases/new\n"
              f"  2. Tag: {tag} (target: main), title: Navi {version}\n"
              f"  3. Attach both files from {DIST}: {updater.SETUP_NAME} and {updater.INFO_NAME}\n"
              "  4. Leave 'Set as a pre-release' unticked and press Publish release.")
        return 0

    notes = f"Navi {version}.\n\nSHA-256 of {updater.SETUP_NAME}: `{info['sha256']}`"
    release = api("POST", f"{API}/releases", token, {"tag_name": tag, "target_commitish": head, "name": f"Navi {version}",
                                                      "body": notes, "draft": True, "prerelease": False})
    upload = release["upload_url"].split("{")[0]
    for path, kind in ((setup, "application/octet-stream"), (info_path, "application/json")):
        name = os.path.basename(path)
        print(f"uploading {name}...")
        with open(path, "rb") as f:
            asset = api("POST", f"{upload}?name={urllib.parse.quote(name)}", token, data=f.read(), content_type=kind)
        digest = asset.get("digest") or ""
        if digest and digest != "sha256:" + sha256(path):
            api("DELETE", f"{API}/releases/{release['id']}", token)
            sys.exit(f"GitHub's hash of {name} doesn't match the file, so the release was deleted. Try again.")
    # published only once both files are up, so no installed Navi ever sees a half-uploaded release
    done = api("PATCH", f"{API}/releases/{release['id']}", token, {"draft": False, "make_latest": "true"})
    print(f"\nPublished: {done.get('html_url')}")
    found = updater.check("0.0.0")
    print("Check from an installed Navi's point of view:", f"sees {found['version']}, hashes match" if found else "nothing yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
