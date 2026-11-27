#navi on your phone: a small web app (phone_app/) that Navi serves to your phone. You add it to your home screen,
#then type or talk to Navi on this PC from anywhere in the house, or from anywhere at all through Tailscale.
#
#security: a phone has to be paired first, with a one-time code shown in settings > phone app (as a QR code). Pairing
#gives the phone a long random token; only a hash of it is saved here, and removing the phone in settings stops it at
#once. Only devices on your own network (home wifi, or your Tailscale network) can connect at all.
import os
import json
import time
import queue
import socket
import secrets
import hashlib
import hmac
import shutil
import ipaddress
import threading
import subprocess
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE=os.path.dirname(os.path.abspath(__file__))
APP_DIR=os.path.join(HERE, "phone_app")
STATE_FILE=os.path.join(HERE, "navi_phones.json")
DEFAULT_STATE={"enabled": False, "port": 8765, "lan": True, "pc_voice": False, "devices": []}
CODE_LETTERS="ABCDEFGHJKMNPQRSTUVWXYZ23456789" #no 0/O, 1/I/L, so a typed code isn't misread
PAIR_MINUTES=10
PAIR_TRIES=10 #wrong codes before pairing closes
ANSWER_SECONDS=300 #how long a question from navi ("send it?") waits for the phone's answer
MAX_TEXT=2000
MAX_BODY=16*1024
TAILSCALE_NET=ipaddress.ip_network("100.64.0.0/10") #tailscale (and carrier) addresses aren't "private" to python

class Hooks:
    #what the phone needs from voice.py
    def __init__(self, run, speech, post, cancelled, name="navi", font="TkDefaultFont"):
        self.run=run              #run(text): handles one command, as if it was said on the pc
        self.speech=speech        #speech(text, lang) -> mp3 bytes in navi's voice
        self.post=post            #post(kind, text): a line in navi's window on the pc
        self.cancelled=cancelled  #the exception that ends a command early (voice.py's StopListening)
        self.name=name
        self.font=font            #the font navi's windows use

hooks=None
local=threading.local()
lock=threading.RLock()

#--- saved state: on/off, port and the paired phones (token hashes, never the tokens)
def load_state():
    s=json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            saved=json.load(f)
        s.update({k: v for k, v in saved.items() if k in DEFAULT_STATE and type(v)==type(DEFAULT_STATE[k])})
    except (OSError, ValueError, AttributeError):
        pass
    s["devices"]=[d for d in s["devices"] if isinstance(d, dict) and d.get("id") and d.get("hash")]
    return s

def save_state():
    with lock:
        tmp=STATE_FILE+".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)

state=load_state()

def setting(key, value=None):
    if value is not None and state[key]!=value:
        with lock:
            state[key]=value
            save_state()
    return state[key]

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def device_for(token):
    if not token or len(token)>200:
        return None
    h=token_hash(token)
    with lock:
        for d in state["devices"]:
            if hmac.compare_digest(d["hash"], h):
                return d
    return None

def devices():
    with lock:
        return [dict(d) for d in state["devices"]]

def remove_device(device_id):
    with lock:
        state["devices"]=[d for d in state["devices"] if d["id"]!=device_id]
        save_state()
        s=sessions.pop(device_id, None)
    if s:
        s.cancel()

#--- pairing: a one-time code, open for a few minutes after you press "pair a phone"
pairing={"code": None, "until": 0, "tries": 0}

def start_pairing():
    code="".join(secrets.choice(CODE_LETTERS) for _ in range(8))
    with lock:
        pairing.update(code=code, until=time.time()+PAIR_MINUTES*60, tries=0)
    return code

def stop_pairing():
    with lock:
        pairing.update(code=None, until=0)

def pairing_open():
    with lock:
        return bool(pairing["code"]) and time.time()<pairing["until"]

def pretty_code(code):
    return code[:4]+"-"+code[4:]

def pair(code, name):
    #a new token for the phone if the code is right (and the code can't be used again)
    code="".join(ch for ch in str(code).upper() if ch in CODE_LETTERS)
    with lock:
        if not pairing_open():
            return None
        if not hmac.compare_digest(code, pairing["code"]):
            pairing["tries"]+=1
            if pairing["tries"]>=PAIR_TRIES:
                stop_pairing()
            return None
        stop_pairing()
        token=secrets.token_urlsafe(32)
        device={"id": secrets.token_hex(8), "name": (str(name).strip() or "Phone")[:40], "hash": token_hash(token),
                "added": int(time.time()), "seen": int(time.time())}
        state["devices"].append(device)
        save_state()
    if hooks:
        hooks.post("system", f"Paired a new phone: {device['name']}.")
    return token, device

