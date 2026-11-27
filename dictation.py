#dictation mode for navi: say "start dictation" and navi types what you say into the window you're using, until you
#say "stop dictation".
#
#voice.py calls start_request(text) and, when it's True, run(...) with how to speak, listen and post to the window.
#What you say is turned into text here (spoken punctuation like "comma" and "new line", capitals at the start of
#sentences, "scratch that" to take back the last bit) and typed as key presses:
#  - Windows: SendInput with unicode key presses, so any language and symbol types right, whatever the keyboard layout
#  - macOS: System Events through osascript, with the text passed as an argument and never pasted into the script
#    (Navi needs Accessibility permission in System Settings > Privacy & Security for this)
#Safety:
#  - nothing is typed into a terminal (cmd, PowerShell, Windows Terminal, Terminal, iTerm and others) or the Windows
#    Run box, where "new line" presses Enter and would run what was typed as a command
#  - nothing is typed into Navi's own window
#  - the stop phrases are checked before anything is typed, so "stop dictation" itself is never typed
import os
import re
import subprocess
import sys

IS_WINDOWS=sys.platform=="win32"
IS_MAC=sys.platform=="darwin"
MAX_CHUNK=2000 #one thing you said; google never sends more, this only guards against surprises

START_RE=re.compile(r"^(?:please\s+)?(?:(?:start|begin|turn on|enable|open)\s+(?:the\s+)?(?:dictation|dictating|voice typing)(?:\s+mode)?|"
                    r"dictation(?:\s+mode)?(?:\s+on)?|dictate|(?:let's|let me|i want to)\s+dictate|take\s+(?:a\s+)?dictation|"
                    r"(?:start\s+)?voice\s+typing|type\s+(?:what|everything)\s+i\s+say|type\s+for\s+me)\s*[.!]*$")
#a stop phrase is the whole thing you said, or its end ("...see you tomorrow stop dictation")
STOP_RE=re.compile(r"(?:^|\s)(?:(?:navi\s+)?(?:stop|end|exit|quit|finish|cancel|turn off|close)\s+(?:the\s+)?(?:dictation|dictating|voice typing|typing)(?:\s+mode)?|"
                   r"dictation\s+off|(?:i'?m|i am)\s+done\s+dictating|done\s+dictating)\s*[.!]*$", re.I)
UNDO_RE=re.compile(r"^(?:scratch|undo|delete|erase|remove)\s+(?:that|the last (?:bit|part|line|sentence))\s*[.!]*$", re.I)

#spoken words that become punctuation. Longer phrases first, so "question mark" wins over a word inside it
SPOKEN=[
    (r"new\s+paragraph", "\n\n"),
    (r"new\s*line|next\s+line|press\s+enter", "\n"),
    (r"question\s+mark", "?"),
    (r"exclamation\s+(?:point|mark)", "!"),
    (r"full\s+stop|period", "."),
    (r"comma", ","),
    (r"semi\s*-?colon", ";"),
    (r"colon", ":"),
    (r"open\s+(?:quote|quotes|quotation\s+marks?)", "“open"), #marked so the spacing knows it opens
    (r"(?:close|end)\s+(?:quote|quotes|quotation\s+marks?)|unquote", "”close"),
    (r"open\s+(?:paren|parenthesis|bracket)", "(open"),
    (r"close\s+(?:paren|parenthesis|bracket)", ")close"),
    (r"hyphen", "-join"),
    (r"dash", "—join"),
    (r"ellipsis|dot\s+dot\s+dot", "…"),
    (r"tab\s+key|press\s+tab", "\ttab"),
]
SPOKEN_RE=re.compile(r"\b(?:"+"|".join(f"(?P<p{i}>{p})" for i, (p, _) in enumerate(SPOKEN))+r")\b", re.I)
SENTENCE_END=".?!\n"

def start_request(t):
    return bool(START_RE.match(t.strip().lower()))

def split_stop(text):
    #(what to type before the stop phrase, whether you said a stop phrase)
    m=STOP_RE.search(text.strip())
    if not m:
        return text, False
    return text.strip()[:m.start()].strip(), True

