#smart home for navi: lights, plugs, fans and scenes through Home Assistant and/or a Philips Hue bridge.
#
#  "turn on the kitchen light", "lights off", "turn off all the lights", "dim the bedroom to 30 percent",
#  "make the living room blue", "warm white", "turn on the fan plug", "activate the movie scene",
#  "is the porch light on", "what lights are on", "what smart devices do I have", "turn off the lights in 10 minutes"
#
#voice.py calls handle(text, ctx) before its other commands; it returns True when it took the request.
#Safety:
#  - only lights, switches/plugs, fans and scenes are ever touched. Locks, doors, garage doors, alarms, cameras and
#    thermostats are left alone even if Home Assistant has them, and devices you hide in Settings are skipped too
#  - what you say is only matched against the device names the hub reports: it never becomes an address, an entity
#    id or a service name, and the only services called are a fixed list (turn on, turn off)
#  - the Home Assistant token is only sent to the address in .env, and plain http is refused unless that address is
#    on your home network, so the token can't cross the internet unencrypted. The Hue bridge must be a home network
#    address too (Hue's key travels in the address, so it must never leave your network)
#  - every request has a short timeout, and an unreachable hub is remembered for a minute so other commands stay fast
import colorsys
import ipaddress
import os
import re
import threading
import time
import urllib.parse

try:
    import requests
except ImportError: #tests can run without it
    requests=None

TIMEOUT=5
CACHE_SECONDS=30
DOWN_SECONDS=60
MAX_DELAY=12*3600
HA_DOMAINS={"light": "light", "switch": "plug", "fan": "fan", "scene": "scene"} #home assistant domain -> kind

class Context:
    #what the commands need from voice.py, so this file can be tested on its own
    def __init__(self, say, hidden=lambda: [], post=lambda kind, value: None, passthrough=(), env=None):
        self.say=say
        self.hidden=hidden #device ids hidden in settings
        self.post=post
        self.passthrough=passthrough
        self.env=env if env is not None else os.environ

class HubError(Exception):
    pass

class Device:
    def __init__(self, hub, id, name, kind, on=None, brightness=None, color=False, available=True):
        self.hub, self.id, self.name, self.kind=hub, id, name, kind
        self.on, self.brightness, self.color, self.available=on, brightness, color, available
    @property
    def key(self):
        return f"{self.hub.short}:{self.id}"
    def __repr__(self):
        return f"Device({self.key}, {self.name!r}, {self.kind})"

#addresses
def private_host(host):
    #true for an address on your own network: 192.168.x.x and the like, localhost, name.local or a bare name
    host=(host or "").strip("[]").lower()
    if not host:
        return False
    try:
        ip=ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        pass
    return bool(re.fullmatch(r"[a-z0-9-]+(\.local|\.lan|\.home|\.internal|\.home\.arpa)?", host))

def ha_base(url):
    #the home assistant address as scheme://host[:port], or raises HubError with what's wrong
    url=(url or "").strip().rstrip("/")
    if not url:
        raise HubError("no address")
    if "://" not in url:
        url="http://"+url
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise HubError("That isn't a Home Assistant address, like http://homeassistant.local:8123.")
    if p.path not in ("", "/"):
        raise HubError("Use just the address of Home Assistant, like http://homeassistant.local:8123, without a page.")
    try:
        port=p.port
    except ValueError:
        raise HubError("That address has a bad port number.")
    if p.scheme=="http" and not private_host(p.hostname):
        raise HubError("That address isn't on your home network, so it needs https (your token would be sent unencrypted).")
    host=f"[{p.hostname}]" if ":" in p.hostname else p.hostname
    return f"{p.scheme}://{host}" + (f":{port}" if port else "")

def hue_host(host):
    host=(host or "").strip().lower()
    host=re.sub(r"^https?://", "", host).rstrip("/")
    m=re.fullmatch(r"([a-z0-9.-]+)(?::(\d{1,5}))?", host)
    if not m or not private_host(m.group(1)):
        raise HubError("The Hue bridge address has to be on your home network, like 192.168.1.20.")
    return host