#--- one session per phone: what navi said to it, and the commands it sent
class Heard:
    #what get_mic_input() hands back when the "microphone" is the phone: text it already typed or transcribed
    def __init__(self, text):
        self.text=text

STOP=object()

class Session:
    def __init__(self, device_id):
        self.device_id=device_id
        self.events=[]
        self.next_id=1
        self.changed=threading.Condition(lock)
        self.inbox=queue.Queue()
        self.worker=None
        self.busy=False
        self.waiting=False

    def add(self, kind, **data):
        with self.changed:
            self.events.append(dict(data, id=self.next_id, kind=kind, at=int(time.time())))
            self.next_id+=1
            del self.events[:-200]
            self.changed.notify_all()

    def since(self, after, wait=0):
        #events after that id, waiting up to `wait` seconds for one to arrive (long polling)
        with self.changed:
            if after>=self.next_id: #navi restarted since the phone last asked: start over
                after=0
            self.changed.wait_for(lambda: self.next_id-1>after, timeout=wait)
            return [e for e in self.events if e["id"]>after], self.next_id-1

    def event(self, event_id):
        with lock:
            return next((e for e in self.events if e["id"]==event_id), None)

    def submit(self, text):
        self.add("you", text=text)
        self.inbox.put(text)
        with lock:
            if self.worker is None or not self.worker.is_alive():
                self.worker=threading.Thread(target=self.work, daemon=True)
                self.worker.start()

    def cancel(self):
        self.inbox.put(STOP)

    def set_busy(self, busy, waiting=False):
        self.busy, self.waiting=busy, waiting
        self.add("state", busy=busy, waiting=waiting)

    def work(self):
        local.session=self
        while True:
            try:
                text=self.inbox.get(timeout=600)
            except queue.Empty:
                with lock:
                    if self.inbox.empty():
                        self.worker=None
                        return
                continue
            if text is STOP:
                continue
            self.set_busy(True)
            try:
                hooks.run(text)
            except hooks.cancelled:
                self.add("system", text="Stopped.")
            except Exception as e:
                print("phone err; {0}".format(e))
                self.add("system", text=f"Something went wrong: {e}")
            finally:
                self.set_busy(False)

    def hear(self):
        #navi asked something ("should I send it?"): the phone's next message is the answer
        self.set_busy(True, waiting=True)
        try:
            text=self.inbox.get(timeout=ANSWER_SECONDS)
        except queue.Empty:
            self.add("system", text="I stopped waiting for an answer.")
            raise hooks.cancelled
        finally:
            self.set_busy(True)
        if text is STOP:
            raise hooks.cancelled
        return text

sessions={}

def session_for(device_id):
    with lock:
        if device_id not in sessions:
            sessions[device_id]=Session(device_id)
        return sessions[device_id]

def current():
    #the phone session this thread is answering, or None when it's the pc's own microphone
    return getattr(local, "session", None)

#--- the web server
def allowed_client(address):
    #home network, this pc, or tailscale. Never the open internet, even if a port gets forwarded by mistake
    try:
        ip=ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip=ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local or (ip.version==4 and ip in TAILSCALE_NET)

def static_files():
    try:
        return {name for name in os.listdir(APP_DIR) if os.path.isfile(os.path.join(APP_DIR, name))}
    except OSError:
        return set()

