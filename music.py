#music for navi: controls spotify through the spotify web api, so it works on any of your spotify devices (this pc,
#your phone, a speaker, the web player) without the spotify app open here. voice.py calls handle(text, ctx) first;
#it returns True when the request was about music. A watcher thread tells the gui what's playing.
#
#How it stays safe:
#  - sign-in is spotify's own page in your browser, with PKCE: navi never sees your password, has no client secret,
#    and a code caught by another program is useless without the one-time verifier that never leaves navi
#  - the sign-in answer comes back to a tiny server on 127.0.0.1 only (not reachable from the network), on one path,
#    for one sign-in, and only if its random state matches. Nothing it receives is shown or run
#  - the token is saved in spotify_token.json next to navi (gitignored, left out of the installer, readable only by
#    you on mac/linux) and never printed or logged
#  - navi only talks to accounts.spotify.com and api.spotify.com over https. Song ids from spotify are checked
#    against spotify's own id format before they're used, and what you say is only ever sent as a search query
#  - album art is only fetched over https from spotify's image servers, with a size limit (see art_url_ok)
import base64
import difflib
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

import requests

TOKEN_FILE="spotify_token.json"
REDIRECT_PORT=43019
REDIRECT_URI=f"http://127.0.0.1:{REDIRECT_PORT}/callback" #must match the redirect uri in your spotify app exactly
AUTH_URL="https://accounts.spotify.com/authorize"
TOKEN_URL="https://accounts.spotify.com/api/token"
API="https://api.spotify.com/v1"
SCOPES="user-read-playback-state user-modify-playback-state user-read-currently-playing user-library-read user-library-modify playlist-read-private"
DASHBOARD="https://developer.spotify.com/dashboard"
WEB_PLAYER="https://open.spotify.com"
CLIENT_ID_RE=re.compile(r"^[0-9a-fA-F]{32}$")
ID_RE=re.compile(r"^[0-9A-Za-z]{22}$")
URI_RE=re.compile(r"^spotify:(track|album|artist|playlist|episode|show):[0-9A-Za-z]{22}$")
ART_HOSTS=("i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com", "image-cdn-fa.spotifycdn.com", "thisis-images.spotifycdn.com",
           "seed-mix-image.spotifycdn.com", "pickasso.spotifycdn.com", "blend-playlist-covers.spotifycdn.com", "charts-images.scdn.co",
           "daily-mix.scdn.co", "newjams-images.scdn.co", "lineup-images.scdn.co", "wrapped-images.spotifycdn.com")
ART_MAX_BYTES=2_000_000
TIMEOUT=10
OWNER_HELP=("Spotify is blocking my Spotify app because the Spotify account that created it in the developer dashboard "
            "doesn't have Premium. Spotify requires that now. Make the app with a Premium account and put its client ID in "
            "Settings, then Keys. If you just got Premium, it can take Spotify a few hours to notice.")
SETUP_HELP=("To use Spotify, open Settings, then Keys, and add a Spotify client ID. The Music section there says how to "
            "make one. Then say connect Spotify.")

class Context:
    #what the music commands need from voice.py, so this file can be tested on its own
    def __init__(self, say, hear, said_yes, data_path, post=lambda kind, value: None, open_url=lambda url: None,
                 media_key=lambda action: None):
        self.say=say
        self.hear=hear
        self.said_yes=said_yes
        self.data_path=data_path #a file next to voice.py
        self.post=post #a line (or the now playing info) for the gui
        self.open_url=open_url
        self.media_key=media_key #media_key("play_pause" / "next" / "previous"): the keyboard's media keys, for when spotify isn't set up

class SpotifyError(Exception):
    def __init__(self, status, reason="", message=""):
        super().__init__(f"{status} {reason} {message}".strip())
        self.status=status
        self.reason=reason
        self.message=message

class NotConnected(Exception):
    pass

class NoDevice(Exception):
    pass

def client_id():
    value=os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    return value if CLIENT_ID_RE.match(value) else ""

problem="" #why spotify is refusing navi right now ("" when it's fine), shown in settings > music
problem_reason=""

def note_problem(reason, text):
    global problem, problem_reason
    problem, problem_reason=text, reason

#tokens
_lock=threading.RLock()
_token=None #cached token dict
_data_path=lambda name: name

def _token_path():
    return _data_path(TOKEN_FILE)

def load_token():
    global _token
    with _lock:
        if _token is None:
            try:
                with open(_token_path(), encoding="utf-8") as f:
                    data=json.load(f)
                if isinstance(data, dict) and isinstance(data.get("refresh_token"), str):
                    _token=data
            except (OSError, ValueError):
                _token=None
        if _token and _token.get("client_id")!=client_id(): #a different spotify app: its tokens don't work
            return None
        return _token

def save_token(data):
    global _token
    with _lock:
        path=_token_path()
        tmp=path+".tmp"
        fd=os.open(tmp, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
        if os.name!="nt":
            os.chmod(path, 0o600)
        _token=data

def forget_token():
    global _token
    with _lock:
        _token=None
        try:
            os.remove(_token_path())
        except OSError:
            pass

def connected():
    return bool(client_id() and load_token())

def _store(answer, old_refresh=None):
    if not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str):
        raise SpotifyError(0, "bad_token", "Spotify sent back an unexpected answer.")
    data={"access_token": answer["access_token"], "refresh_token": answer.get("refresh_token") or old_refresh,
          "expires_at": time.time()+int(answer.get("expires_in", 3600))-60, "client_id": client_id(),
          "scope": str(answer.get("scope", ""))}
    if not isinstance(data["refresh_token"], str):
        raise SpotifyError(0, "bad_token", "Spotify didn't send a refresh token.")
    save_token(data)
    return data

