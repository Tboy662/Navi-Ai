"""Builds Navi for Windows: dist/Navi-Setup.exe, one file that installs Navi and asks for the keys.

    py -3.14 -m pip install -r requirements.txt pyinstaller
    py -3.14 packaging/build_windows.py

Steps: draws the icon, compiles Navi with PyInstaller (a folder, so Navi starts fast: nothing to unpack at launch),
checks no key from .env or navi_settings.json ended up in it, zips it, compiles the small setup program
(navi_setup.py) as one exe and glues the zip onto its end. Setup reads the zip straight out of its own file.
Your keys are never put in the build: only the files listed in DATA below are copied in.
Agent mode's Chromium isn't in it: Navi downloads it into agent_browser the first time agent mode opens a page.
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
SEP = os.pathsep
# files Navi reads at run time, copied next to Navi.exe. Nothing personal (no .env, settings, memory or recordings)
DATA = ["sounds", "minecraft_knowledge.md", "minecraft_examples.json", "voice_clone_worker.py", "COMMANDS.md",
        ".env.example", "vosk_model", "vosk_spk_model"]
OPTIONAL_DATA = {"vosk_model", "vosk_spk_model"}  # offline wake word models: Navi falls back to Google without them
EXCLUDE = ["matplotlib", "scipy", "pandas", "torch", "IPython", "pytest", "setuptools"]
SECRET_FILES = [".env", "navi_settings.json", "navi_memory.json", "google_token.json", "google_credentials.json"]


def run(args):
    print(">", " ".join(args[:6]), "..." if len(args) > 6 else "")
    subprocess.run(args, check=True, cwd=ROOT)


def make_icon(path):
    # Navi's orb: glowing blue rings, like the status orb on screen
    from PIL import Image, ImageDraw, ImageFilter
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((18, 18, 238, 238), fill=(59, 130, 246, 170))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)
    for pad, color in ((34, (30, 58, 138, 255)), (58, (37, 99, 235, 255)), (92, (147, 197, 253, 255)), (114, (239, 246, 255, 255))):
        d.ellipse((pad, pad, size - pad, size - pad), fill=color)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def secret_values():
    # the keys on this pc, only to make sure none of them got into the build. They are never printed
    values = []
    try:
        sys.path.insert(0, ROOT)
        import navi_setup
        values += [v for v in navi_setup.read_env(os.path.join(ROOT, ".env")).values() if len(v) >= 8]
    except Exception:
        pass
    try:
        with open(os.path.join(ROOT, "navi_settings.json"), encoding="utf-8") as f:
            token = json.load(f).get("hf_token", "")
        if len(token) >= 8:
            values.append(token)
    except (OSError, ValueError):
        pass
    defaults = {"imap.gmail.com", "you@gmail.com", "your-gmail-app-password", "yourschool.instructure.com"}
    return [v.encode() for v in values if v not in defaults]


def check_no_secrets(folder):
    secrets = secret_values()
    for root, _, files in os.walk(folder):
        for name in files:
            if name in SECRET_FILES or name.startswith("client_secret"):
                sys.exit(f"STOP: {name} is in the build. Keys must never be shipped.")
            if not secrets:
                continue
            with open(os.path.join(root, name), "rb") as f:
                blob = f.read()
            for i, s in enumerate(secrets):
                if s in blob:
                    sys.exit(f"STOP: key #{i + 1} from your .env/settings was found inside {os.path.relpath(os.path.join(root, name), folder)}.")
    print(f"checked: none of your {len(secrets)} keys are in the build")


def build_navi(icon):
    args = [sys.executable, "-m", "PyInstaller", "voice.py", "--name", "Navi", "--windowed", "--onedir",
            "--contents-directory", ".", "--icon", icon, "--noconfirm", "--clean",
            "--distpath", os.path.join(BUILD, "app"), "--workpath", os.path.join(BUILD, "work"), "--specpath", BUILD,
            "--hidden-import", "navi_setup", "--collect-binaries", "vosk", "--collect-data", "gtts",
            "--collect-data", "speech_recognition", "--collect-data", "playwright", "--collect-submodules", "edge_tts"]
    for name in DATA:
        src = os.path.join(ROOT, name)
        if not os.path.exists(src):
            if name in OPTIONAL_DATA:
                print(f"note: {name} is missing, the build won't have the offline wake word")
                continue
            sys.exit(f"missing {name}")
        args += ["--add-data", f"{src}{SEP}{name if os.path.isdir(src) else '.'}"]
    for mod in EXCLUDE:
        args += ["--exclude-module", mod]
    run(args)
    return os.path.join(BUILD, "app", "Navi")


def zip_folder(folder, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, folder).replace("\\", "/"))


def build_setup(icon, payload):
    run([sys.executable, "-m", "PyInstaller", "navi_setup.py", "--name", "Navi-Setup-stub", "--windowed", "--onefile",
         "--icon", icon, "--noconfirm", "--clean", "--distpath", os.path.join(BUILD, "setup"),
         "--workpath", os.path.join(BUILD, "work-setup"), "--specpath", BUILD]
        + sum((["--exclude-module", m] for m in EXCLUDE + ["numpy", "PIL", "playwright"]), []))
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "Navi-Setup.exe")
    with open(out, "wb") as dst:
        for part in (os.path.join(BUILD, "setup", "Navi-Setup-stub.exe"), payload):
            with open(part, "rb") as src:
                shutil.copyfileobj(src, dst, 1 << 20)
    code = subprocess.run([out, "--selftest"]).returncode
    if code != 0:
        sys.exit(f"STOP: the finished setup couldn't read its own files (selftest exit {code})")
    return out


def main():
    os.makedirs(BUILD, exist_ok=True)
    icon = os.path.join(BUILD, "navi.ico")
    make_icon(icon)
    app = build_navi(icon)
    check_no_secrets(app)
    payload = os.path.join(BUILD, "payload.zip")
    zip_folder(app, payload)
    out = build_setup(icon, payload)
    print(f"\nDone: {out} ({os.path.getsize(out) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
