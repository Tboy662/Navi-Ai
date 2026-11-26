"""Navi's setup: the key wizard, the Windows installer and the uninstaller.

    Navi-Setup.exe                     installs Navi on Windows and asks for each key (writes .env)
    Navi.exe --keys                    asks for the keys again (also: python voice.py --keys)
    Navi.exe --uninstall               removes Navi from this PC
    python navi_setup.py --keys --dir DIR   the key wizard for a source install (the Mac installer uses this)

Only the standard library and tkinter, so it opens fast. On Windows the program files travel as a zip appended to
Navi-Setup.exe (packaging/build_windows.py makes it); it starts unpacking while you type your keys.
Keys are only ever written to .env in the install folder: never into the program, the zip or git.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

APP_NAME = "Navi"
VERSION = "1.0.0"
PUBLISHER = "Tboy662"
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)
MANIFEST = "navi_install.json"  # the program files a Windows install put down (everything else is the user's data)
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Navi"


def install_dir():
    # where Navi lives: per user, so installing needs no admin rights and nothing is shared with other accounts
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(base, "Programs", APP_NAME)
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/Navi/app")
    return os.path.expanduser("~/.local/share/navi/app")


INSTALL_DIR = install_dir()
SAVED_DIR = INSTALL_DIR + " (saved settings)"  # an uninstall that keeps your keys moves them here; setup moves them back


def app_dir():
    # the folder of the running Navi (where voice.py looks for .env)
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------------------------------------------
# the keys: one page each, with where to get them
# ---------------------------------------------------------------------------------------------------------------
# field: (.env name, label, secret, check) where check(value) returns a warning or ""
def _prefix(*starts, name="key"):
    def check(v):
        return "" if not v or v.startswith(starts) else f"That doesn't look like a {name} (they start with {starts[0]})."
    return check


def _gmail(v):
    return "" if not v or ("@" in v and "." in v.split("@")[-1]) else "That doesn't look like an email address."


def _app_password(v):
    letters = v.replace(" ", "")
    return "" if not v or (len(letters) == 16 and letters.isalpha()) else \
        "Gmail app passwords are 16 letters (like abcd efgh ijkl mnop). Your normal Gmail password won't work."


def _canvas_url(v):
    return "" if not v or ("." in v and " " not in v) else "Type your school's Canvas address, like yourschool.instructure.com."


PAGES = [
    {"title": "AI brain (Groq)", "optional": False,
     "about": "Navi's main AI model runs on Groq: it's fast and free (about 1000 requests a day). This powers AI chat, "
              "agent mode and smarter answers.",
     "steps": ["Sign in at console.groq.com (a Google account works).", "Open API Keys and press Create API Key.",
               "Copy the key (starts with gsk_) and paste it below."],
     "links": [("Get a Groq key", "https://console.groq.com/keys")],
     "fields": [("GROQ_API_KEY", "Groq API key", True, _prefix("gsk_", name="Groq key"))]},
    {"title": "Backup AI (Google Gemini)", "optional": True,
     "about": "Gemini is used when Groq is busy, and for questions you type in the window. Free with a Google account.",
     "steps": ["Open Google AI Studio and sign in.", "Press Create API key.", "Copy it (starts with AIza) and paste it below."],
     "links": [("Get a Gemini key", "https://aistudio.google.com/apikey")],
     "fields": [("GEMINI_API_KEY", "Gemini API key", True, _prefix("AIza", name="Gemini key"))]},
    {"title": "More backup AI (OpenRouter)", "optional": True,
     "about": "OpenRouter gives Navi a few extra free models for when the others run out. Skip it if you like.",
     "steps": ["Sign in at openrouter.ai.", "Open Keys and press Create Key.", "Copy it (starts with sk-or-) and paste it below."],
     "links": [("Get an OpenRouter key", "https://openrouter.ai/keys")],
     "fields": [("OPENROUTER_API_KEY", "OpenRouter key", True, _prefix("sk-or-", name="OpenRouter key"))]},
    {"title": "Email (Gmail)", "optional": True,
     "about": "Lets Navi read your unread mail and send email for you. It needs a Gmail app password, a separate "
              "16-letter password just for Navi. Your real password is never used.",
     "steps": ["Turn on 2-Step Verification for your Google account (first link).",
               "Create an app password named Navi (second link) and copy the 16 letters.",
               "In Gmail settings, under Forwarding and POP/IMAP, make sure IMAP is on."],
     "links": [("2-Step Verification", "https://myaccount.google.com/signinoptions/twosv"),
               ("Create an app password", "https://myaccount.google.com/apppasswords"),
               ("Gmail IMAP setting", "https://mail.google.com/mail/u/0/#settings/fwdandpop")],
     "fields": [("MAIL_USER", "Gmail address", False, _gmail),
                ("MAIL_PASSWORD", "App password", True, _app_password)]},
    {"title": "Homework (Canvas)", "optional": True,
     "about": "Lets Navi tell you what homework is due and how your grades look.",
     "steps": ["Type your school's Canvas address below (what you see in the browser, like yourschool.instructure.com).",
               "Press Open my Canvas settings, scroll to Approved Integrations and press New Access Token.",
               "Name it Navi, leave the date empty, generate it, and paste the token below."],
     "links": [("Open my Canvas settings", "canvas:")],
     "fields": [("CANVAS_URL", "Canvas address", False, _canvas_url),
                ("CANVAS_API_KEY", "Access token", True, None)]},
    {"title": "Places and restaurants (Geoapify)", "optional": True,
     "about": "Used to find restaurants and places near you. Free for personal use.",
     "steps": ["Sign up at Geoapify and create a project.", "Copy the project's API key and paste it below."],
     "links": [("Get a Geoapify key", "https://myprojects.geoapify.com/")],
     "fields": [("GEOAPIFY_API_KEY", "Geoapify API key", True, None)]},
    {"title": "Your own voice (Hugging Face)", "optional": True,
     "about": "Only needed if you want Navi to speak in a voice you train in Settings > Voice training. Its voice "
              "model is free but needs a Hugging Face account.",
     "steps": ["Sign up at huggingface.co.", "Open the pocket-tts model page and accept its conditions.",
               "Under Settings > Access Tokens make a Read token and paste it below (starts with hf_)."],
     "links": [("Accept the model's conditions", "https://huggingface.co/kyutai/pocket-tts"),
               ("Make a token", "https://huggingface.co/settings/tokens")],
     "fields": [("HF_TOKEN", "Hugging Face token", True, _prefix("hf_", name="Hugging Face token"))]},
    {"title": "Calendar (Google)", "optional": True,
     "about": "Lets Navi read and add events in Google Calendar. This one is a file instead of a key, and it takes a "
              "few minutes in Google Cloud. You can also skip it and drop the file in Navi's folder later.",
     "steps": ["In Google Cloud make a project and turn on the Google Calendar API (first link).",
               "Under Credentials press Create credentials > OAuth client ID > Desktop app.",
               "Download the JSON file and pick it below. Navi asks you to sign in the first time you use it."],
     "links": [("Turn on the Calendar API", "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"),
               ("Make the OAuth client", "https://console.cloud.google.com/apis/credentials")],
     "file": "google_credentials.json"},
]
ENV_ORDER = ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "MAIL_USER", "MAIL_PASSWORD", "IMAP_SERVER",
             "IMAP_PORT", "CANVAS_URL", "CANVAS_API_KEY", "GEOAPIFY_API_KEY", "HF_TOKEN"]
ENV_DEFAULTS = {"IMAP_SERVER": "imap.gmail.com", "IMAP_PORT": "993"}


# ---------------------------------------------------------------------------------------------------------------
# .env reading and writing (the same format python-dotenv reads)
# ---------------------------------------------------------------------------------------------------------------
def read_env(path):
    values = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except OSError:
        return values
    for line in lines:
        s = line.strip()
        if s.startswith("export "):
            s = s[7:].strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            quote, value = value[0], value[1:-1]
            if quote == "'":
                value = value.replace("\\'", "'").replace("\\\\", "\\")
            else:
                value = value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def env_line(key, value):
    value = value.replace("\r", "").replace("\n", "")  # one line per key
    if not value:
        return f"{key}="
    return "%s='%s'" % (key, value.replace("\\", "\\\\").replace("'", "\\'"))  # single quotes: taken literally


def write_env(path, values):
    # rewrites .env with these values. Keys it doesn't know about (that you added yourself) are kept
    old = read_env(path)
    merged = dict(old)
    merged.update(values)
    for key, default in ENV_DEFAULTS.items():
        merged.setdefault(key, default)
    lines = ["# Navi's keys, written by Navi's setup. Change them with Navi key setup, or edit this file.",
             "# Keep this file private: anyone with it can use your accounts."]
    lines += [env_line(k, merged.get(k, "")) for k in ENV_ORDER]
    lines += [env_line(k, v) for k, v in merged.items() if k not in ENV_ORDER]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # only you can read it (on mac and linux)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    if not IS_WINDOWS:
        os.chmod(path, 0o600)


# ---------------------------------------------------------------------------------------------------------------
# windows helpers
# ---------------------------------------------------------------------------------------------------------------
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def powershell(script, env_extra):
    # runs a short powershell script. Paths and names are passed in environment variables, never pasted into it
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                          env=dict(os.environ, **env_extra), capture_output=True, text=True, timeout=60,
                          creationflags=NO_WINDOW)


def special_folder(csidl):
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
    return buf.value


def shortcut_paths():
    programs = special_folder(0x02)  # CSIDL_PROGRAMS: the start menu
    startup = special_folder(0x07)   # CSIDL_STARTUP: what opens when you sign in (voice.py uses the same file)
    desktop = special_folder(0x10)   # CSIDL_DESKTOPDIRECTORY (follows onedrive)
    return {"menu": os.path.join(programs, "Navi.lnk"), "keys": os.path.join(programs, "Navi key setup.lnk"),
            "uninstall": os.path.join(programs, "Uninstall Navi.lnk"),
            "desktop": os.path.join(desktop, "Navi.lnk"), "startup": os.path.join(startup, "Navi.lnk")}


def make_shortcuts(links):
    # links: list of dicts with link, target, args, dir, desc
    script = ("$w=New-Object -ComObject WScript.Shell; foreach($x in (ConvertFrom-Json $env:NAVI_LINKS)){"
              "$s=$w.CreateShortcut($x.link); $s.TargetPath=$x.target; $s.Arguments=$x.args; $s.WorkingDirectory=$x.dir;"
              "$s.Description=$x.desc; $s.IconLocation=$x.target+',0'; $s.Save()}")
    result = powershell(script, {"NAVI_LINKS": json.dumps(links)})
    if result.returncode != 0:
        raise OSError("couldn't make the shortcuts: " + (result.stderr or "").strip()[:300])


def stop_running_navi(folder):
    # closes Navi if it's running from this folder (but never this process: the uninstaller is Navi.exe too)
    if not IS_WINDOWS:
        return
    script = ("$p=Get-Process -Name Navi -ErrorAction SilentlyContinue | Where-Object { $_.Path -and "
              "$_.Path.StartsWith($env:NAVI_DIR, [StringComparison]::OrdinalIgnoreCase) -and $_.Id -ne [int]$env:NAVI_PID };"
              "if($p){ $p | Stop-Process -Force; $p | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue }")
    powershell(script, {"NAVI_DIR": os.path.join(folder, ""), "NAVI_PID": str(os.getpid())})


def register_uninstaller(folder, size_kb):
    import winreg
    exe = os.path.join(folder, "Navi.exe")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in {"DisplayName": "Navi voice assistant", "DisplayVersion": VERSION, "Publisher": PUBLISHER,
                            "InstallLocation": folder, "DisplayIcon": exe + ",0",
                            "UninstallString": f'"{exe}" --uninstall', "QuietUninstallString": f'"{exe}" --uninstall'}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in {"NoModify": 1, "NoRepair": 1, "EstimatedSize": int(size_kb)}.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def unregister_uninstaller():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------------------------
# installing the program files
# ---------------------------------------------------------------------------------------------------------------
def open_payload():
    # the program files: a zip glued to the end of Navi-Setup.exe (zipfile finds it from the end of the file)
    try:
        zf = zipfile.ZipFile(sys.executable)
        return zf if zf.namelist() else None
    except (OSError, zipfile.BadZipFile):
        return None


def safe_join(base, name):
    # a path inside base, or an error (a zip entry can't write outside the install folder)
    path = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([path, os.path.realpath(base)]) != os.path.realpath(base):
        raise ValueError("bad path in the setup file: " + name)
    return path


def unpack(zf, dest, progress=lambda f: None):
    infos = [i for i in zf.infolist() if not i.is_dir()]
    total = sum(i.file_size for i in infos) or 1
    done = 0
    for info in infos:
        target = safe_join(dest, info.filename)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        done += info.file_size
        progress(done / total)
    return [i.filename for i in infos]


def read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def remove_empty_dirs(folder, keep_root=True):
    for root, dirs, files in os.walk(folder, topdown=False):
        if root == folder and keep_root:
            continue
        try:
            os.rmdir(root)  # only works when empty
        except OSError:
            pass


def move_tree_contents(src, dest):
    # moves everything in src into dest (instant on the same drive), replacing what's there
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dest, name)
        if os.path.isdir(s) and os.path.isdir(d):
            move_tree_contents(s, d)
            shutil.rmtree(s, ignore_errors=True)
        else:
            if os.path.isdir(d):
                shutil.rmtree(d)
            elif os.path.exists(d):
                os.remove(d)
            os.replace(s, d) if os.path.splitdrive(s)[0].lower() == os.path.splitdrive(d)[0].lower() else shutil.move(s, d)


def finish_install(folder, staged, files, options, env_values, calendar_file):
    """Swaps the unpacked files into place and sets up shortcuts. Your keys and data in the folder are kept."""
    stop_running_navi(folder)
    old = read_manifest(folder)
    new_files = set(f.replace("\\", "/") for f in files)
    if old:
        for rel in old.get("files", []):
            if rel.replace("\\", "/") not in new_files:
                try:
                    os.remove(safe_join(folder, rel))
                except (OSError, ValueError):
                    pass
    if os.path.isdir(SAVED_DIR):  # settings kept by an earlier uninstall come back
        move_tree_contents(SAVED_DIR, folder)
        shutil.rmtree(SAVED_DIR, ignore_errors=True)
    move_tree_contents(staged, folder)
    shutil.rmtree(staged, ignore_errors=True)
    remove_empty_dirs(folder)
    with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "files": sorted(new_files)}, f, indent=0)
    write_env(os.path.join(folder, ".env"), env_values)
    if calendar_file:
        shutil.copyfile(calendar_file, os.path.join(folder, "google_credentials.json"))
    exe = os.path.join(folder, "Navi.exe")
    paths = shortcut_paths()
    base = {"target": exe, "dir": folder}
    links = [dict(base, link=paths["menu"], args="", desc="Navi voice assistant"),
             dict(base, link=paths["keys"], args="--keys", desc="Change Navi's keys"),
             dict(base, link=paths["uninstall"], args="--uninstall", desc="Remove Navi")]
    for key in ("desktop", "startup"):  # the two shortcuts you can tick on the first page
        if options.get(key):
            links.append(dict(base, link=paths[key], args="", desc="Navi voice assistant"))
        elif os.path.exists(paths[key]):
            os.remove(paths[key])
    make_shortcuts(links)
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(folder) for f in fs)
    register_uninstaller(folder, size // 1024)
    return exe


# ---------------------------------------------------------------------------------------------------------------
# uninstalling
# ---------------------------------------------------------------------------------------------------------------
def uninstall_windows(folder, keep_data):
    manifest = read_manifest(folder)
    if manifest is None:
        raise OSError(f"{folder} doesn't look like a Navi install, so nothing was removed.")
    stop_running_navi(folder)
    paths = shortcut_paths()
    for key in ("menu", "keys", "uninstall", "desktop", "startup"):
        try:
            if os.path.exists(paths[key]):
                os.remove(paths[key])
        except OSError:
            pass
    unregister_uninstaller()
    if keep_data:  # everything that isn't a program file is yours: move it aside for a later install
        program = set(os.path.normcase(os.path.normpath(p)) for p in manifest.get("files", [])) | {MANIFEST.lower()}
        os.makedirs(SAVED_DIR, exist_ok=True)
        for root, dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, folder)
                if os.path.normcase(rel) not in program:
                    target = os.path.join(SAVED_DIR, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    try:
                        os.replace(full, target)
                    except OSError:
                        pass
    # this program may be running from that folder, so a tiny script removes it once we've closed
    script = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), f"navi_uninstall_{os.getpid()}.cmd")
    with open(script, "w", encoding="utf-8") as f:
        f.write('@echo off\r\nfor /l %%i in (1,1,30) do (\r\n  if not exist "%NAVI_DIR%" goto done\r\n'
                '  rmdir /s /q "%NAVI_DIR%" 2>nul\r\n  if exist "%NAVI_DIR%" ping -n 2 127.0.0.1 >nul\r\n)\r\n'
                ':done\r\n(goto) 2>nul & del "%~f0"\r\n')
    subprocess.Popen(["cmd.exe", "/c", script], env=dict(os.environ, NAVI_DIR=folder), cwd=os.path.dirname(script),
                     creationflags=NO_WINDOW | 0x00000008, close_fds=True)  # DETACHED_PROCESS


def run_uninstall():
    root = tk.Tk()
    root.withdraw()
    if not IS_WINDOWS:
        messagebox.showinfo("Uninstall Navi", "On a Mac, run this in Terminal:\n\n"
                            "bash \"$HOME/Library/Application Support/Navi/app/install-mac.sh\" --uninstall")
        return 0
    folder = app_dir() if FROZEN else INSTALL_DIR
    win = tk.Toplevel(root)
    win.title("Uninstall Navi")
    win.resizable(False, False)
    keep = tk.BooleanVar(value=False)
    answer = {"go": False}
    frame = ttk.Frame(win, padding=20)
    frame.pack(fill="both")
    ttk.Label(frame, text="Remove Navi from this PC?", font=("Segoe UI", 13, "bold")).pack(anchor="w")
    ttk.Label(frame, text=f"This deletes Navi, its shortcuts and its folder:\n{folder}", justify="left").pack(anchor="w", pady=(6, 10))
    ttk.Checkbutton(frame, text="Keep my keys, settings and voice recordings for next time", variable=keep).pack(anchor="w")
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(16, 0))

    def go():
        answer["go"] = True
        win.destroy()
    ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right")
    ttk.Button(buttons, text="Uninstall", command=go).pack(side="right", padx=8)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.wait_window()
    if not answer["go"]:
        return 1
    try:
        uninstall_windows(folder, keep.get())
    except Exception as e:
        messagebox.showerror("Uninstall Navi", f"Navi couldn't be removed: {e}")
        return 1
    note = f"\n\nYour keys and settings were kept in:\n{SAVED_DIR}" if keep.get() else ""
    messagebox.showinfo("Uninstall Navi", "Navi was removed." + note)
    return 0


# ---------------------------------------------------------------------------------------------------------------
# the wizard window
# ---------------------------------------------------------------------------------------------------------------
NAVY, ACCENT, MUTED, WARN = "#0b1530", "#3b82f6", "#5b6475", "#b45309"
FONT = "Segoe UI" if IS_WINDOWS else ("Helvetica Neue" if IS_MAC else "TkDefaultFont")


class Wizard:
    def __init__(self, mode, folder, payload=None):
        self.mode = mode  # "install" or "keys"
        self.folder = folder
        self.payload = payload
        self.root = tk.Tk()
        self.root.title("Navi setup" if mode == "install" else "Navi key setup")
        self.root.geometry("680x560")
        self.root.minsize(600, 520)
        try:
            ttk.Style().theme_use("vista" if IS_WINDOWS else ("aqua" if IS_MAC else "clam"))
        except tk.TclError:
            pass
        if FROZEN and IS_WINDOWS:
            try:
                self.root.iconbitmap(sys.executable)
            except tk.TclError:
                pass
        existing = read_env(os.path.join(folder, ".env"))
        if not existing and mode == "install":
            existing = read_env(os.path.join(SAVED_DIR, ".env"))
        self.vars = {}
        for page in PAGES:
            for name, *_ in page.get("fields", []):
                self.vars[name] = tk.StringVar(value=existing.get(name, os.environ.get(name, "") if name == "HF_TOKEN" else ""))
        self.calendar = tk.StringVar(value="")
        self.desktop = tk.BooleanVar(value=True)
        self.startup = tk.BooleanVar(value=False)
        self.launch = tk.BooleanVar(value=True)
        self.warned = {}
        self.staged = None
        self.unpacked = None
        self.unpack_error = None
        self.progress_value = 0.0
        self.pages = (["welcome"] if mode == "install" else []) + list(range(len(PAGES))) + ["ready", "done"]
        self.index = 0
        self.build_frame()
        self.show()
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)

    # -- layout ---------------------------------------------------------------------------------------------------
    def build_frame(self):
        head = tk.Frame(self.root, bg=NAVY, height=78)
        head.pack(fill="x")
        head.pack_propagate(False)
        orb = tk.Canvas(head, width=44, height=44, bg=NAVY, highlightthickness=0)
        orb.pack(side="left", padx=(20, 12))
        for r, color in ((20, "#1e3a8a"), (15, "#2563eb"), (9, "#93c5fd")):
            orb.create_oval(22 - r, 22 - r, 22 + r, 22 + r, fill=color, outline="")
        text = tk.Frame(head, bg=NAVY)
        text.pack(side="left", fill="y", pady=14)
        self.title = tk.Label(text, bg=NAVY, fg="white", font=(FONT, 15, "bold"), anchor="w")
        self.title.pack(anchor="w")
        self.subtitle = tk.Label(text, bg=NAVY, fg="#9fb3d9", font=(FONT, 10), anchor="w")
        self.subtitle.pack(anchor="w")
        foot = ttk.Frame(self.root, padding=(20, 10))
        foot.pack(side="bottom", fill="x")
        ttk.Separator(self.root).pack(side="bottom", fill="x")
        self.status = ttk.Label(foot, text="", foreground=MUTED)
        self.status.pack(side="left")
        self.bar = ttk.Progressbar(foot, length=140, mode="determinate", maximum=1.0)
        self.next_btn = ttk.Button(foot, text="Next", command=self.next, default="active")
        self.next_btn.pack(side="right")
        self.skip_btn = ttk.Button(foot, text="Skip", command=self.skip)
        self.skip_btn.pack(side="right", padx=6)
        self.back_btn = ttk.Button(foot, text="Back", command=self.back)
        self.back_btn.pack(side="right")
        self.body = ttk.Frame(self.root, padding=(24, 18))
        self.body.pack(fill="both", expand=True)
        self.root.bind("<Return>", lambda e: self.next())

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def para(self, text, **kw):
        label = ttk.Label(self.body, text=text, wraplength=610, justify="left", **kw)
        label.pack(anchor="w", fill="x", pady=(0, 10))
        return label

    # -- pages ----------------------------------------------------------------------------------------------------
    def show(self):
        self.clear()
        page = self.pages[self.index]
        key_pages = [p for p in self.pages if isinstance(p, int)]
        self.back_btn.state(["!disabled"] if self.index > 0 and page != "done" else ["disabled"])
        self.skip_btn.pack_forget()
        self.next_btn.configure(text="Next")
        if page == "welcome":
            self.title.configure(text="Install Navi")
            self.subtitle.configure(text=f"Version {VERSION}")
            self.para("Navi is a voice assistant for your PC. Say \"Navi\", then ask for the weather, your mail, "
                      "your homework, reminders, or chat with its AI.")
            self.para("Next you'll add your keys, one service at a time, each with a link to where you get it. "
                      "Every one except the AI is optional and you can change them later from Navi key setup in "
                      "the Start menu. They're saved only on this PC.")
            box = ttk.LabelFrame(self.body, text="Install to", padding=10)
            box.pack(fill="x", pady=(4, 12))
            ttk.Label(box, text=self.folder).pack(anchor="w")
            ttk.Label(box, text="Just for you, no admin rights needed.", foreground=MUTED).pack(anchor="w")
            ttk.Checkbutton(self.body, text="Put a Navi shortcut on my desktop", variable=self.desktop).pack(anchor="w")
            ttk.Checkbutton(self.body, text="Start Navi when I sign in to Windows", variable=self.startup).pack(anchor="w", pady=4)
            self.next_btn.configure(text="Next")
        elif isinstance(page, int):
            p = PAGES[page]
            self.title.configure(text=p["title"])
            n = key_pages.index(page) + 1
            self.subtitle.configure(text=f"Key {n} of {len(key_pages)}" + ("  ·  optional" if p["optional"] else "  ·  recommended"))
            self.para(p["about"])
            steps = ttk.Frame(self.body)
            steps.pack(anchor="w", fill="x", pady=(0, 8))
            for i, step in enumerate(p["steps"], 1):
                ttk.Label(steps, text=f"{i}.", width=3, foreground=ACCENT).grid(row=i, column=0, sticky="nw")
                ttk.Label(steps, text=step, wraplength=570, justify="left").grid(row=i, column=1, sticky="w", pady=1)
            links = ttk.Frame(self.body)
            links.pack(anchor="w", pady=(0, 12))
            for label, url in p["links"]:
                ttk.Button(links, text=label + "  ↗", command=lambda u=url: self.open_link(u)).pack(side="left", padx=(0, 8))
            if "fields" in p:
                grid = ttk.Frame(self.body)
                grid.pack(anchor="w", fill="x")
                grid.columnconfigure(1, weight=1)
                first = None
                for row, (name, label, secret, check) in enumerate(p["fields"]):
                    ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
                    entry = ttk.Entry(grid, textvariable=self.vars[name], show="•" if secret else "", width=48)
                    entry.grid(row=row, column=1, sticky="ew", pady=4)
                    first = first or entry
                    if secret:
                        shown = tk.BooleanVar(value=False)
                        ttk.Checkbutton(grid, text="Show", variable=shown,
                                        command=lambda e=entry, s=shown: e.configure(show="" if s.get() else "•")
                                        ).grid(row=row, column=2, padx=(8, 0))
                if first:
                    first.focus_set()
            else:
                row = ttk.Frame(self.body)
                row.pack(anchor="w", fill="x")
                ttk.Button(row, text="Choose the JSON file...", command=self.pick_calendar).pack(side="left")
                have = os.path.exists(os.path.join(self.folder, p["file"]))
                ttk.Label(row, textvariable=self.calendar, foreground=MUTED).pack(side="left", padx=10)
                if have and not self.calendar.get():
                    self.calendar.set("Already set up (keeps the current file)")
            self.warning = ttk.Label(self.body, text="", foreground=WARN, wraplength=610, justify="left")
            self.warning.pack(anchor="w", pady=(10, 0))
            self.skip_btn.pack(side="right", padx=6, after=self.next_btn)
        elif page == "ready":
            self.title.configure(text="Ready to install" if self.mode == "install" else "Save your keys")
            self.subtitle.configure(text="Here's what Navi can do with what you entered")
            for p in PAGES:
                done = all(self.vars[f[0]].get().strip() for f in p["fields"]) if "fields" in p else \
                    bool(self.calendar.get())
                ttk.Label(self.body, text=("✓  " if done else "–  ") + p["title"],
                          foreground="#15803d" if done else MUTED).pack(anchor="w", pady=1)
            self.para("")
            if self.mode == "install":
                ttk.Checkbutton(self.body, text="Open Navi when setup finishes", variable=self.launch).pack(anchor="w")
                self.next_btn.configure(text="Install")
            else:
                self.para("Navi reads its keys when it starts, so restart Navi after saving.", foreground=MUTED)
                self.next_btn.configure(text="Save")
        elif page == "done":
            self.title.configure(text="All set")
            self.subtitle.configure(text="")
            if self.mode == "install":
                self.para("Navi is installed. Find it in the Start menu, and say \"Navi\" to wake it up.")
                self.para("To change a key later, open Navi key setup from the Start menu. To remove Navi, use "
                          "Settings > Apps, or Uninstall Navi in the Start menu.", foreground=MUTED)
            else:
                self.para("Your keys are saved. Restart Navi so it picks them up.")
            self.next_btn.configure(text="Finish")

    def open_link(self, url):
        if url == "canvas:":
            base = self.vars["CANVAS_URL"].get().strip().rstrip("/")
            if not base:
                self.warning.configure(text="Type your Canvas address first, then press the button.")
                return
            if not base.startswith(("http://", "https://")):
                base = "https://" + base
            url = base + "/profile/settings"
        webbrowser.open(url)

    def pick_calendar(self):
        path = filedialog.askopenfilename(title="Pick the OAuth client JSON from Google Cloud",
                                          filetypes=[("JSON file", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            ok = isinstance(data, dict) and ("installed" in data or "web" in data)
        except (OSError, ValueError):
            ok = False
        if not ok:
            self.warning.configure(text="That isn't a Google OAuth client file. Download the JSON for a Desktop app client.")
            return
        self.calendar_path = path
        self.calendar.set(os.path.basename(path))

    # -- moving through the pages ---------------------------------------------------------------------------------
    def check_page(self):
        # returns True to move on. A warning is shown once; pressing Next again goes on anyway
        page = self.pages[self.index]
        if not isinstance(page, int) or "fields" not in PAGES[page]:
            return True
        notes = []
        for name, label, secret, check in PAGES[page]["fields"]:
            value = self.vars[name].get().strip()
            if name == "CANVAS_URL":
                value = value.replace("https://", "").replace("http://", "").strip("/")
            self.vars[name].set(value)
            note = check(value) if check else ""
            if note:
                notes.append(note)
        key = (page, tuple(self.vars[f[0]].get() for f in PAGES[page]["fields"]))
        if notes and self.warned.get(page) != key:
            self.warned[page] = key
            self.warning.configure(text=" ".join(notes) + "  Press Next again to keep it anyway.")
            return False
        return True

    def next(self):
        page = self.pages[self.index]
        if page == "done":
            self.finish()
            return
        if not self.check_page():
            return
        if page == "welcome":
            self.start_unpacking()
        if page == "ready":
            self.next_btn.state(["disabled"])
            self.back_btn.state(["disabled"])
            if self.mode == "install":
                self.install()
            else:
                self.save_keys()
            return
        self.index += 1
        self.show()

    def skip(self):
        page = self.pages[self.index]
        if isinstance(page, int) and "fields" in PAGES[page]:
            pass  # skipping keeps what was there before (an empty key stays empty)
        self.index += 1
        self.show()

    def back(self):
        if self.index > 0:
            self.index -= 1
            self.show()

    def cancel(self):
        if self.pages[self.index] != "done" and not messagebox.askyesno(self.root.title(), "Quit setup? Nothing will be changed."):
            return
        if self.staged:
            shutil.rmtree(self.staged, ignore_errors=True)
        self.root.destroy()

    def env_values(self):
        return {name: var.get().strip() for name, var in self.vars.items()}

    # -- installing -----------------------------------------------------------------------------------------------
    def start_unpacking(self):
        # unpacks the program files into a side folder while you type your keys
        if self.staged or not self.payload:
            return
        self.staged = os.path.join(self.folder, ".incoming")
        self.bar.pack(side="left", padx=(10, 0))
        self.status.configure(text="Unpacking Navi...")

        def work():
            try:
                shutil.rmtree(self.staged, ignore_errors=True)
                os.makedirs(self.staged, exist_ok=True)
                self.unpacked = unpack(self.payload, self.staged, lambda f: setattr(self, "progress_value", f))
            except Exception as e:
                self.unpack_error = e
        threading.Thread(target=work, daemon=True).start()
        self.poll_progress()

    def poll_progress(self):
        self.bar["value"] = self.progress_value
        if self.unpack_error:
            self.status.configure(text="Unpacking failed", foreground=WARN)
        elif self.unpacked is not None:
            self.status.configure(text="Navi is unpacked")
            self.bar.pack_forget()
        else:
            self.root.after(100, self.poll_progress)

    def install(self):
        self.status.configure(text="Installing...")
        self.bar.pack(side="left", padx=(10, 0))

        def wait():
            if self.unpack_error:
                messagebox.showerror("Navi setup", f"Setup couldn't unpack Navi: {self.unpack_error}")
                self.root.destroy()
                return
            if self.unpacked is None:
                self.root.after(100, wait)
                return
            try:
                self.exe = finish_install(self.folder, self.staged, self.unpacked,
                                          {"desktop": self.desktop.get(), "startup": self.startup.get()},
                                          self.env_values(), getattr(self, "calendar_path", None))
            except Exception as e:
                messagebox.showerror("Navi setup", f"Setup couldn't finish: {e}")
                self.next_btn.state(["!disabled"])
                self.back_btn.state(["!disabled"])
                return
            self.status.configure(text="")
            self.bar.pack_forget()
            self.index += 1
            self.next_btn.state(["!disabled"])
            self.show()
        wait()

    def save_keys(self):
        try:
            write_env(os.path.join(self.folder, ".env"), self.env_values())
            if getattr(self, "calendar_path", None):
                shutil.copyfile(self.calendar_path, os.path.join(self.folder, "google_credentials.json"))
        except OSError as e:
            messagebox.showerror("Navi key setup", f"Couldn't save the keys: {e}")
            self.next_btn.state(["!disabled"])
            return
        self.index += 1
        self.next_btn.state(["!disabled"])
        self.show()

    def finish(self):
        if self.mode == "install" and self.launch.get() and getattr(self, "exe", None):
            subprocess.Popen([self.exe], cwd=self.folder, close_fds=True)
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    folder = app_dir()
    if "--dir" in argv:
        i = argv.index("--dir")
        if i + 1 < len(argv):
            folder = os.path.abspath(argv[i + 1])
    if "--uninstall" in argv:
        return run_uninstall()
    payload = open_payload() if FROZEN and IS_WINDOWS else None
    if "--selftest" in argv:  # the build checks the glued-on zip is readable
        return 0 if payload and payload.testzip() is None else 3
    if "--keys" in argv or payload is None:
        return Wizard("keys", folder).run()
    return Wizard("install", INSTALL_DIR, payload).run()


if __name__ == "__main__":
    sys.exit(main())