def access_token():
    with _lock:
        token=load_token()
        if not token:
            raise NotConnected()
        if time.time()<token.get("expires_at", 0):
            return token["access_token"]
        r=requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"],
                                         "client_id": client_id()}, timeout=TIMEOUT)
        if r.status_code==400: #revoked, or the app was deleted: sign in again
            forget_token()
            raise NotConnected()
        if r.status_code!=200:
            raise SpotifyError(r.status_code, "refresh_failed")
        return _store(r.json(), token["refresh_token"])["access_token"]

#sign in (authorization code with PKCE)
def pkce_pair():
    verifier=secrets.token_urlsafe(64)[:128] #43 to 128 characters
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge

def auth_link(challenge, state):
    return AUTH_URL+"?"+urllib.parse.urlencode({"client_id": client_id(), "response_type": "code", "redirect_uri": REDIRECT_URI,
                                                 "code_challenge_method": "S256", "code_challenge": challenge,
                                                 "state": state, "scope": SCOPES})

PAGE="""<!doctype html><meta charset="utf-8"><title>Navi</title>
<body style="font-family:Segoe UI,sans-serif;background:#1e1f26;color:#e6e6eb;display:grid;place-items:center;height:90vh">
<div style="text-align:center"><h2>%s</h2><p>You can close this tab.</p></div>"""

class _Callback(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        url=urllib.parse.urlsplit(self.path)
        if url.path!="/callback":
            self.send_error(404)
            return
        q=urllib.parse.parse_qs(url.query)
        state=q.get("state", [""])[0]
        result=self.server.result
        if result.get("done") or not hmac.compare_digest(state.encode(), self.server.state.encode()):
            self.send_error(400) #not the sign-in navi started
            return
        result["done"]=True
        if "code" in q:
            result["code"]=q["code"][0]
            message="Navi is connected to Spotify."
        else:
            result["error"]=q.get("error", ["unknown"])[0][:40]
            message="Spotify sign-in was cancelled."
        body=(PAGE % message).encode("utf-8") #fixed text only, nothing from the request
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    timeout=5 #a connection that never sends anything can't hold up the sign-in

    def log_message(self, *args): #never print the request: it holds the code
        pass

def sign_in(open_url, wait=300):
    #opens spotify's sign-in page and waits for the answer. Returns "ok", or why it didn't work
    if not client_id():
        return "no_client_id"
    verifier, challenge=pkce_pair()
    state=secrets.token_urlsafe(24)
    try:
        server=http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _Callback)
    except OSError:
        return "port_busy"
    server.state=state
    server.result={}
    server.timeout=1
    try:
        open_url(auth_link(challenge, state))
        end=time.monotonic()+wait
        while not server.result.get("done") and time.monotonic()<end:
            server.handle_request()
    finally:
        server.server_close()
    if "code" not in server.result:
        return "cancelled" if server.result.get("done") else "timeout"
    r=requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": server.result["code"], "redirect_uri": REDIRECT_URI,
                                     "client_id": client_id(), "code_verifier": verifier}, timeout=TIMEOUT)
    if r.status_code!=200:
        print("spotify sign in err;", r.status_code)
        return "rejected"
    _store(r.json())
    return "ok"

#the web api
def call(method, path, params=None, body=None, retry=True):
    r=requests.request(method, API+path, params=params, json=body, timeout=TIMEOUT,
                       headers={"Authorization": "Bearer "+access_token()})
    if r.status_code==401 and retry: #the token ran out early
        with _lock:
            if _token:
                _token["expires_at"]=0
        return call(method, path, params, body, retry=False)
    if r.status_code==429 and retry:
        try:
            wait=int(r.headers.get("Retry-After", "1"))
        except ValueError:
            wait=1
        time.sleep(max(0, min(wait, 5)))
        return call(method, path, params, body, retry=False)
    if r.status_code==204 or not r.content:
        if r.status_code>=400:
            raise SpotifyError(r.status_code)
        if problem_reason=="OWNER_PREMIUM":
            note_problem("", "")
        return None
    try:
        data=r.json()
    except ValueError:
        data=None
    if r.status_code>=400:
        err=(data or {}).get("error", {}) if isinstance(data, dict) else {}
        err=err if isinstance(err, dict) else {"message": str(err)}
        message=str(err.get("message", "")) if data is not None else r.text[:300] #some refusals are plain text, not json
        reason=str(err.get("reason", ""))
        if r.status_code==403 and "premium subscription required for the owner" in message.lower():
            reason="OWNER_PREMIUM" #spotify's rule for developer apps: the account that made the app needs premium
            note_problem(reason, "Spotify is refusing this app: the account that created it in the Spotify developer "
                                 "dashboard needs Premium. Make the app with a Premium account and put its client ID on the Keys page.")
        raise SpotifyError(r.status_code, reason, message)
    if problem_reason=="OWNER_PREMIUM":
        note_problem("", "")
    return data

def playback():
    #what's playing right now, or None
    return call("GET", "/me/player", {"additional_types": "track,episode"})

def devices():
    data=call("GET", "/me/player/devices") or {}
    return [d for d in data.get("devices", []) if isinstance(d, dict) and d.get("id") and not d.get("is_restricted")]

def pick_device(found):
    #the active one, else this pc, else a phone, else anything
    for test in (lambda d: d.get("is_active"), lambda d: d.get("type")=="Computer", lambda d: d.get("type")=="Smartphone", lambda d: True):
        for d in found:
            if test(d):
                return d
    return None

def transfer(device_id, play=True):
    call("PUT", "/me/player", body={"device_ids": [device_id], "play": play})
    time.sleep(0.6) #spotify takes a moment to switch

def player(method, path, params=None, body=None, wake=False):
    #a playback command. With no active device: wake=True moves playback to one of your devices first
    try:
        return call(method, path, params, body)
    except SpotifyError as e:
        if e.status!=404 and e.reason!="NO_ACTIVE_DEVICE":
            raise
    if not wake:
        raise NoDevice()
    d=pick_device(devices())
    if d is None:
        raise NoDevice()
    transfer(d["id"], play=False)
    try:
        return call(method, path, dict(params or {}, device_id=d["id"]), body)
    except SpotifyError as e:
        if e.status==404:
            raise NoDevice()
        raise