#colors: name -> rgb, and white names -> kelvin
COLORS={"red": (255, 0, 0), "orange": (255, 120, 0), "yellow": (255, 220, 0), "green": (0, 255, 0), "lime": (160, 255, 0),
        "teal": (0, 160, 140), "cyan": (0, 255, 255), "turquoise": (64, 224, 208), "blue": (0, 0, 255),
        "light blue": (120, 190, 255), "sky blue": (120, 200, 255), "navy": (0, 0, 160), "indigo": (75, 0, 200),
        "purple": (140, 0, 255), "violet": (180, 80, 255), "lavender": (190, 160, 255), "magenta": (255, 0, 255),
        "pink": (255, 90, 180), "hot pink": (255, 40, 140), "gold": (255, 190, 0), "amber": (255, 160, 0)}
WHITES={"candle": 2000, "candlelight": 2000, "warm white": 2700, "warm": 2700, "soft white": 2700, "white": 4000,
        "neutral white": 4000, "neutral": 4000, "cool white": 5000, "cool": 5000, "cold white": 5500, "daylight": 6500,
        "bright white": 6500}

def rgb_to_xy(rgb):
    #philips' conversion from rgb to the xy colors hue bulbs take
    def lin(c):
        c=c/255
        return ((c+0.055)/1.055)**2.4 if c>0.04045 else c/12.92
    r, g, b=(lin(c) for c in rgb)
    x=r*0.664511+g*0.154324+b*0.162028
    y=r*0.283881+g*0.668433+b*0.047685
    z=r*0.000088+g*0.072310+b*0.986039
    total=x+y+z
    if total==0:
        return [0.3227, 0.329]
    return [round(x/total, 4), round(y/total, 4)]

#the hubs
class HomeAssistant:
    label="Home Assistant"
    short="ha"
    def __init__(self, url, token, http=None):
        self.base=ha_base(url)
        self.token=token
        self.http=http or requests

    def request(self, method, path, body=None):
        try:
            r=self.http.request(method, self.base+path, json=body, timeout=TIMEOUT, allow_redirects=False,
                                headers={"Authorization": "Bearer "+self.token, "Content-Type": "application/json"})
        except Exception as e:
            raise HubError(f"I couldn't reach Home Assistant ({type(e).__name__}).")
        if r.status_code==401:
            raise HubError("Home Assistant didn't accept the token. Make a new long-lived token in your profile.")
        if r.status_code>=400 or 300<=r.status_code<400:
            raise HubError(f"Home Assistant said no (error {r.status_code}).")
        try:
            return r.json()
        except ValueError:
            return None

    def devices(self):
        out=[]
        for s in self.request("GET", "/api/states") or []:
            if not isinstance(s, dict):
                continue
            entity=str(s.get("entity_id", ""))
            domain=entity.split(".", 1)[0]
            if domain not in HA_DOMAINS:
                continue
            attrs=s.get("attributes") if isinstance(s.get("attributes"), dict) else {}
            name=str(attrs.get("friendly_name") or entity.split(".", 1)[-1].replace("_", " "))
            state=s.get("state")
            bri=attrs.get("brightness")
            modes=attrs.get("supported_color_modes") or []
            out.append(Device(self, entity, name, HA_DOMAINS[domain], on=(state=="on") if state in ("on", "off") else None,
                              brightness=round(bri/255*100) if isinstance(bri, (int, float)) and state=="on" else None,
                              color=any(m in ("hs", "xy", "rgb", "rgbw", "rgbww") for m in modes if isinstance(m, str)),
                              available=state not in ("unavailable", "unknown")))
        return out

    def apply(self, device, change):
        domain=device.id.split(".", 1)[0]
        if domain not in HA_DOMAINS: #never anything but the allowed kinds, whatever the list says
            raise HubError("I can't control that.")
        data={"entity_id": device.id}
        if domain=="scene":
            service="turn_on"
        elif change.get("on") is False:
            service="turn_off"
        else:
            service="turn_on"
            if domain=="light":
                if "brightness" in change:
                    data["brightness_pct"]=change["brightness"]
                if "step" in change:
                    data["brightness_step_pct"]=change["step"]
                if "rgb" in change:
                    data["rgb_color"]=list(change["rgb"])
                if "kelvin" in change:
                    data["color_temp_kelvin"]=change["kelvin"]
        self.request("POST", f"/api/services/{domain}/{service}", data)

    def check(self):
        msg=self.request("GET", "/api/")
        return "Connected to Home Assistant." if isinstance(msg, dict) else "Home Assistant answered, but oddly."

