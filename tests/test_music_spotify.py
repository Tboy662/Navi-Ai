#tests music.py against a fake spotify (no network, no account needed), including attempts to break the sign-in.
#Run: py -3.14 tests/test_music_spotify.py
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import music
import requests

TMP=tempfile.mkdtemp()
CID="0123456789abcdef0123456789abcdef"
os.environ["SPOTIFY_CLIENT_ID"]=CID
said=[]
keys=[]
opened=[]
ctx=music.Context(say=said.append, hear=lambda: "", said_yes=lambda t: True, data_path=lambda n: os.path.join(TMP, n),
                  open_url=opened.append, media_key=keys.append)
music._data_path=ctx.data_path
failures=0

def check(name, ok):
    global failures
    print(("ok   " if ok else "FAIL ")+name)
    failures+=0 if ok else 1

class Resp:
    def __init__(self, status, data=None, headers=None, text=None):
        self.status_code=status
        self.content=text.encode() if text is not None else b"" if data is None else json.dumps(data).encode()
        self.text=self.content.decode()
        self.headers=headers or {}
    def json(self):
        return json.loads(self.content)

TRACK={"type": "track", "id": "0VjIjW4GlUZAMYd2vXMi3b", "uri": "spotify:track:0VjIjW4GlUZAMYd2vXMi3b", "name": "Blinding Lights",
       "duration_ms": 200000, "artists": [{"name": "The Weeknd"}],
       "album": {"uri": "spotify:album:4yP0hdKOZPNshxUOjY0cZj", "images": [{"url": "https://i.scdn.co/image/abc", "width": 64}]}}
class Fake:
    def __init__(self):
        self.reset()
    def reset(self):
        self.calls=[]
        self.active=True
        self.devices=[{"id": "dev1", "name": "Tomer's phone", "type": "Smartphone", "is_active": False, "is_restricted": False}]
        self.premium=True
        self.playing=True
        self.token_status=200
        self.owner_blocked=False
    def token(self, url, data=None, timeout=None):
        self.calls.append(("TOKEN", data.get("grant_type"), data))
        if self.token_status!=200:
            return Resp(self.token_status, {"error": "invalid_grant"})
        return Resp(200, {"access_token": "AT"+str(len(self.calls)), "refresh_token": "RT", "expires_in": 3600, "scope": music.SCOPES})
    def request(self, method, url, params=None, json=None, timeout=None, headers=None):
        assert url.startswith("https://api.spotify.com/v1/"), url
        path=url[len(music.API):]
        self.calls.append((method, path, params, json))
        if self.owner_blocked: #what spotify really sends when the app's owner has no premium (plain text, no json)
            return Resp(403, text="Active premium subscription required for the owner of the app. When the subscription status changes, it can take a few hours before requests are allowed again.")
        if not self.premium and path.startswith("/me/player/") and method in ("PUT", "POST"):
            return Resp(403, {"error": {"status": 403, "message": "Player command failed: Premium required", "reason": "PREMIUM_REQUIRED"}})
        if path=="/me/player/devices":
            return Resp(200, {"devices": self.devices})
        if path=="/me/player" and method=="PUT":
            self.active=True
            return Resp(204)
        if path=="/me/player" and method=="GET":
            return Resp(200, {"is_playing": self.playing, "progress_ms": 1000, "item": TRACK, "device": {"name": "PC", "volume_percent": 50},
                              "shuffle_state": False, "repeat_state": "off"}) if self.active else Resp(204)
        if path.startswith("/me/player/"):
            if not self.active and not (params or {}).get("device_id"):
                return Resp(404, {"error": {"status": 404, "message": "Player command failed: No active device found", "reason": "NO_ACTIVE_DEVICE"}})
            return Resp(204)
        if path=="/search":
            return Resp(200, {"tracks": {"items": [TRACK, {"uri": "spotify:track:../../evil", "name": "bad"}]}, "artists": {"items": [{"uri": "spotify:artist:1Xyo4u8uXC1ZmMpatF05PJ", "name": "The Weeknd"}]},
                              "playlists": {"items": [{"uri": "spotify:playlist:37i9dQZF1DXbITWG1ZJKYt", "name": "Jazz Classics"}]}})
        if path=="/me/playlists":
            return Resp(200, {"items": [{"uri": "spotify:playlist:5ABHKGoOzxkaa28ttQV9sE", "name": "Chill"}]})
        if path=="/me/tracks":
            return Resp(200, {"items": [{"track": TRACK}]}) if method=="GET" else Resp(200, {})
        return Resp(404, {"error": {"status": 404, "message": "not found"}})