def wait_for_device(seconds=25):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        time.sleep(2)
        try:
            d=pick_device(devices())
        except (SpotifyError, requests.RequestException):
            d=None
        if d:
            return d
    return None

#now playing, for the gui
def clean(text, limit=200):
    #one line, no characters outside the basic plane (old tk versions can't draw emoji)
    text=re.sub(r"\s+", " ", str(text or "")).strip()
    return "".join(ch for ch in text if ord(ch)<=0xFFFF)[:limit]

def art_url_ok(url):
    try:
        u=urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme=="https" and (u.hostname or "") in ART_HOSTS and u.port in (None, 443) and not u.username and not u.password

def fetch_art(url):
    #album art bytes, or None. Only from spotify's image servers, at most ART_MAX_BYTES, no redirects
    if not art_url_ok(url):
        return None
    try:
        with requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=False) as r:
            if r.status_code!=200 or not r.headers.get("Content-Type", "").startswith("image/"):
                return None
            data=b""
            for chunk in r.iter_content(65536):
                data+=chunk
                if len(data)>ART_MAX_BYTES:
                    return None
            return data
    except requests.RequestException:
        return None

def now_playing_info(state):
    #the parts of spotify's playback state the gui shows, as plain strings and numbers
    if not isinstance(state, dict):
        return None
    item=state.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type")=="episode":
        artist=(item.get("show") or {}).get("name", "")
        images=item.get("images") or (item.get("show") or {}).get("images") or []
    else:
        artist=", ".join(a.get("name", "") for a in item.get("artists") or [] if isinstance(a, dict))
        images=(item.get("album") or {}).get("images") or []
    images=[i for i in images if isinstance(i, dict) and isinstance(i.get("url"), str)]
    images.sort(key=lambda i: abs((i.get("width") or 300)-64)) #the smallest one that still looks sharp
    art=next((i["url"] for i in images if art_url_ok(i["url"])), "")
    return {"title": clean(item.get("name")), "type": clean(item.get("type"), 10), "artist": clean(artist), "art": art, "id": clean(item.get("id"), 40),
            "playing": bool(state.get("is_playing")), "progress": int(state.get("progress_ms") or 0),
            "duration": int(item.get("duration_ms") or 0), "device": clean((state.get("device") or {}).get("name"), 60),
            "shuffle": bool(state.get("shuffle_state")), "repeat": clean(state.get("repeat_state"), 10), "at": time.monotonic()}

#without premium: the spotify app on this pc. Its window title is "Artist - Song" while a song plays and "Spotify
#Free" (or similar) while paused, so navi reads the song from there and uses the keyboard's media keys to control it
IDLE_TITLES={"", "spotify", "spotify free", "spotify premium"}