class Hue:
    label="Hue"
    short="hue"
    def __init__(self, bridge, key, http=None):
        self.host=hue_host(bridge)
        if not re.fullmatch(r"[A-Za-z0-9-]{10,80}", key or ""):
            raise HubError("The Hue key looks wrong. Pair the bridge again in Settings, Smart home.")
        self.key=key
        self.http=http or requests

    def request(self, method, path, body=None):
        try:
            r=self.http.request(method, f"http://{self.host}/api/{self.key}{path}", json=body, timeout=TIMEOUT,
                                allow_redirects=False)
            data=r.json()
        except Exception as e:
            raise HubError(f"I couldn't reach the Hue bridge ({type(e).__name__}).")
        if isinstance(data, list) and data and isinstance(data[0], dict) and "error" in data[0]:
            err=data[0]["error"]
            if isinstance(err, dict) and err.get("type")==1:
                raise HubError("The Hue bridge doesn't know me anymore. Pair it again in Settings, Smart home.")
            raise HubError("The Hue bridge said no.")
        return data

    def devices(self):
        out=[]
        lights=self.request("GET", "/lights") or {}
        for id, l in (lights.items() if isinstance(lights, dict) else []):
            st=l.get("state", {}) if isinstance(l, dict) else {}
            bri=st.get("bri")
            out.append(Device(self, f"light/{id}", str(l.get("name", f"light {id}")), "light", on=bool(st.get("on")),
                              brightness=round(bri/254*100) if isinstance(bri, (int, float)) and st.get("on") else None,
                              color="xy" in st, available=st.get("reachable", True) is not False))
        groups=self.request("GET", "/groups") or {}
        for id, g in (groups.items() if isinstance(groups, dict) else []):
            if not isinstance(g, dict) or g.get("type") not in ("Room", "Zone", "LightGroup"):
                continue
            st=g.get("state", {}) if isinstance(g.get("state"), dict) else {}
            act=g.get("action", {}) if isinstance(g.get("action"), dict) else {}
            bri=act.get("bri")
            out.append(Device(self, f"group/{id}", str(g.get("name", f"room {id}")), "room", on=bool(st.get("any_on")),
                              brightness=round(bri/254*100) if isinstance(bri, (int, float)) and st.get("any_on") else None,
                              color="xy" in act))
        scenes=self.request("GET", "/scenes") or {}
        seen=set()
        for id, s in (scenes.items() if isinstance(scenes, dict) else []):
            if not isinstance(s, dict) or s.get("type")!="GroupScene" or not s.get("group"):
                continue
            room=groups.get(s["group"], {}).get("name", "") if isinstance(groups, dict) else ""
            name=f"{room} {s.get('name', '')}".strip() if room else str(s.get("name", ""))
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append(Device(self, f"scene/{s['group']}/{id}", name, "scene"))
        return out

    def apply(self, device, change):
        kind, _, rest=device.id.partition("/")
        if device.kind=="scene":
            group, _, scene=rest.partition("/")
            self.request("PUT", f"/groups/{group}/action", {"scene": scene})
            return
        body={}
        if change.get("on") is False:
            body["on"]=False
        else:
            body["on"]=True
            if "brightness" in change:
                body["bri"]=max(1, min(254, round(change["brightness"]/100*254)))
            if "step" in change:
                body["bri_inc"]=max(-254, min(254, round(change["step"]/100*254)))
            if "rgb" in change:
                body["xy"]=rgb_to_xy(change["rgb"])
            if "kelvin" in change:
                body["ct"]=max(153, min(500, round(1000000/change["kelvin"])))
        if kind=="light":
            self.request("PUT", f"/lights/{rest}/state", body)
        elif kind=="group":
            self.request("PUT", f"/groups/{rest}/action", body)
        else:
            raise HubError("I can't control that.")

    def check(self):
        cfg=self.request("GET", "/config")
        return f"Connected to the Hue bridge {cfg.get('name', '')}".strip()+"." if isinstance(cfg, dict) else "Connected."

def hubs(env):
    #the hubs set up in .env. A hub with a bad address is left out and its problem reported
    out, problems=[], []
    if env.get("HA_URL") and env.get("HA_TOKEN"):
        try:
            out.append(HomeAssistant(env["HA_URL"], env["HA_TOKEN"]))
        except HubError as e:
            problems.append(str(e))
    if env.get("HUE_BRIDGE") and env.get("HUE_KEY"):
        try:
            out.append(Hue(env["HUE_BRIDGE"], env["HUE_KEY"]))
        except HubError as e:
            problems.append(str(e))
    return out, problems