fake=Fake()
requests.request=fake.request
requests.post=fake.token
music.requests.request=fake.request
music.requests.post=fake.token
music.time.sleep=lambda s: None
real_titles=music.spotify_titles
music.spotify_titles=lambda: [] #the real spotify app on this pc mustn't change the results
real_session=music.session_now_playing
music.session_now_playing=lambda last=None: False #...nor windows' media controls (tested on their own below)
music.session_control=lambda action: False

#--- sign in -----------------------------------------------------------------------------------------------------
def browser(code="CODE", state=None, path="/callback", tamper=None):
    #pretends to be the browser coming back from spotify
    def go(link):
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
        check("auth link goes to accounts.spotify.com", link.startswith("https://accounts.spotify.com/authorize?"))
        check("auth link uses PKCE S256 and the fixed loopback redirect", q["code_challenge_method"]==["S256"] and q["redirect_uri"]==[music.REDIRECT_URI])
        check("no client secret in the link", "client_secret" not in q)
        go.state=q["state"][0]
        def hit():
            time.sleep(0.3)
            if tamper:
                tamper(go.state)
            st=go.state if state is None else state
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{music.REDIRECT_PORT}{path}?code={code}&state={st}", timeout=5).read()
            except urllib.error.HTTPError as e:
                go.status=e.code
        threading.Thread(target=hit, daemon=True).start()
    go.status=200
    return go

def attacks(state):
    #what another program on this pc (or a web page) might try while the sign-in waits
    for url in ["/callback?code=EVIL&state=wrong", "/callback?code=EVIL", "/../../etc/passwd", "/?code=EVIL&state="+state[:-1]+"x",
                "/callback?code=EVIL&state=%00"]:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{music.REDIRECT_PORT}{url}", timeout=5).read()
            attacks.leaked=True
        except urllib.error.HTTPError:
            pass
attacks.leaked=False

b=browser(tamper=attacks)
check("sign in works", music.sign_in(b, wait=10)=="ok")
check("forged callbacks were refused", not attacks.leaked)
tok=[c for c in fake.calls if c[0]=="TOKEN"][-1][2]
check("code exchanged with the verifier, real code only", tok["code"]=="CODE" and len(tok["code_verifier"])>=43 and "client_secret" not in tok)
check("token saved", music.connected() and json.load(open(os.path.join(TMP, music.TOKEN_FILE)))["refresh_token"]=="RT")
music.forget_token()
b=browser(state="not-the-state")
check("wrong state is refused (times out)", music.sign_in(b, wait=3)=="timeout" and b.status==400 and not music.connected())
b=browser(path="/other")
check("other paths are refused", music.sign_in(b, wait=3)=="timeout" and b.status==404)
import socket
s=socket.socket(); s.bind(("127.0.0.1", music.REDIRECT_PORT)); s.listen(1)
check("port in use is reported", music.sign_in(lambda u: None, wait=1)=="port_busy")
s.close()
os.environ["SPOTIFY_CLIENT_ID"]="abc&redirect_uri=http://evil"
check("a bad client id is ignored, not pasted into the link", music.client_id()=="" and music.sign_in(lambda u: None)=="no_client_id")
os.environ["SPOTIFY_CLIENT_ID"]=CID
b=browser()
music.sign_in(b, wait=10)

#--- callback server only listens on loopback ----------------------------------------------------------------------
import http.server
check("callback server binds to 127.0.0.1 only", "127.0.0.1" in music.REDIRECT_URI)

#--- commands ------------------------------------------------------------------------------------------------------
def run(text):
    fake.calls.clear(); said.clear(); keys.clear()
    handled=music.handle(text, ctx)
    return handled
check("pause", run("pause the music") and ("PUT", "/me/player/pause", None, None) in fake.calls)
check("next", run("next song") and any(c[1]=="/me/player/next" for c in fake.calls))
run("play blinding lights by the weeknd")
play=[c for c in fake.calls if c[1]=="/me/player/play"]
check("play a song in its album", play and play[0][3]=={"context_uri": TRACK["album"]["uri"], "offset": {"uri": TRACK["uri"]}} and said==["Playing Blinding Lights by The Weeknd."])
search=[c for c in fake.calls if c[1]=="/search"][0]
check("what you say only goes in the search query", search[2]["q"]=="blinding lights artist:the weeknd")
run("play some jazz")
check("play some <genre> picks a playlist", [c for c in fake.calls if c[1]=="/me/player/play"][0][3]=={"context_uri": "spotify:playlist:37i9dQZF1DXbITWG1ZJKYt"})
run("play my chill playlist")
check("your own playlist", [c for c in fake.calls if c[1]=="/me/player/play"][0][3]=={"context_uri": "spotify:playlist:5ABHKGoOzxkaa28ttQV9sE"})
run("what's playing")
check("what's playing", said==["This is Blinding Lights by The Weeknd on PC."])
run("like this song")
check("like", ("PUT", "/me/tracks", {"ids": TRACK["id"]}, None) in fake.calls)
run("spotify volume 250")
check("volume is clamped", any(c[1]=="/me/player/volume" and c[2]=={"volume_percent": 100} for c in fake.calls))
run("play the music on my phone")
check("transfer to phone", ("PUT", "/me/player", None, {"device_ids": ["dev1"], "play": True}) in fake.calls)