def spotify_titles():
    #titles of the spotify app's windows on this pc ([] if it isn't running, or not on windows)
    if os.name!="nt":
        return []
    import ctypes
    from ctypes import wintypes
    user32=ctypes.WinDLL("user32") #its own copy, so voice.py's argtypes on windll.user32 aren't touched
    kernel32=ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype=wintypes.HANDLE
    kernel32.OpenProcess.argtypes=[wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    user32.GetWindowTextLengthW.argtypes=[wintypes.HWND]
    user32.GetWindowTextW.argtypes=[wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes=[wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    names={}
    def exe(pid):
        if pid not in names:
            names[pid]=""
            h=kernel32.OpenProcess(0x1000, False, pid) #PROCESS_QUERY_LIMITED_INFORMATION: only its name
            if h:
                buf=ctypes.create_unicode_buffer(520)
                size=wintypes.DWORD(520)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    names[pid]=os.path.basename(buf.value).lower()
                kernel32.CloseHandle(h)
        return names[pid]
    titles=[]
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        n=user32.GetWindowTextLengthW(hwnd)
        if 0<n<=500:
            cls=ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value.startswith("Chrome_WidgetWin"): #spotify's main window (its other windows are helpers)
                pid=wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if exe(pid.value)=="spotify.exe":
                    buf=ctypes.create_unicode_buffer(n+1)
                    user32.GetWindowTextW(hwnd, buf, n+1)
                    titles.append(buf.value)
        return True
    user32.EnumWindows(each, 0)
    return titles

def local_now_playing(last=None):
    #the song in the spotify app on this pc, or None. last: what was shown before, kept while it's paused
    titles=spotify_titles()
    if not titles:
        return None
    song=next((t for t in titles if t.strip().lower() not in IDLE_TITLES and " - " in t), None)
    if song is None: #the app is open but paused
        if last and last.get("source")=="local":
            return dict(last, playing=False, at=time.monotonic())
        return None
    artist, title=song.split(" - ", 1)
    return {"title": clean(title), "artist": clean(artist), "art": "", "id": clean(song, 200), "type": "track", "playing": True,
            "progress": 0, "duration": 0, "device": "this PC", "shuffle": False, "repeat": "", "at": time.monotonic(), "source": "local"}

def api_usable():
    #spotify's web api works for you: connected, and not refused for lack of premium
    return connected() and problem_reason!="OWNER_PREMIUM"

#youtube: plays a song by name, free. Needs a youtube data api key (google cloud), which only goes to googleapis.com
YOUTUBE_SEARCH="https://www.googleapis.com/youtube/v3/search"
VIDEO_ID_RE=re.compile(r"^[A-Za-z0-9_-]{11}$")

def youtube_key():
    value=os.environ.get("YOUTUBE_API_KEY", "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{30,60}", value) else ""

def youtube_find(query):
    #(video id, title, channel) of the best music video for what you said, or None. The key is sent in a header, so it's
    #never part of a url that could end up in an error message
    import html as html_text
    for extra in ({"videoCategoryId": "10"}, {}): #music first, then anything
        r=requests.get(YOUTUBE_SEARCH, params=dict({"part": "snippet", "type": "video", "maxResults": 1, "q": query[:200]}, **extra),
                       headers={"X-Goog-Api-Key": youtube_key()}, timeout=TIMEOUT, allow_redirects=False)
        if r.status_code!=200:
            try:
                reason=str(((r.json().get("error") or {}).get("errors") or [{}])[0].get("reason", ""))
            except (ValueError, AttributeError, IndexError):
                reason=""
            raise SpotifyError(r.status_code, "YOUTUBE_"+reason)
        for item in (r.json() or {}).get("items", []):
            vid=str((item.get("id") or {}).get("videoId", ""))
            if VIDEO_ID_RE.match(vid):
                s=item.get("snippet") or {}
                return vid, clean(html_text.unescape(str(s.get("title", "")))), clean(html_text.unescape(str(s.get("channelTitle", ""))))
    return None

def play_youtube(query, ctx):
    if not youtube_key():
        ctx.say("To play songs on YouTube, add a YouTube API key in Settings, then Keys. It's free, and the Keys page has a Get button.")
        return
    try:
        found=youtube_find(query)
    except SpotifyError as e:
        if "quota" in e.reason.lower() or "limit" in e.reason.lower():
            ctx.say("YouTube's free daily limit for searches is used up. It resets tomorrow.")
        elif e.status in (400, 403):
            ctx.say("YouTube didn't accept the API key. Check it in Settings, then Keys.")
        else:
            ctx.say("YouTube had a problem with that. Try again in a moment.")
        return
    except requests.RequestException:
        ctx.say("I couldn't reach YouTube. Check your internet connection.")
        return
    if not found:
        ctx.say(f"I couldn't find {query} on YouTube.")
        return
    vid, title, channel=found
    ctx.open_url("https://www.youtube.com/watch?v="+vid)
    ctx.say(f"Playing {title} on YouTube.")

def open_spotify_search(query, ctx):
    #the spotify app's search (or the web player's, without the app), so you can press play
    q=urllib.parse.quote(query[:100], safe="")
    app=False
    if os.name=="nt":
        try:
            import winreg
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\spotify"))
            app=True
        except OSError:
            app=False
    ctx.open_url("spotify:search:"+q if app else WEB_PLAYER+"/search/"+q)

NO_PREMIUM_LIMITS="Without Spotify Premium I can only pause, skip, go back and tell you what's playing in the Spotify app on this PC."

def local_handle(action, arg, t, ctx):
    #every request when spotify's web api isn't usable: the spotify app on this pc, media keys and youtube
    if action=="pause" and "pause" not in t:
        return False #"stop" on its own isn't worth pressing play/pause for (it would start music if none was playing)
    if action in ("pause", "resume", "next", "previous"):
        ctx.media_key({"pause": "play_pause", "resume": "play_pause"}.get(action, action))
        if watcher is not None:
            poke()
        return True
    if action=="what":
        info=local_now_playing(watcher.last if watcher is not None else None)
        if info:
            ctx.say(("This is " if info["playing"] else "Paused on ")+info["title"]+(f" by {info['artist']}" if info["artist"] else "")+".")
        elif spotify_titles():
            ctx.say("Spotify is open, but nothing is playing.")
        else:
            return False #maybe it's playing somewhere else: let the ai answer
        return True
    if action in ("play", "queue", "shuffle_play") and arg:
        kind, name, artist=parse_request(arg)
        query=" ".join(w for w in (name, artist) if w).strip() or arg
        if youtube_key() and "spotify" not in t:
            play_youtube(query, ctx)
        else:
            open_spotify_search(query, ctx)
            ctx.say(f"I opened the search for {query} in Spotify. Press play there."+("" if youtube_key() else
                    " Add a YouTube key in Settings, then Keys, and I can play songs by name on YouTube."))
        return True
    ctx.say(NO_PREMIUM_LIMITS)
    return True

class Watcher(threading.Thread):
    #asks spotify what's playing every few seconds (only while you're connected) and posts it to the gui
    def __init__(self, ctx):
        super().__init__(daemon=True)
        self.ctx=ctx
        self.wake=threading.Event()
        self.last=None
        self.api_retry=0 #after spotify refuses the app, its api is asked again only every 10 minutes

    def refresh(self):
        self.wake.set()

    def run(self):
        while True:
            info=None
            wait=2
            used_api=False
            if connected() and time.time()>=self.api_retry:
                try:
                    info=now_playing_info(playback())
                    wait=3 if info and info["playing"] else 6
                    used_api=True
                except NotConnected:
                    pass
                except requests.RequestException:
                    info, wait, used_api=self.last, 15, True #keep showing it through a network blip
                except (SpotifyError, ValueError) as e:
                    if getattr(e, "status", 0)==403: #a refused app stays refused for a while: don't keep asking
                        self.api_retry=time.time()+600
                    else:
                        wait, used_api=15, True
            if not used_api:
                try:
                    info=local_now_playing(self.last)
                except OSError:
                    info=None
            key=None if info is None else (info["id"], info["playing"], info["title"], info["shuffle"], info["repeat"], info["progress"]//4000)
            if key!=(None if self.last is None else self.last.get("_key")):
                if info is not None:
                    info["_key"]=key
                self.ctx.post("music", info)
            self.last=info
            self.wake.wait(wait)
            self.wake.clear()

watcher=None

def start(ctx):
    global watcher, _data_path
    _data_path=ctx.data_path
    if watcher is None:
        watcher=Watcher(ctx)
        watcher.start()
    return watcher

def poke():
    if watcher is not None:
        threading.Timer(0.5, watcher.refresh).start()

#commands
def norm(text):
    text=text.lower().replace("’", "'")
    text=re.sub(r"[^\w\s'&-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def strip_polite(t):
    return re.sub(r"^(?:(?:hey |ok |okay )?navi |please |can you |could you |would you |will you |go ahead and |i want to |i wanna |let's |lets )+", "", t).strip()

def similar(a, b):
    a, b=norm(a), norm(b)
    return 1.0 if a==b else difflib.SequenceMatcher(None, a, b).ratio()

DEVICE_WORDS={"phone": "Smartphone", "computer": "Computer", "pc": "Computer", "laptop": "Computer", "speaker": "Speaker",
              "tv": "TV", "television": "TV", "tablet": "Tablet", "this pc": "Computer", "this computer": "Computer"}
NOT_MUSIC=r"\b(timer|stopwatch|stop ?watch|alarm|reminder|focus|pomodoro|video|youtube|game|minecraft|shut ?down|restart the (pc|computer)|talking)\b"
MUSIC_WORDS=r"(?:the |my |this |some )?(?:music|song|track|spotify|playback|tune|tunes|playlist|album|podcast|episode)"

def is_music(t):
    #the requests this file takes (t is already normalized and stripped of "please" and the like)
    return command(t)[0] is not None

def command(t):
    #(action, argument) or (None, None)
    if re.search(r"\b(connect|link|sign in to|sign into|log in to|login to|set up|setup|hook up)\b.*\bspotify\b", t):
        return "connect", None
    if re.search(r"\b(disconnect|unlink|sign out of|log out of|logout of|forget)\b.*\bspotify\b", t):
        return "disconnect", None
    if re.search(r"\bspotify devices\b|\b(?:what|which) devices (?:can|does|is|are) spotify\b|\bwhere is (?:the )?music playing\b", t):
        return "devices", None
    if re.search(r"\bwhat(?:'s| is) (?:playing|this song|the song|this track|that song|this music)\b|^what(?:'s| is) this$|\bwhat song\b|\bwhat track\b|"
                 r"\bwho (?:sings|is singing|sang|made) (?:this|that)\b|\bname (?:of )?(?:this|the|that) song\b|\bwhat am i listening to\b|"
                 r"\bwhat(?:'s| is) (?:currently |now )playing\b", t):
        return "what", None
    m=re.match(r"^(?:play|put on|throw on|blast|queue up|queue)\s+(.+?)\s+(?:on|from|in|using) (?:youtube|you tube)$", t)
    if m:
        return "youtube", m.group(1)
    if re.search(NOT_MUSIC, t):
        return None, None
    if re.search(r"^(?:(?:like|save|love|heart)(?: this| the| that)?(?: song| track)?|add (?:this|the|that) (?:song|track) to my (?:liked songs|library|favorites)|"
                 r"i (?:like|love) this song)$", t):
        return "like", None
    if re.search(r"\bshuffle\b|\bshuffling\b", t):
        if re.search(r"\b(off|stop|don't|do not|no more|disable|turn off|without)\b", t):
            return "shuffle", False
        m=re.match(r"^play (.+?) on shuffle$", t)
        if m:
            return "shuffle_play", m.group(1)
        m=re.match(r"^(?:turn on |start |play (?:on )?)?(?:shuffle|shuffling)(?: on| mode| play)?\b\s*(.*)$", t)
        rest=m.group(1).strip() if m else ""
        return ("shuffle_play", rest) if rest and not re.fullmatch(MUSIC_WORDS+r"?|on|it|this|everything", rest) else ("shuffle", True)
    if re.search(r"\b(?:repeat|loop)(?:ing)? (?:this |the |that |my )?(?:song|track|playlist|album)\b|^(?:turn |put )?(?:on )?(?:repeat|loop)(?: mode)? (?:on|off)$|"
                 r"^(?:turn|switch) (?:on|off) (?:repeat|loop)(?: mode)?$|\bon repeat\b|\bstop (?:repeating|looping)\b|^loop(?: it| this)?$", t):
        if re.search(r"\b(off|stop|don't|do not|no more|disable)\b", t):
            return "repeat", "off"
        if re.search(r"\b(playlist|album|all)\b", t):
            return "repeat", "context"
        return "repeat", "track"
    m=re.search(r"\b(?:spotify|music|song|playback) volume (?:to |at )?(\d{1,3})\b|\bvolume (?:of|on|for) (?:the )?(?:spotify|music|song) (?:to |at )?(\d{1,3})\b|"
                r"\bset (?:the )?(?:spotify|music) (?:volume )?to (\d{1,3})\b", t)
    if m:
        return "volume", int(next(g for g in m.groups() if g))
    m=re.search(r"\b(?:turn|make) (?:the )?(?:music|spotify|song) (up|down|louder|quieter|softer)\b|\bturn (up|down) (?:the )?(?:music|spotify|song)\b|\b(?:music|spotify|song) (louder|quieter|softer|up|down)\b|"
                r"\b(?:spotify|music) volume (up|down)\b", t)
    if m:
        word=next(g for g in m.groups() if g)
        return "volume_step", 15 if word in ("up", "louder") else -15
    if re.search(r"\b(?:restart|replay) (?:this|the|that) (?:song|track)\b|\bstart (?:this|the) (?:song|track) (?:over|again)\b|"
                 r"\b(?:play|start) (?:this|the|it) (?:song |track )?from the (?:beginning|start)\b|^play (?:that|this|it) again$", t):
        return "seek_to", 0
    m=re.search(r"\b(?:skip|fast forward|go|jump)(?: ahead| forward| forwards)?(?: by)? (\d{1,3}) seconds?\b|\b(?:rewind|go back|skip back|jump back)(?: by)? (\d{1,3}) seconds?\b", t)
    if m:
        return "seek_by", int(m.group(1)) if m.group(1) else -int(m.group(2))
    m=re.search(r"^(?:switch|move|transfer|send|put)(?: the)?(?: music| spotify| playback| song| it| this)? (?:to|onto|over to) (?:my |the )?(.+)$|"
                r"^(?:play|continue|keep playing)(?: the)?(?: music| this| it| spotify| song)? on (?:my |the )(.+)$", t)
    if m:
        target=next(g for g in m.groups() if g).strip()
        device_word=re.sub(r"^(?:my|the|this) ", "", target) in DEVICE_WORDS or target in DEVICE_WORDS
        asked=re.search(r"\b(music|spotify|playback|song|it|this)\b", t.split(" to ")[0].split(" on ")[0])
        if target not in ("spotify", "next song", "next track", "next one") and (device_word or asked):
            return "transfer", target
    if re.search(r"^(?:pause|stop)$|^(?:pause|stop)(?: playing)? (?:the |my |this )?(?:music|song|track|spotify|playback|podcast|it|that)(?: for now| please)?$|"
                 r"^pause (?:it|this|that)|^(?:turn off|shut off|kill) (?:the )?(?:music|spotify)$|^(?:pause|stop) spotify\b", t):
        return "pause", None
    if re.search(r"^(?:resume|unpause|continue)(?: playing)?(?: the| my)?(?: music| song| track| spotify| playback| podcast| it)?$|"
                 r"^(?:play|start|turn on)(?: playing)?(?: the| some| my)? (?:music|spotify|tunes)$|^play$|^(?:keep|continue) playing\b|^(?:play|resume) it$|"
                 r"^(?:start|turn on) (?:the )?music$", t):
        return "resume", None
    if re.search(r"^(?:next|skip)(?: it| this| that| one)?$|\b(?:next|skip)(?: this| the| that| to the next)? (?:song|track|one)\b|"
                 r"^(?:play )?(?:the )?next (?:song|track)\b|^skip (?:it|this|that)\b|^(?:skip|next) (?:on )?spotify$", t):
        return "next", None
    if re.search(r"^(?:previous|back|go back)(?: song| track| one)?$|\b(?:previous|last) (?:song|track)\b|\bgo back (?:a|one) (?:song|track)\b|"
                 r"\bprevious one\b", t):
        return "previous", None
    m=re.search(r"^(?:play|queue up|queue|add|put on|throw on|blast)\b\s+(.+)$", t)
    if m:
        what=m.group(1).strip()
        queue=t.startswith("queue") or t.startswith("add")
        if t.startswith("add") and not re.search(r"\bto (?:the |my )?queue\b", what):
            return None, None #"add milk to my list" isn't music
        what=re.sub(r"\s+(?:to|on|in) (?:the |my )?queue$|\s+next$", "", what).strip() if queue else what
        if what and not re.fullmatch(r"(?:a |some )?(?:game|games|video|videos|minecraft)", what):
            return ("queue" if queue else "play"), what
    return None, None

def parse_request(what):
    #"the album thriller by michael jackson" -> ("album", "thriller", "michael jackson")
    what=re.sub(r"\s+(?:on|from|in|using|with) spotify$|\s+for me$|\s+please$", "", what).strip()
    what=re.sub(r"^(?:the |a |an )", "", what) if not re.match(r"^(?:the )?(?:album|playlist|song|track|artist|podcast)\b", what) else what
    if re.fullmatch(r"(?:my |the )?(?:liked songs|liked|favorites|favourites|favorite songs|saved songs|library|likes|my songs)", what):
        return "liked", "", ""
    m=re.match(r"^(?:my |the )?playlist (?:called |named )?(.+)$|^my (.+?) playlist$|^(?:the )?(.+?) playlist$", what)
    if m:
        name=next(g for g in m.groups() if g)
        return "playlist", name, "mine" if what.startswith("my ") or what.startswith("playlist") else ""
    kind="any"
    m=re.match(r"^(?:the )?(album|song|track|artist|podcast) (?:called |named )?(.+)$", what)
    if m:
        kind={"track": "song"}.get(m.group(1), m.group(1))
        what=m.group(2)
    m=re.match(r"^(?:songs by |music by |something by |anything by |stuff by )(.+)$", what)
    if m:
        return "artist", m.group(1), ""
    m=re.match(r"^(?:some |more )(.+)$", what)
    if m and kind=="any":
        return "some", m.group(1), "" #an artist, else a genre or mood playlist
    m=re.match(r"^(.+?) by (.+)$", what)
    if m:
        return (kind if kind!="any" else "song"), m.group(1), m.group(2)
    return kind, what, ""

def first(items):
    return next((i for i in items if isinstance(i, dict) and URI_RE.match(str(i.get("uri", "")))), None)

def search(kind, name, artist):
    #(what to play, how to say it) for a spoken request, or (None, None)
    if kind=="liked":
        data=call("GET", "/me/tracks", {"limit": 50}) or {}
        uris=[i["track"]["uri"] for i in data.get("items", []) if isinstance(i, dict) and isinstance(i.get("track"), dict)
              and URI_RE.match(str(i["track"].get("uri", "")))]
        return ({"uris": uris}, "your liked songs") if uris else (None, None)
    if kind=="playlist":
        data=call("GET", "/me/playlists", {"limit": 50}) or {}
        mine=[p for p in data.get("items", []) if isinstance(p, dict) and URI_RE.match(str(p.get("uri", "")))]
        best=max(mine, key=lambda p: similar(p.get("name", ""), name), default=None)
        if best and similar(best.get("name", ""), name)>=0.75:
            return {"context_uri": best["uri"]}, "your playlist "+clean(best.get("name"))
        found=first((call("GET", "/search", {"q": name, "type": "playlist", "limit": 5}) or {}).get("playlists", {}).get("items", []))
        return ({"context_uri": found["uri"]}, "the playlist "+clean(found.get("name"))) if found else (None, None)
    q=name if not artist else f"{name} artist:{artist}"
    types={"song": "track", "album": "album", "artist": "artist", "podcast": "show", "some": "artist,playlist,track"}.get(kind, "track,artist")
    data=call("GET", "/search", {"q": q[:200], "type": types, "limit": 5}) or {}
    tracks=[t for t in (data.get("tracks") or {}).get("items", []) if isinstance(t, dict) and URI_RE.match(str(t.get("uri", "")))]
    artists=[a for a in (data.get("artists") or {}).get("items", []) if isinstance(a, dict) and URI_RE.match(str(a.get("uri", "")))]
    if kind in ("artist", "any", "some") and artists and (kind=="artist" or similar(artists[0].get("name", ""), name)>=(0.8 if kind=="some" else 0.9)):
        a=artists[0]
        return {"context_uri": a["uri"]}, clean(a.get("name"))
    if kind=="album":
        a=first((data.get("albums") or {}).get("items", []))
        if a:
            by=", ".join(x.get("name", "") for x in a.get("artists", []) if isinstance(x, dict))
            return {"context_uri": a["uri"]}, f"{clean(a.get('name'))} by {clean(by)}"
        return None, None
    if kind=="some":
        p=first((data.get("playlists") or {}).get("items", []))
        if p:
            return {"context_uri": p["uri"]}, clean(p.get("name"))
    if kind=="podcast":
        s=first((data.get("shows") or {}).get("items", []))
        return ({"context_uri": s["uri"]}, clean(s.get("name"))) if s else (None, None)
    if tracks:
        t=tracks[0]
        by=", ".join(x.get("name", "") for x in t.get("artists", []) if isinstance(x, dict))
        album=(t.get("album") or {}).get("uri", "")
        body={"context_uri": album, "offset": {"uri": t["uri"]}} if URI_RE.match(str(album)) else {"uris": [t["uri"]]}
        return body, f"{clean(t.get('name'))} by {clean(by)}"
    if artists:
        return {"context_uri": artists[0]["uri"]}, clean(artists[0].get("name"))
    return None, None

def start_playing(ctx, body=None):
    #plays (or resumes); if none of your devices is on, offers to open the web player
    try:
        player("PUT", "/me/player/play", body=body, wake=True)
        return True
    except NoDevice:
        pass
    ctx.say("None of your Spotify devices are on. I'm opening the Spotify web player. Give it a few seconds.")
    ctx.open_url(WEB_PLAYER)
    d=wait_for_device()
    if d is None:
        ctx.say("I still can't see a Spotify device. Open Spotify on your phone or this PC and ask me again.")
        return False
    transfer(d["id"], play=False)
    try:
        call("PUT", "/me/player/play", {"device_id": d["id"]}, body)
    except SpotifyError:
        ctx.say("The web player is open. Press play there once, then I can control it.")
        return False
    return True

def say_error(ctx, e):
    if isinstance(e, SpotifyError) and e.reason=="OWNER_PREMIUM":
        ctx.say(OWNER_HELP)
    elif isinstance(e, NotConnected):
        ctx.say("Spotify isn't connected anymore. Say connect Spotify to sign in again.")
    elif isinstance(e, requests.RequestException):
        ctx.say("I couldn't reach Spotify. Check your internet connection.")
    elif premium_refusal(e):
        ctx.say("Spotify only lets apps control playback on a Premium account. I can still tell you what's playing.")
    elif e.status==403:
        ctx.say("Spotify didn't allow that right now. Some songs and devices can't be controlled.")
    elif e.status==429:
        ctx.say("Spotify says I'm asking too often. Try again in a minute.")
    else:
        ctx.say("Spotify had a problem with that. Try again in a moment.")
        print("spotify err;", e.status, e.reason)

def premium_refusal(e):
    return isinstance(e, SpotifyError) and e.status==403 and ("PREMIUM" in e.reason.upper() or "premium" in e.message.lower())

def explain_once(ctx, e):
    #why spotify refused, said the first time only (not on every pause and skip)
    if not getattr(ctx, "told_about_premium", False):
        ctx.told_about_premium=True
        say_error(ctx, e)

signing_in=threading.Lock()

def connect(ctx, done=None):
    #signs in on another thread (it can take minutes), so navi keeps listening. done(result) is called after
    if not client_id():
        ctx.say(SETUP_HELP)
        return
    if not signing_in.acquire(blocking=False):
        ctx.say("I'm already waiting for you to sign in to Spotify in your browser.")
        return
    ctx.say("Sign in to Spotify in the browser window I'm opening.")
    def work():
        try:
            result=finish_connect(ctx)
        finally:
            signing_in.release()
        if done:
            done(result)
    threading.Thread(target=work, daemon=True).start()

def finish_connect(ctx):
    ctx.post("system", "Sign in to Spotify in your browser. If it says the redirect URI is not valid, add "
                       f"{REDIRECT_URI} to your app in the Spotify dashboard.")
    try:
        result=sign_in(ctx.open_url)
        if result=="ok":
            call("GET", "/me") #signed in, but does spotify let this app in?
    except SpotifyError as e:
        result="owner_premium" if e.reason=="OWNER_PREMIUM" else "network"
    except requests.RequestException:
        result="network"
    except NotConnected:
        result="rejected"
    ctx.say({"owner_premium": "You're signed in, but "+OWNER_HELP[0].lower()+OWNER_HELP[1:],"ok": "Spotify is connected. Try saying play some music.",
             "port_busy": f"Something else on this PC is using port {REDIRECT_PORT}, so I can't finish signing in. Close it and try again.",
             "cancelled": "Okay, I didn't connect Spotify.",
             "timeout": "The sign-in took too long. Say connect Spotify to try again.",
             "rejected": "Spotify didn't accept the sign-in. Check the client ID in Settings, then Keys.",
             "network": "I couldn't reach Spotify. Check your internet connection."}.get(result, "That didn't work."))
    poke()
    return result

def handle(text, ctx):
    #True if this was a music request (and it's been dealt with)
    global _data_path
    _data_path=ctx.data_path
    t=strip_polite(norm(text))
    action, arg=command(t)
    if action is None:
        return False
    if action=="connect":
        connect(ctx)
        return True
    if action=="youtube":
        play_youtube(arg, ctx)
        return True
    if action=="disconnect":
        forget_token()
        ctx.say("Spotify is disconnected. You can also remove Navi from your Spotify account's apps page.")
        poke()
        return True
    if not api_usable(): #no premium (or not set up): the spotify app on this pc, media keys and youtube
        return local_handle(action, arg, t, ctx)
    try:
        run(action, arg, ctx)
    except NoDevice:
        if action in ("pause", "next", "previous"):
            ctx.media_key("play_pause" if action=="pause" else action)
        else:
            ctx.say("Nothing is playing on Spotify right now.")
    except SpotifyError as e:
        if e.reason=="OWNER_PREMIUM" or premium_refusal(e):
            explain_once(ctx, e)
            local_handle(action, arg, t, ctx)
        else:
            say_error(ctx, e)
    except (NotConnected, requests.RequestException) as e:
        say_error(ctx, e)
    poke()
    return True

def control(action, ctx):
    #a press on the orb's music controls: "play_pause", "next" or "previous"
    _ctx_paths(ctx)
    if not api_usable():
        ctx.media_key(action)
        poke()
        return
    try:
        if action=="play_pause":
            state=playback() or {}
            if state.get("is_playing"):
                player("PUT", "/me/player/pause")
            else:
                start_playing(ctx)
        elif action in ("next", "previous"):
            player("POST", "/me/player/"+action)
    except NoDevice:
        ctx.say("Nothing is playing on Spotify right now.")
    except SpotifyError as e:
        if e.reason=="OWNER_PREMIUM" or premium_refusal(e):
            ctx.media_key(action)
        else:
            say_error(ctx, e)
    except (NotConnected, requests.RequestException) as e:
        say_error(ctx, e)
    poke()

def _ctx_paths(ctx):
    global _data_path
    _data_path=ctx.data_path

def run(action, arg, ctx):
    if action=="pause":
        player("PUT", "/me/player/pause")
    elif action=="resume":
        start_playing(ctx)
    elif action=="next":
        player("POST", "/me/player/next")
    elif action=="previous":
        player("POST", "/me/player/previous")
    elif action=="what":
        info=now_playing_info(playback())
        if not info:
            ctx.say("Nothing is playing on Spotify right now.")
        else:
            where=f" on {info['device']}" if info["device"] else ""
            verb="This is" if info["playing"] else "Paused on"
            ctx.say(f"{verb} {info['title']}" + (f" by {info['artist']}" if info["artist"] else "") + where + ".")
    elif action=="like":
        info=now_playing_info(playback())
        if not info or not ID_RE.match(info["id"]) or info["type"]!="track":
            ctx.say("Nothing is playing to save.")
            return
        call("PUT", "/me/tracks", {"ids": info["id"]})
        ctx.say(f"Saved {info['title']} to your liked songs.")
    elif action=="shuffle":
        player("PUT", "/me/player/shuffle", {"state": "true" if arg else "false"})
        ctx.say("Shuffle is on." if arg else "Shuffle is off.")
    elif action=="shuffle_play":
        kind, name, artist=parse_request(arg)
        body, said=search(kind, name, artist)
        if not body:
            ctx.say(f"I couldn't find {arg} on Spotify.")
            return
        if start_playing(ctx, body):
            try:
                player("PUT", "/me/player/shuffle", {"state": "true"})
            except (SpotifyError, NoDevice):
                pass
            ctx.say(f"Shuffling {said}.")
    elif action=="repeat":
        player("PUT", "/me/player/repeat", {"state": arg})
        ctx.say({"off": "Repeat is off.", "track": "Repeating this song.", "context": "Repeating this playlist."}[arg])
    elif action=="volume":
        player("PUT", "/me/player/volume", {"volume_percent": max(0, min(100, arg))})
        ctx.say(f"Spotify volume {max(0, min(100, arg))} percent.")
    elif action=="volume_step":
        state=playback() or {}
        now=(state.get("device") or {}).get("volume_percent")
        if now is None:
            raise NoDevice()
        player("PUT", "/me/player/volume", {"volume_percent": max(0, min(100, int(now)+arg))})
    elif action=="seek_to":
        player("PUT", "/me/player/seek", {"position_ms": 0})
    elif action=="seek_by":
        state=playback() or {}
        if not state.get("item"):
            raise NoDevice()
        player("PUT", "/me/player/seek", {"position_ms": max(0, int(state.get("progress_ms") or 0)+arg*1000)})
    elif action=="devices":
        found=devices()
        if not found:
            ctx.say("None of your Spotify devices are on right now.")
        else:
            names=[clean(d.get("name"), 60)+(" (playing)" if d.get("is_active") else "") for d in found]
            ctx.say("Your Spotify devices are: "+", ".join(names)+".")
    elif action=="transfer":
        found=devices()
        words=DEVICE_WORDS
        target=re.sub(r"^(?:my|the) ", "", arg)
        hit=next((d for d in found if words.get(target) and d.get("type")==words[target]), None)
        if hit is None and found:
            best=max(found, key=lambda d: similar(d.get("name", ""), target))
            hit=best if similar(best.get("name", ""), target)>=0.6 or norm(target) in norm(best.get("name", "")) else None
        if hit is None:
            ctx.say(f"I can't find a Spotify device called {target}." + (" Say Spotify devices to hear them." if found else
                                                                          " Open Spotify on it first."))
            return
        transfer(hit["id"], play=True)
        ctx.say(f"Playing on {clean(hit.get('name'), 60)}.")
    elif action in ("play", "queue"):
        kind, name, artist=parse_request(arg)
        if not name and kind!="liked":
            start_playing(ctx)
            return
        body, said=search(kind, name, artist)
        if not body:
            ctx.say(f"I couldn't find {arg} on Spotify.")
            return
        if action=="queue":
            uri=(body.get("offset") or {}).get("uri") or (body.get("uris") or [None])[0]
            if not uri:
                ctx.say("I can only add songs to the queue, not whole albums or playlists.")
                return
            player("POST", "/me/player/queue", {"uri": uri})
            ctx.say(f"Added {said} to the queue.")
        elif start_playing(ctx, body):
            ctx.say(f"Playing {said}.")