#hue pairing (used by settings)
def hue_discover(http=None):
    #bridge addresses on this network, from philips' discovery service. Only home network addresses are kept
    http=http or requests
    r=http.get("https://discovery.meethue.com/", timeout=TIMEOUT)
    found=[]
    for b in r.json() if r.status_code==200 else []:
        ip=str(b.get("internalipaddress", "")) if isinstance(b, dict) else ""
        try:
            if ipaddress.ip_address(ip).is_private:
                found.append(ip)
        except ValueError:
            pass
    return found

def hue_pair(bridge, http=None):
    #one try at pairing: the key, or None while the bridge's button hasn't been pressed
    http=http or requests
    host=hue_host(bridge)
    r=http.post(f"http://{host}/api", json={"devicetype": "navi#pc"}, timeout=TIMEOUT, allow_redirects=False)
    data=r.json()
    item=data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    if "success" in item and re.fullmatch(r"[A-Za-z0-9-]{10,80}", str(item["success"].get("username", ""))):
        return item["success"]["username"]
    if item.get("error", {}).get("type")==101:
        return None
    raise HubError("The bridge didn't pair: "+str(item.get("error", {}).get("description", "unknown answer"))[:100])

#the device list, cached so a run of commands doesn't ask the hub every time
_cache={"at": 0, "devices": [], "key": None, "down": {}}
_lock=threading.Lock()

def all_devices(ctx, fresh=False):
    found, problems=hubs(ctx.env)
    key=tuple((h.short, getattr(h, "base", getattr(h, "host", ""))) for h in found)
    hidden=set(ctx.hidden() or [])
    with _lock:
        if not fresh and _cache["key"]==key and time.time()-_cache["at"]<CACHE_SECONDS:
            return [d for d in _cache["devices"] if d.key not in hidden], problems
    devices=[]
    for hub in found:
        down=_cache["down"].get(hub.short)
        if down and time.time()-down[0]<DOWN_SECONDS and not fresh:
            problems.append(down[1])
            continue
        try:
            devices+=hub.devices()
            _cache["down"].pop(hub.short, None)
        except HubError as e:
            _cache["down"][hub.short]=(time.time(), str(e))
            problems.append(str(e))
    with _lock:
        _cache.update(at=time.time(), devices=devices, key=key)
    return [d for d in devices if d.key not in hidden], problems

def forget_cache():
    with _lock:
        _cache["at"]=0
        _cache["down"]={}

#understanding what was said
LEAD=r"^(?:(?:hey\s+)?navi,?\s+)?(?:(?:can|could|would|will)\s+you\s+|please\s+|i\s+want\s+you\s+to\s+)*"
DEVICE_WORDS=r"lights?|lamps?|bulbs?|plugs?|outlets?|sockets?|fans?|switch(?:es)?|leds?|led strip|scenes?|chandelier|light strip"
DEVICE_RE=re.compile(r"\b(?:"+DEVICE_WORDS+r")\b")
NOT_MINE=re.compile(r"\b(screen|monitor|display|volume|music|song|computer|pc|laptop|wi-?fi|bluetooth|timer|alarm|"
                    r"reminder|stopwatch|tv|television|subtitles?|captions?|dark mode|light mode|night light|"
                    r"homework alerts?|phone alerts?|notifications?|brief(ing)?|shuffle)\b")
STOP=set("the my a an all of in on off to please and every each some our your at up down for".split())
GENERIC=set("light lights lamp lamps bulb bulbs led leds fixture".split())
KIND_WORDS={"light": "light", "lights": "light", "lamp": "light", "lamps": "light", "bulb": "light", "bulbs": "light",
            "led": "light", "leds": "light", "plug": "plug", "plugs": "plug", "outlet": "plug", "outlets": "plug",
            "socket": "plug", "sockets": "plug", "switch": "plug", "switches": "plug", "fan": "fan", "fans": "fan",
            "scene": "scene", "scenes": "scene"}
NUMBER_WORDS={"ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
              "eighty": 80, "ninety": 90, "hundred": 100, "a hundred": 100, "one hundred": 100, "half": 50, "full": 100,
              "max": 100, "maximum": 100, "minimum": 1, "lowest": 1, "min": 1}

def words_of(s):
    return [w[:-1] if len(w)>3 and w.endswith("s") and not w.endswith("ss") else w for w in re.findall(r"[a-z0-9']+", s.lower())]