class Formatter:
    #turns what you said into the exact text to type, remembering what came before so spacing and capitals are right
    def __init__(self):
        self.history=[] #what each thing you said typed, for "scratch that"

    def format(self, text):
        text=re.sub(r"\s+", " ", text[:MAX_CHUNK]).strip()
        done="".join(self.history[-20:]) #enough to know the last few characters
        out=[]
        def last():
            return (done+"".join(out))[-1:]
        def space():
            return " " if last() and last() not in " \n\t(\u201c-\u2014" else ""
        def capital():
            tail=(done+"".join(out)).rstrip(" \u201c(")
            return not tail or tail[-1] in SENTENCE_END
        pos=0
        parts=[]
        for m in SPOKEN_RE.finditer(text):
            parts.append(("words", text[pos:m.start()]))
            parts.append(("mark", SPOKEN[int(m.lastgroup[1:])][1]))
            pos=m.end()
        parts.append(("words", text[pos:]))
        for kind, value in parts:
            if kind=="words":
                for word in value.split():
                    if capital():
                        word=word[0].upper()+word[1:]
                    out.append(space()+word)
            elif value.startswith("\n"):
                out.append(value)
            elif value.endswith("open"):
                out.append(space()+value[0])
            else: #closing marks, hyphens, dashes and tabs: no space before
                out.append(value[0])
        typed="".join(out)
        if typed:
            self.history.append(typed)
        return typed

    def undo(self):
        #how many characters to delete for "scratch that" (0 if nothing to take back)
        return len(self.history.pop()) if self.history else 0

#the window that will get the keys
TERMINALS_WINDOWS={"cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe",
                   "openconsole.exe", "bash.exe", "wsl.exe", "wslhost.exe", "mintty.exe", "alacritty.exe", "wezterm-gui.exe",
                   "putty.exe", "kitty.exe", "hyper.exe", "tabby.exe", "cmder.exe", "conemu.exe", "conemu64.exe", "mobaxterm.exe"}
TERMINALS_MAC={"terminal", "iterm2", "iterm", "warp", "alacritty", "kitty", "wezterm", "hyper", "tabby", "ghostty"}

def focused_window():
    #(program name in lower case, is it navi itself, is it a place that runs typed commands); ("", False, False) if unknown
    if IS_WINDOWS:
        return _focused_windows()
    if IS_MAC:
        return _focused_mac()
    return "", False, False