TYPES={".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
       ".png": "image/png", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json"}

class Handler(BaseHTTPRequestHandler):
    server_version="Navi"
    sys_version=""
    protocol_version="HTTP/1.1"

    def log_message(self, format, *args):
        pass #keeps the console quiet; errors are printed where they happen

    def send(self, code, body=b"", kind="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body=json.dumps(body).encode()
        elif isinstance(body, str):
            body=body.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
                                                    "style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command!="HEAD":
            self.wfile.write(body)

    def refuse(self):
        if not allowed_client(self.client_address[0]):
            self.send(403, {"error": "Only devices on your own network can reach Navi."})
            return True
        return False

    def device(self):
        auth=self.headers.get("Authorization", "")
        d=device_for(auth[7:].strip() if auth.startswith("Bearer ") else "")
        if d is None:
            self.send(401, {"error": "This phone isn't paired."})
            return None
        if time.time()-d.get("seen", 0)>60: #"last used" in settings, saved at most once a minute
            with lock:
                d["seen"]=int(time.time())
                save_state()
        return d

    def body(self):
        try:
            n=int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n=0
        if n<=0 or n>MAX_BODY:
            return {}
        try:
            data=json.loads(self.rfile.read(n))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.refuse():
            return
        url=urllib.parse.urlsplit(self.path)
        query=urllib.parse.parse_qs(url.query)
        path=url.path
        if path.startswith("/api/"):
            d=self.device()
            if d is None:
                return
            if path=="/api/hello":
                return self.send(200, {"name": hooks.name if hooks else "navi", "device": d["name"]}, extra={"Cache-Control": "no-store"})
            if path=="/api/events":
                try:
                    after=int(query.get("after", ["0"])[0])
                except ValueError:
                    after=0
                s=session_for(d["id"])
                events, last=s.since(after, wait=25 if query.get("wait") else 0)
                return self.send(200, {"events": events, "last": last, "busy": s.busy, "waiting": s.waiting},
                                 extra={"Cache-Control": "no-store"})
            if path=="/api/speech":
                try:
                    e=session_for(d["id"]).event(int(query.get("id", ["0"])[0]))
                except ValueError:
                    e=None
                if not e or e["kind"]!="navi":
                    return self.send(404, {"error": "No such reply."})
                try:
                    mp3=hooks.speech(e["text"], e.get("lang", "en"))
                except Exception as err:
                    print("phone speech err; {0}".format(err))
                    return self.send(502, {"error": "Couldn't make the speech."})
                return self.send(200, mp3, "audio/mpeg", {"Cache-Control": "private, max-age=3600"})
            return self.send(404, {"error": "Not found."})
        name="index.html" if path in ("/", "") else path.lstrip("/")
        if name not in static_files(): #only the app's own files, never anything else on the pc
            return self.send(404, "Not found", "text/plain")
        with open(os.path.join(APP_DIR, name), "rb") as f:
            data=f.read()
        kind=TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
        self.send(200, data, kind, {"Cache-Control": "no-cache"})

    def do_POST(self):
        if self.refuse():
            return
        path=urllib.parse.urlsplit(self.path).path
        data=self.body()
        if path=="/api/pair":
            result=pair(data.get("code", ""), data.get("name", "Phone"))
            if not result:
                return self.send(403, {"error": "That code didn't work. In Navi on your PC, open Settings, then Phone app, and press Pair a phone for a new one."})
            return self.send(200, {"token": result[0], "device": result[1]["name"], "name": hooks.name if hooks else "navi"})
        d=self.device()
        if d is None:
            return
        if path=="/api/say":
            text=str(data.get("text", "")).strip()[:MAX_TEXT]
            if not text:
                return self.send(400, {"error": "Nothing to say."})
            if hooks:
                hooks.post("system", f"From {d['name']}: {text}")
            session_for(d["id"]).submit(text)
            return self.send(200, {"ok": True})
        if path=="/api/stop":
            session_for(d["id"]).cancel()
            return self.send(200, {"ok": True})
        if path=="/api/unpair":
            remove_device(d["id"])
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "Not found."})

class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False #a second navi shouldn't share the port

server=None
server_error=""

def start(h):
    #starts the server if the phone app is turned on (call again after changing settings)
    global hooks, server, server_error
    hooks=h or hooks
    stop()
    if not state["enabled"]:
        return False
    host="0.0.0.0" if state["lan"] else "127.0.0.1" #tailscale serve reaches it on 127.0.0.1 either way
    try:
        server=Server((host, int(state["port"])), Handler)
    except OSError as e:
        server_error=f"Couldn't use port {state['port']}: {e.strerror or e}. Is Navi already open?"
        print("phone server err; {0}".format(e))
        return False
    server_error=""
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return True

def stop():
    global server
    if server is not None:
        server.shutdown()
        server.server_close()
        server=None

def running():
    return server is not None

#--- addresses the phone can use
def lan_ip():
    #this pc's address on the home network (nothing is sent: a udp "connect" only picks the route)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip=s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None

def lan_url():
    ip=lan_ip()
    return f"http://{ip}:{state['port']}" if ip and state["lan"] else None

def tailscale_exe():
    for path in (shutil.which("tailscale"), r"C:\Program Files\Tailscale\tailscale.exe",
                 "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if path and os.path.exists(path):
            return path
    return None

def tailscale(*args, timeout=10):
    exe=tailscale_exe()
    if not exe:
        return None
    try:
        r=subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                         creationflags=0x08000000 if os.name=="nt" else 0) #no console window
        return r
    except (OSError, subprocess.TimeoutExpired):
        return None

def tailscale_name():
    #this pc's name on your tailscale network, like navi-pc.tail1234.ts.net (None if tailscale isn't on)
    r=tailscale("status", "--json")
    if not r or r.returncode:
        return None
    try:
        info=json.loads(r.stdout)
    except ValueError:
        return None
    if info.get("BackendState")!="Running":
        return None
    return (info.get("Self") or {}).get("DNSName", "").rstrip(".") or None