def delay_of(t):
    m=re.search(r"\b(?:in|after)\s+(\d{1,4}|an?|one|two|three|five|ten|fifteen|twenty|thirty|half an?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", t)
    if not m:
        return None, t
    n={"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
       "half a": 0.5, "half an": 0.5}.get(m.group(1))
    n=float(m.group(1)) if n is None else n
    unit=m.group(2)[0]
    seconds=n*(3600 if unit=="h" else 60 if unit=="m" else 1)
    return seconds, (t[:m.start()]+t[m.end():]).strip()

def brightness_of(t):
    m=re.search(r"\b(\d{1,3})\s*(?:%|percent\b)", t) or re.search(r"\b(?:to|at)\s+(\d{1,3})\b(?!\s*(?:seconds?|minutes?|hours?|am|pm))", t)
    if m:
        return int(m.group(1))
    m=re.search(r"\b(?:to|at)\s+(?:(half)|(full)|(max(?:imum)?)|(min(?:imum)?)|(lowest)|(a hundred|one hundred|hundred|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety))(?:\s+(?:percent|brightness|power))?\b", t) \
        or re.search(r"\b(full|max(?:imum)?)\s+brightness\b|\b(half)\s+brightness\b", t)
    if m:
        word=next(g for g in m.groups() if g)
        return NUMBER_WORDS.get(word, 100)
    return None

def color_of(s):
    #(rgb, kelvin, spoken name) for the first color in s
    for name in sorted(WHITES, key=len, reverse=True):
        if re.search(r"\b"+name+r"\b", s):
            return None, WHITES[name], name
    for name in sorted(COLORS, key=len, reverse=True):
        if re.search(r"\b"+name+r"\b", s):
            return COLORS[name], None, name
    return None, None, None

def setting_start(body):
    #where the color or brightness part of "the bedroom lights to 40 percent" starts, or None
    spots=[]
    for name in list(WHITES)+list(COLORS):
        m=re.search(r"(?:\b(?:to|at)\s+)?\b"+name+r"\b", body)
        if m:
            spots.append(m.start())
    for pattern in (r"\b(?:to|at)\s+\d", r"\b\d{1,3}\s*(?:%|percent\b)", r"\bbrightness\b",
                    r"\b(?:to|at)\s+(?:half|full|max|maximum|min|minimum|lowest|a hundred|one hundred|hundred|ten|twenty|"
                    r"thirty|forty|fifty|sixty|seventy|eighty|ninety)\b", r"\b(?:full|half|max(?:imum)?)\s+brightness\b"):
        m=re.search(pattern, body)
        if m:
            spots.append(m.start())
    return min(spots) if spots else None

NOT_A_DEVICE_COMMAND=re.compile(r"^(?:remind|add|take|note|jot|search|google|look up|translate|convert|calculate|how|why|"
                                r"when|where|who|tell me (?:a|about)|read|draft|write|send|reply|e-?mail|open|close|launch|"
                                r"pause|resume|skip|next|previous|find|spell|roll|flip|pick|cancel|delete|clear|forget|remember)\b")