def _focused_windows():
    import ctypes
    from ctypes import wintypes
    user32=ctypes.WinDLL("user32") #own copies, so these argtypes don't touch anyone else's calls
    kernel32=ctypes.WinDLL("kernel32")
    user32.GetForegroundWindow.restype=wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes=[wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes=[wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    kernel32.OpenProcess.restype=wintypes.HANDLE
    kernel32.OpenProcess.argtypes=[wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    hwnd=user32.GetForegroundWindow()
    if not hwnd:
        return "", False, False
    pid=wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name=""
    handle=kernel32.OpenProcess(0x1000, False, pid.value) #PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            buf=ctypes.create_unicode_buffer(1024)
            size=wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name=os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    cls=ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title=ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    run_box=cls.value=="#32770" and name=="explorer.exe" and title.value.strip().lower() in ("run", "")
    console=cls.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow")
    return name, pid.value==os.getpid(), name in TERMINALS_WINDOWS or console or run_box

def _focused_mac():
    try:
        out=subprocess.run(["/usr/bin/osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
                           capture_output=True, text=True, timeout=5).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return "", False, False
    own=out in ("python", "python3", "navi") or out.startswith("python")
    return out, own, out in TERMINALS_MAC

#typing
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    class _KEYBDINPUT(ctypes.Structure):
        _fields_=[("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                  ("dwExtraInfo", ctypes.c_size_t)]
    class _MOUSEINPUT(ctypes.Structure): #only here so the union is the size windows expects
        _fields_=[("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                  ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
    class _INPUTUNION(ctypes.Union):
        _fields_=[("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
    class _INPUT(ctypes.Structure):
        _fields_=[("type", wintypes.DWORD), ("u", _INPUTUNION)]
    _send=ctypes.WinDLL("user32").SendInput
    _send.argtypes=[wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _send.restype=wintypes.UINT

def _key_events(text):
    #(virtual key, unicode unit) pairs; enter, tab and backspace go as real keys so apps treat them normally
    events=[]
    for ch in text:
        if ch=="\n":
            events.append((0x0D, 0))
        elif ch=="\t":
            events.append((0x09, 0))
        elif ch=="\b":
            events.append((0x08, 0))
        else:
            data=ch.encode("utf-16-le")
            for i in range(0, len(data), 2): #emoji and other characters past 0xFFFF are two units
                events.append((0, int.from_bytes(data[i:i+2], "little")))
    return events

def type_text(text):
    #types into the focused window; False if the system refused (for example a window running as administrator)
    if not text:
        return True
    if IS_WINDOWS:
        inputs=[]
        for vk, unit in _key_events(text):
            for up in (0, 2): #KEYEVENTF_KEYUP
                flags=up|(4 if not vk else 0) #KEYEVENTF_UNICODE
                inputs.append(_INPUT(1, _INPUTUNION(ki=_KEYBDINPUT(vk, unit, flags, 0, 0)))) #INPUT_KEYBOARD
        sent=0
        for i in range(0, len(inputs), 200): #in batches, so a long sentence doesn't stall other input
            batch=(_INPUT*len(inputs[i:i+200]))(*inputs[i:i+200])
            sent+=_send(len(batch), batch, ctypes.sizeof(_INPUT))
        return sent==len(inputs)
    if IS_MAC:
        script=('on run argv\n'
                'set t to item 1 of argv\n'
                'set AppleScript\'s text item delimiters to linefeed\n'
                'set parts to text items of t\n'
                'tell application "System Events"\n'
                'repeat with i from 1 to count of parts\n'
                'if i > 1 then key code 36\n'
                'set p to item i of parts\n'
                'if p is not "" then keystroke p\n'
                'end repeat\n'
                'end tell\n'
                'end run')
        try:
            return subprocess.run(["/usr/bin/osascript", "-e", script, text.replace("\t", "    ")], capture_output=True, timeout=60).returncode==0
        except (OSError, subprocess.SubprocessError):
            return False
    return False

def delete_chars(n):
    if n<=0:
        return True
    if IS_MAC:
        script='on run argv\ntell application "System Events"\nrepeat (item 1 of argv as integer) times\nkey code 51\nend repeat\nend tell\nend run'
        try:
            return subprocess.run(["/usr/bin/osascript", "-e", script, str(int(n))], capture_output=True, timeout=60).returncode==0
        except (OSError, subprocess.SubprocessError):
            return False
    return type_text("\b"*n)

def run(say, listen, post=lambda kind, value: None, focused=focused_window, typer=type_text, deleter=delete_chars):
    #the dictation loop. listen() returns what you said, "" if it was unclear, or None if speech recognition is offline
    if not (IS_WINDOWS or IS_MAC) and typer is type_text:
        say("I can only type for you on Windows or a Mac.")
        return
    say("Dictation on. Click where you want the text, then talk. Say stop dictation when you're done.")
    post("system", "Dictation on: say \"stop dictation\" to finish, \"scratch that\" to undo, \"new line\" for Enter.")
    fmt=Formatter()
    warned=None
    while True:
        heard=listen()
        if heard is None:
            say("I lost the connection to speech recognition, so dictation is off.")
            break
        if not heard.strip():
            continue
        text, stop=split_stop(heard)
        if UNDO_RE.match(text.strip()):
            name, own, blocked=focused()
            if own or blocked:
                say("I won't type in that window.")
            elif not deleter(fmt.undo()):
                say("I couldn't change that window.")
            continue
        if text:
            name, own, blocked=focused()
            if own:
                if warned!="own":
                    say("Click the window you want me to type in first.")
                warned="own"
            elif blocked:
                if warned!=name:
                    say("I won't type into a terminal or the Run box, because new line would run it as a command.")
                warned=name
            else:
                warned=None
                typed=fmt.format(text)
                if typed and not typer(typed):
                    fmt.undo()
                    say("I couldn't type into that window. If it's running as administrator, Navi has to be too.")
        if stop:
            break
    post("system", "Dictation off.")
    say("Dictation off.")