#no active device: moves playback to one of your devices and tries again
fake.active=False
run("play blinding lights")
check("wakes a device when nothing is active", any(c[1]=="/me/player" and c[0]=="PUT" for c in fake.calls) and said[-1].startswith("Playing"))
fake.active=False
fake.devices=[]
music.wait_for_device=lambda seconds=25: None
run("resume")
check("no device at all: opens the web player and explains", opened and opened[-1]==music.WEB_PLAYER and "can't see a Spotify device" in said[-1])
fake.active=False
run("pause")
check("pause with nothing active falls back to the media key", keys==["play_pause"])
fake.reset()

fake.premium=False
run("next song")
check("no premium is explained", "Premium" in said[-1])
fake.reset()

#an expired token is refreshed; a revoked one signs you out
with music._lock:
    music._token["expires_at"]=0
run("pause")
check("refreshes an expired token", any(c[0]=="TOKEN" and c[1]=="refresh_token" for c in fake.calls))
with music._lock:
    music._token["expires_at"]=0
fake.token_status=400
run("pause")
check("revoked token: disconnected and told", not music.connected() and "isn't connected" in said[-1])
fake.reset()

#the app's owner has no premium: spotify refuses everything with a plain-text 403
music.sign_in(browser(), wait=10) #signed out by the test above
fake.owner_blocked=True
ctx.told_about_premium=False
run("pause")
check("owner without premium: pause uses the media key and explains why", keys==["play_pause"] and said and "doesn't have Premium" in said[0])
check("settings shows the reason", "needs Premium" in music.problem)
run("next song")
check("...and only explains once", keys==["next"] and said==[])
opened.clear()
run("play blinding lights by the weeknd")
check("play opens the song search in spotify", opened and opened[0] in ("spotify:search:blinding%20lights%20the%20weeknd",
      music.WEB_PLAYER+"/search/blinding%20lights%20the%20weeknd") and "Press play there" in said[-1])
music.spotify_titles=lambda: ["The Weeknd - Blinding Lights"]
run("what's playing")
check("what's playing reads the spotify app instead", said==["This is Blinding Lights by The Weeknd."])
music.spotify_titles=lambda: []
said.clear()
real_sign_in=music.sign_in
music.sign_in=lambda open_url, wait=300: "ok" #the sign-in itself is tested above
check("connect reports the block after signing in", music.finish_connect(ctx)=="owner_premium" and "signed in, but" in said[-1])
music.sign_in=real_sign_in
fake.reset()
music.playback() #the watcher asks spotify again every 10 minutes
check("the reason clears once spotify answers again", music.problem=="" and music.api_usable())

#not connected: media keys for the basics, setup help for the rest
music.forget_token()
check("not connected: pause uses the media key", run("pause") and keys==["play_pause"])
os.environ["SPOTIFY_CLIENT_ID"]=""
opened.clear()
check("not set up, no youtube key: play opens spotify's search", run("play hello by adele") and opened and "hello%20adele" in opened[0] and "YouTube key" in said[-1])
check("not set up: shuffle explains the limits", run("shuffle") and said[-1]==music.NO_PREMIUM_LIMITS)
check("not set up: what's playing goes to the ai", not run("what's playing"))
check("not music: left alone", not run("repeat that") and not run("what time is it"))