def parse(text):
    #what the person asked for, as a dict, or None when it isn't a smart home command
    t=text.lower().strip()
    t=re.sub(r"[?!.,]+", " ", t)
    t=re.sub(r"\s+", " ", t).strip()
    lead=re.match(LEAD, t)
    t=t[lead.end():] if lead else t
    if not t:
        return None
    if re.match(r"(?:what|which|list|show|tell me)\b.*\b(?:smart (?:home )?)?devices\b|"
                r"(?:what|which)\s+(?:smart\s+)?(?:lights|plugs|lamps|fans|scenes)\s+(?:do i have|have i got|are there|can you control)\b|"
                r"(?:list|show|name)\s+(?:all\s+)?(?:my|the)\s+(?:smart\s+)?(?:lights|plugs|lamps|fans|scenes)\b", t):
        if "devices" in t and not re.search(r"\bsmart\b|\bhome\b|\bcontrol\b", t):
            return None
        return {"action": "list"}
    m=re.match(r"(?:what|which)\s+(lights|lamps|plugs|fans|devices|switches)\s+(?:are|is)\s+(?:still\s+|left\s+)?on$", t)
    if m:
        return {"action": "status", "target": "*" if m.group(1)=="devices" else m.group(1)}
    m=re.match(r"(?:is|are)\s+(?:there\s+)?(?:any\s+(?:of\s+)?)?(?:the\s+|my\s+)?(.+?)\s+(?:still\s+|left\s+)?(?:on|off|lit)$", t) or \
      re.match(r"(?:did i leave|have i left)\s+(?:any\s+(?:of\s+)?)?(?:the\s+|my\s+)?(.+?)\s+on$", t)
    if m:
        return {"action": "status", "target": m.group(1).strip()}
    if NOT_A_DEVICE_COMMAND.match(t):
        return None
    t=re.sub(r"(?:\s+(?:please|now|right now|for me|thanks|thank you))+$", "", t)
    delay, t=delay_of(t)
    change, target=None, None
    patterns=[
        (r"(?:turn|switch|flip|put|power)\s+(on|off)\s+(.+)$", lambda m: (m.group(1), m.group(2))),
        (r"(?:turn|switch|flip|put|power)\s+(.+?)\s+(on|off)$", lambda m: (m.group(2), m.group(1))),
        (r"(?:kill|shut off|shut down|cut)\s+(.+)$", lambda m: ("off", m.group(1))),
        (r"(.+?)\s+(on|off)$", lambda m: (m.group(2), m.group(1))),
        (r"(?:light up|illuminate)\s+(.+)$", lambda m: ("on", m.group(1))),
    ]
    for pattern, pick in patterns:
        m=re.match(pattern, t)
        if m:
            state, target=pick(m)
            change={"on": state=="on"}
            break
    if change is None:
        m=re.match(r"(?:activate|start|run|set|recall|use)\s+(?:the\s+|my\s+)?(.+?)\s+scene$|scene\s+(.+)$|"
                   r"(?:activate|recall)\s+(?:the\s+|my\s+)?(.+)$", t)
        if m:
            name=next(g for g in m.groups() if g)
            return {"action": "scene", "target": name.strip(), "delay": delay}
        m=re.match(r"(dim|darken|brighten|lighten)\s+(.+?)(?:\s+(?:to|at)\s+.+)?$", t) or \
          re.match(r"(?:make|turn|set)\s+(.+?)\s+(brighter|dimmer|darker|lighter)$", t)
        if m:
            verb, target=(m.group(1), m.group(2)) if m.group(1) in ("dim", "darken", "brighten", "lighten") else (m.group(2), m.group(1))
            level=brightness_of(t)
            if level is not None:
                change={"brightness": level}
            else:
                change={"step": 30 if verb in ("brighten", "lighten", "brighter", "lighter") else -30}
        else:
            m=re.match(r"(?:set|make|turn|change|switch|put|color|colou?r)\s+(.+)$", t)
            body=m.group(1) if m else t
            spot=setting_start(body)
            if spot is None or spot==0:
                return None
            target, rest=body[:spot], body[spot:]
            target=re.sub(r"(?:\s+(?:to|at|brightness|color|colou?r))+\s*$", "", target)
            rgb, kelvin, cname=color_of(" "+rest+" ")
            level=brightness_of(" "+rest)
            if rgb is None and kelvin is None and level is None:
                return None
            if not m and not re.match(r"\s*(?:to|at)\b|\s*\d|\s*brightness\b", rest) and not re.search(r"\s(?:to|at|brightness)\s*$", body[:spot]+" "):
                return None #"bedroom lights to 50%" is fine, "red lights" isn't a command
            change={}
            if rgb is not None:
                change["rgb"]=rgb
            if kelvin is not None:
                change["kelvin"]=kelvin
            if level is not None:
                change["brightness"]=level
            change["name"]=cname
    target=(target or "").strip()
    while True:
        cut=re.sub(r"^(?:the|my|all|of|every|everything in|any)\s+", "", target)
        if cut==target:
            break
        target=cut
    if not target:
        return None
    if "brightness" in change:
        if not 0<=change["brightness"]<=100:
            return {"action": "error", "say": "Brightness can be 0 to 100 percent."}
        if change["brightness"]==0:
            change={"on": False}
    return {"action": "set", "target": target, "change": change, "delay": delay}

def kind_of_word(w):
    return KIND_WORDS.get(w) or KIND_WORDS.get(w+"s")