def tailscale_url():
    #the https address, if `tailscale serve` already points at navi
    name=tailscale_name()
    if not name:
        return None
    r=tailscale("serve", "status", "--json")
    if r and not r.returncode and f"127.0.0.1:{state['port']}" in r.stdout:
        return f"https://{name}"
    return None

def setup_tailscale():
    #points https://<this pc>.<tailnet>.ts.net at navi, reachable only by your own devices. Returns (url, message)
    if not tailscale_exe():
        return None, "Tailscale isn't installed on this PC yet. Get it free at tailscale.com/download, sign in, then try again."
    name=tailscale_name()
    if not name:
        return None, "Tailscale is installed but not signed in or turned on. Open it, sign in, then try again."
    exe=tailscale_exe()
    try:
        p=subprocess.Popen([exe, "serve", "--bg", "--yes", str(state["port"])], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, creationflags=0x08000000 if os.name=="nt" else 0)
    except OSError as e:
        return None, f"Couldn't start Tailscale: {e}"
    lines=[]
    def read():
        for line in p.stdout:
            lines.append(line.strip())
    reader=threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(20) #it waits by itself if https has to be turned on first: the link below does that
    url=tailscale_url()
    if url:
        return url, "Done. Your phone can use this address anywhere, as long as Tailscale is on there too."
    link=next((w for l in lines for w in l.split() if w.startswith("https://login.tailscale.com")), None)
    if link:
        return None, f"Tailscale needs HTTPS turned on for your network, once. Open this link, turn it on, then press the button again:\n{link}"
    if p.poll() is None:
        p.kill()
    return None, "Tailscale didn't finish: "+(" ".join(lines[-3:]) or "no answer.")

def best_url():
    return tailscale_url() or lan_url()

def pair_link(code, url):
    return f"{url}/#pair={code}"

def qr_image(text, size=220):
    #a PIL image of the QR code, or None without the qrcode package (the link and code still work)
    try:
        import qrcode
    except ImportError:
        return None
    qr=qrcode.QRCode(border=2, box_size=8)
    qr.add_data(text)
    qr.make(fit=True)
    img=qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((size, size))

#--- settings > phone app (the window is voice.py's SettingsWindow; this fills one page of it)
def when_text(ts):
    if not ts:
        return "never"
    ago=time.time()-ts
    if ago<120:
        return "just now"
    if ago<3600:
        return f"{int(ago//60)} minutes ago"
    if ago<86400:
        return f"{int(ago//3600)} hours ago"
    return time.strftime("%b %d", time.localtime(ts))