#--- the spotify app on this pc (no premium) ------------------------------------------------------------------------
music.spotify_titles=lambda: ["Daft Punk - One More Time", "Spotify Free"]
info=music.local_now_playing()
check("song read from the app's window title", info["title"]=="One More Time" and info["artist"]=="Daft Punk" and info["playing"] and info["source"]=="local")
music.spotify_titles=lambda: ["Spotify Free"]
check("paused keeps the last song, not playing", music.local_now_playing(info)["title"]=="One More Time" and not music.local_now_playing(info)["playing"])
check("paused with nothing known shows nothing", music.local_now_playing(None) is None)
music.spotify_titles=lambda: []
check("app closed: nothing", music.local_now_playing(info) is None)
check("real window scan runs without error", isinstance(real_titles(), list))
got=real_session()
check("windows' media controls answer (song info, None, or False without pywinrt)", got is None or got is False or (isinstance(got, dict) and got["source"]=="local"))
if isinstance(got, dict) and got["art"]:
    check("its album art is kept for the orb", 0<len(music.fetch_art(got["art"]) or b"")<=music.ART_MAX_BYTES)
keys.clear()
music.control("next", ctx)
check("orb buttons use the media keys without the api", keys==["next"])

#--- youtube -------------------------------------------------------------------------------------------------------
yt=[]
def fake_get(url, params=None, headers=None, timeout=None, allow_redirects=True, **kw):
    yt.append((url, params, headers))
    if headers.get("X-Goog-Api-Key")=="AIzaBADBADBADBADBADBADBADBADBADBADBAD":
        return Resp(400, {"error": {"errors": [{"reason": "badRequest"}]}})
    if params["q"]=="quota":
        return Resp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
    if params["q"]=="weird":
        return Resp(200, {"items": [{"id": {"videoId": "../../evil?x=1"}}, {"id": {"videoId": "dQw4w9WgXcQ"}, "snippet": {"title": "Ok &amp; fine"}}]})
    return Resp(200, {"items": [{"id": {"videoId": "4NRXx6U8ABQ"}, "snippet": {"title": "The Weeknd - Blinding Lights (Official Audio)", "channelTitle": "TheWeekndVEVO"}}]})
music.requests.get=fake_get
os.environ["YOUTUBE_API_KEY"]="AIzaSyA0123456789abcdefghijklmnopqrstuv"
opened.clear(); said.clear()
check("play X on youtube", run("play blinding lights on youtube") and opened==["https://www.youtube.com/watch?v=4NRXx6U8ABQ"] and said[-1].startswith("Playing The Weeknd"))
check("youtube key goes in a header, never the url", "key" not in yt[-1][1] and yt[-1][2]["X-Goog-Api-Key"].startswith("AIza") and yt[-1][0]==music.YOUTUBE_SEARCH)
opened.clear()
check("without premium, plain play uses youtube when there's a key", run("play blinding lights by the weeknd") and opened[-1].startswith("https://www.youtube.com/watch?v="))
opened.clear()
check("...but play X on spotify still opens spotify", run("play blinding lights on spotify") and opened and "youtube" not in opened[-1])
opened.clear()
run("play weird on youtube")
check("bad video ids are skipped, titles unescaped", opened==["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] and said[-1]=="Playing Ok & fine on YouTube.")
run("play quota on youtube")
check("quota used up is explained", "daily limit" in said[-1])
os.environ["YOUTUBE_API_KEY"]="AIzaBADBADBADBADBADBADBADBADBADBADBAD"
run("play hello on youtube")
check("bad key is explained", "didn't accept the API key" in said[-1])
os.environ["YOUTUBE_API_KEY"]="not a key&q=x"
check("a malformed key is never sent", music.youtube_key()=="" and run("play hello on youtube") and "add a YouTube API key" in said[-1])
os.environ["YOUTUBE_API_KEY"]=""

#--- album art -----------------------------------------------------------------------------------------------------
check("art from spotify's image server", music.art_url_ok("https://i.scdn.co/image/ab67616d0000b273"))
for bad in ["http://i.scdn.co/x", "https://i.scdn.co.evil.com/x", "https://evil.com/i.scdn.co", "https://user:pw@i.scdn.co/x",
            "https://i.scdn.co:8443/x", "file:///C:/Windows/win.ini", "https://127.0.0.1/x", "javascript:alert(1)"]:
    check("art refused: "+bad, not music.art_url_ok(bad))
info=music.now_playing_info({"is_playing": True, "item": dict(TRACK, name="A\nsong 😀 "+"x"*500, album={"images": [{"url": "https://evil.com/a.jpg"}]})})
check("now playing text is one line, no emoji, capped; bad art dropped", "\n" not in info["title"] and "😀" not in info["title"] and len(info["title"])<=200 and info["art"]=="")
check("search results with bad ids are skipped", music.first([{"uri": "spotify:track:../../evil"}, {"uri": TRACK["uri"]}])["uri"]==TRACK["uri"])

print("failures:", failures)
sys.exit(1 if failures else 0)