def match(target, devices, kinds=None):
    #the devices the words point at. Only "lights", "all the lights" or "everything" mean every light; any other
    #word has to be in a device's name, so "the front door" never turns into every light
    words=[w for w in words_of(target) if w not in STOP]
    kind_words={w for w in words if kind_of_word(w)}
    kinds_said=[w for w in words if kind_of_word(w)]
    want=kind_of_word(kinds_said[-1]) if kinds_said else None #"fan plug": a plug, and "fan" is part of its name
    names=set(w for w in words if (not kind_of_word(w) or w in kinds_said[:-1]) and w not in GENERIC)
    pool=[d for d in devices if (kinds is None or d.kind in kinds)]
    if names in (set(), {"everything"}, {"house"}, {"whole", "house"}, {"home"}):
        if not words and not names:
            return []
        if want=="scene":
            return []
        if names=={"everything"} and want is None:
            return [d for d in pool if d.kind in ("light", "plug", "fan")]
        if want in (None, "light"):
            single=re.search(r"\b(lamp|light|bulb)\b", target) and not re.search(r"\b(all|every|everything)\b", target)
            named=[d for d in pool if d.kind=="light" and single.group(1) in words_of(d.name)] if single else []
            return named or [d for d in pool if d.kind=="light"] #"the lamp" is the desk lamp, "the lights" are all
        return [d for d in pool if d.kind==want]
    scored=[]
    for d in pool:
        dw=set(w for w in words_of(d.name) if w not in STOP)
        if not names<=dw: #every name word has to be in the device's name
            continue
        core=set(w for w in dw if not kind_of_word(w) and w not in GENERIC)
        score=len(names)*2+len(kind_words & dw)-len(core-names)
        if want:
            if d.kind!=want and not (want=="light" and d.kind=="room"):
                continue #"the fan plug" is never the bedroom fan
            score+=1
        elif d.kind in ("light", "room"): #"turn off the bedroom" means its lights more than its fan
            score+=1
        scored.append((score, d))
    if not scored:
        return []
    best=max(s for s, d in scored)
    hits=[d for s, d in scored if s==best]
    rooms=[d for d in hits if d.kind=="room"]
    if rooms: #a hue room already covers its lights
        hits=rooms+[d for d in hits if d.kind not in ("light", "room")]
    return hits

def cap(s):
    return s[:1].upper()+s[1:]

def names_text(devices, limit=6):
    names=[d.name for d in devices[:limit]]
    more=len(devices)-limit
    if more>0:
        names.append(f"{more} more")
    if len(names)<=1:
        return "".join(names)
    return ", ".join(names[:-1])+" and "+names[-1]

def the(devices):
    if len(devices)==1:
        name=devices[0].name
        return name if re.match(r"(?i)the\b|my\b", name) else "the "+name
    return names_text(devices)

#doing it
def apply(devices, change):
    #changes every device; returns (done, problems)
    done, problems=[], []
    for d in devices:
        c={k: v for k, v in change.items() if k!="name"}
        if d.kind in ("plug", "fan") and set(c)-{"on"}:
            if "step" in c or "brightness" in c:
                c={"on": True}
            else:
                problems.append(f"{d.name} can't change color")
                continue
        try:
            d.hub.apply(d, c)
            done.append(d)
        except HubError as e:
            problems.append(str(e))
    forget_cache()
    return done, problems

def describe(change, devices):
    who=the(devices)
    if "on" in change and len(change)==1:
        return f"Turned {'on' if change['on'] else 'off'} {who}."
    if "step" in change and len(change)==1:
        return f"Made {who} {'brighter' if change['step']>0 else 'dimmer'}."
    parts=[]
    if change.get("name"):
        parts.append(change["name"].strip())
    if "brightness" in change:
        parts.append(f"{change['brightness']} percent")
    return f"Set {who} to {' at '.join(parts) if parts else 'that'}."

def handle(text, ctx):
    try:
        return run(text, ctx)
    except ctx.passthrough:
        raise
    except Exception as e:
        print("smart home err;", type(e).__name__, e)
        ctx.say("Sorry, something went wrong with the smart home.")
        return True

def mentions_device(text):
    t=text.lower()
    return bool(DEVICE_RE.search(t)) and not NOT_MINE.search(t)