def settings_page(win):
    import tkinter as tk
    c=win.gui.COLORS
    page={"popup": None}
    found_url={} #the tailscale address, looked up in the background (it runs the tailscale command)
    win.section("Phone app")
    win.label("Talk to Navi on this PC from your phone, by typing or with your voice. It's a web app you add to your "
              "home screen, so there's nothing to buy and it works on Android and iPhone.", muted=False)
    on=tk.BooleanVar(value=state["enabled"])
    lan=tk.BooleanVar(value=state["lan"])
    pc_voice=tk.BooleanVar(value=state["pc_voice"])

    def restart():
        win.in_background(lambda: start(None), lambda result: show_status())

    def toggle():
        setting("enabled", on.get())
        if not on.get():
            close_popup()
        restart()

    win.check("Let my phone use Navi", None, on, toggle)
    win.check("Allow phones on my home Wi-Fi", None, lan, lambda: (setting("lan", lan.get()), restart()))
    win.check("Also say phone replies out loud on this PC", None, pc_voice, lambda: setting("pc_voice", pc_voice.get()))
    page["status"]=win.label("")
    win.buttons(("Pair a phone", lambda: open_pairing(), True))
    page["pair"]=win.label("")

    win.section("Paired phones")
    page["list"]=tk.Frame(win.body, bg=c["bg"])
    page["list"].grid(row=win.row, column=0, columnspan=2, sticky="w")
    win.row+=1

    win.section("Away from home")
    win.label("Free and private with Tailscale, which links your own devices with encryption:\n1. Install Tailscale on this PC "
              "and on your phone (tailscale.com/download) and sign in to the same account on both.\n2. Press Set up "
              "Tailscale.\n3. Press Pair a phone again and scan the new code.\nThis address also makes the microphone "
              "button in the phone app work, because it's HTTPS.", muted=False)
    win.buttons(("Set up Tailscale", lambda: setup(), False))
    page["tailscale"]=win.label("")

    def setup():
        page["tailscale"].configure(text="Setting up Tailscale...")
        def done(result):
            if isinstance(result, Exception):
                result=(None, f"Something went wrong: {result}")
            if result[0]:
                found_url["url"]=result[0]
            page["tailscale"].configure(text=result[1])
            show_status()
        win.in_background(setup_tailscale, done)

    def show_status():
        if not win.win.winfo_exists():
            return
        if not state["enabled"]:
            text="Off."
        elif not running():
            text=server_error or "Starting..."
        else:
            urls=[u for u in (found_url.get("url"), lan_url()) if u]
            text="On. Address: "+(" or ".join(urls) if urls else "only through Tailscale (set it up below).")
            if state["lan"] and os.name=="nt":
                text+="\nIf Windows asks, let Navi through the firewall on private networks."
        page["status"].configure(text=text)
        show_devices()

    def close_popup():
        stop_pairing()
        if page["popup"] is not None and page["popup"].winfo_exists():
            page["popup"].destroy()
        page["popup"]=None

    def open_pairing():
        if not running():
            page["pair"].configure(text="Turn on \"Let my phone use Navi\" first.")
            return
        close_popup()
        code=start_pairing()
        page["pair"].configure(text="Getting the address...")
        def work():
            found_url["url"]=tailscale_url()
            return found_url["url"] or lan_url()
        def done(url):
            if not win.win.winfo_exists():
                return
            if isinstance(url, Exception) or not url:
                stop_pairing()
                page["pair"].configure(text="I couldn't find an address your phone can use. Check that this PC is on "
                                            "Wi-Fi, or set up Tailscale below.")
                return
            page["pair"].configure(text="")
            show_popup(code, url)
        win.in_background(work, done)

    def show_popup(code, url):
        #the qr code in its own small window, so the settings window doesn't grow
        pop=tk.Toplevel(win.win)
        page["popup"]=pop
        pop.title("Pair a phone")
        pop.configure(bg=c["bg"])
        pop.resizable(False, False)
        pop.transient(win.win)
        pop.protocol("WM_DELETE_WINDOW", close_popup)
        img=qr_image(pair_link(code, url))
        if img is not None:
            from PIL import ImageTk
            photo=ImageTk.PhotoImage(img)
            qr=tk.Label(pop, image=photo, bg=c["bg"])
            qr.image=photo #tk drops images nobody holds on to
            qr.pack(padx=20, pady=(20,10))
        tk.Label(pop, text=pretty_code(code), font=(hooks.font, 20, "bold"), fg=c["text"], bg=c["bg"]).pack()
        how=(("Scan this with your phone's camera, or open " if img is not None else "On your phone, open ")+
             f"{url} and type the code. It works once, within {PAIR_MINUTES} minutes.\n\nThen add it to your home "
             "screen. iPhone: Share, then Add to Home Screen. Android: the menu, then Add to Home screen or Install app.")
        tk.Label(pop, text=how, font=(hooks.font, 10), fg=c["text"], bg=c["bg"], justify="left", wraplength=300).pack(padx=20, pady=10)
        win.gui.make_button(pop, "Cancel", close_popup).pack(pady=(0,16))
        watch(len(state["devices"]))

    def watch(count):
        #closes the code once the phone has paired, and lists the phone
        if not win.win.winfo_exists() or page["popup"] is None:
            return
        if len(state["devices"])>count or not pairing_open():
            paired=len(state["devices"])>count
            close_popup()
            page["pair"].configure(text="Paired! Your phone is ready." if paired else "The code ran out. Press Pair a phone for a new one.")
            show_devices()
            return
        win.win.after(1000, lambda: watch(count))

    def show_devices():
        frame=page["list"]
        for w in frame.winfo_children():
            w.destroy()
        found=devices()
        if not found:
            tk.Label(frame, text="None yet.", font=(hooks.font, 9), fg=c["muted"], bg=c["bg"]).pack(anchor="w")
        for d in found:
            row=tk.Frame(frame, bg=c["bg"])
            tk.Label(row, text=f"{d['name']}  ·  last used {when_text(d.get('seen'))}", font=(hooks.font, 10), fg=c["text"],
                     bg=c["bg"]).pack(side="left")
            win.gui.make_button(row, "Remove", lambda i=d["id"]: (remove_device(i), show_devices())).pack(side="left", padx=(10,0))
            row.pack(anchor="w", pady=2)

    def first_check():
        found_url["url"]=tailscale_url()
    win.in_background(first_check, lambda result: show_status())
    show_status()