def run(text, ctx):
    t=text.lower()
    if NOT_MINE.search(t) and not re.search(r"\b(lights?|lamps?|plugs?|fans?)\b", t.replace("night light", "")):
        return False
    req=parse(text)
    if req is None:
        return False
    explicit=mentions_device(text) or req["action"] in ("list", "scene")
    found, _=hubs(ctx.env)
    if not found:
        if explicit and req["action"]!="scene":
            ctx.say("Smart home isn't set up yet. Add Home Assistant or a Philips Hue bridge in Settings, Smart home.")
            return True
        return False
    if req["action"]=="error":
        ctx.say(req["say"])
        return True
    devices, problems=all_devices(ctx)
    if req["action"]=="list":
        if not devices:
            ctx.say(problems[0] if problems else "I don't see any lights, plugs, fans or scenes.")
            return True
        lines=[]
        for kind, word in (("room", "room"), ("light", "light"), ("plug", "switch"), ("fan", "fan"), ("scene", "scene")):
            group=[d for d in devices if d.kind==kind]
            if group:
                lines.append(f"{len(group)} {word}{'' if len(group)==1 else 'es' if word=='switch' else 's'}: {names_text(group, 8)}")
        ctx.post("say", "\n".join(lines))
        ctx.say("I can control "+"; ".join(lines)+".")
        return True
    if req["action"]=="status":
        hits=[d for d in devices if d.kind in ("light", "plug", "fan")] if req["target"]=="*" else \
             match(req["target"], [d for d in devices if d.kind!="scene"])
        if not hits:
            if explicit:
                ctx.say(problems[0] if problems else f"I couldn't find {req['target']}.")
                return True
            return False
        if len(hits)==1:
            d=hits[0]
            if not d.available or d.on is None:
                ctx.say(f"{cap(the(hits))} isn't answering right now.")
            elif d.on:
                ctx.say(f"{cap(the(hits))} is on" + (f", at {d.brightness} percent." if d.brightness is not None else "."))
            else:
                ctx.say(f"{cap(the(hits))} is off.")
            return True
        on=[d for d in hits if d.on and d.kind!="room"] or [d for d in hits if d.on]
        if not on:
            ctx.say("They're all off.")
        else:
            ctx.say(f"{len(on)} {'is' if len(on)==1 else 'are'} on: {names_text(on)}.")
        return True
    if req["action"]=="scene":
        return scene(req, devices, problems, t, explicit, ctx)
    if req["action"]=="set" and re.search(r"\bscenes?\b", req["target"]) and req["change"].get("on"):
        req={"action": "scene", "target": re.sub(r"\s*\bscenes?\b\s*", " ", req["target"]).strip(), "delay": req.get("delay")}
        return scene(req, devices, problems, t, explicit, ctx)
    kinds={"plug", "fan", "light", "room"} if "rgb" not in req["change"] and "kelvin" not in req["change"] else {"light", "room"}
    hits=match(req["target"], devices, kinds)
    if "step" in req["change"] or "brightness" in req["change"]:
        hits=[d for d in hits if d.kind in ("light", "room")] or hits
    if not hits:
        others=match(req["target"], [d for d in devices if d.kind!="scene"])
        if others and set(kinds)=={"light", "room"}:
            ctx.say(f"{cap(the(others))} can't change color.")
            return True
        if explicit:
            ctx.say(problems[0] if problems and not devices else f"I couldn't find {req['target']} in your smart home.")
            return True
        return False
    hits=[d for d in hits if d.available] or hits
    if req.get("delay"):
        return later(req["delay"], hits, req["change"], ctx)
    done, errs=apply(hits, req["change"])
    if done:
        ctx.say(describe(req["change"], done) + (f" But {errs[0]}." if errs else ""))
    else:
        ctx.say(errs[0] if errs else "That didn't work.")
    return True

def scene(req, devices, problems, t, explicit, ctx):
    hits=match(req["target"]+" scene", [d for d in devices if d.kind=="scene"])
    if not hits:
        if explicit and "scene" in t:
            ctx.say(problems[0] if problems and not devices else f"I couldn't find a scene called {req['target']}.")
            return True
        return False
    hits=hits[:1]
    if req.get("delay"):
        return later(req["delay"], hits, {"on": True}, ctx, f"the {hits[0].name} scene")
    done, errs=apply(hits, {"on": True})
    ctx.say(f"Started {hits[0].name}." if done else errs[0])
    return True

def later(seconds, devices, change, ctx, what=None):
    if not 1<=seconds<=MAX_DELAY:
        ctx.say("I can do that from 1 second to 12 hours from now.")
        return True
    def go():
        try:
            apply(devices, change)
        except Exception as e:
            print("smart home timer err;", type(e).__name__, e)
    timer=threading.Timer(seconds, go)
    timer.daemon=True
    timer.start()
    minutes=round(seconds/60)
    when=f"{round(seconds)} seconds" if seconds<90 else f"{minutes} minutes" if minutes<90 else f"{round(seconds/3600, 1):g} hours"
    if what:
        action="start "+what
    elif set(change)=={"on"}:
        action=f"turn {'on' if change['on'] else 'off'} {the(devices)}"
    else:
        action="change "+the(devices)
    ctx.say(f"Okay, I'll {action} in {when}.")
    return True
