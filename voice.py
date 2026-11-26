#import/setup
import truststore
truststore.inject_into_ssl() #use the windows certificate store (needed with AVG https scanning)
from mailbox import Mailbox
import tkinter as tk
from tkinter import ttk
from gtts import gTTS
from google import genai
import os
import sys
import subprocess
IS_WINDOWS=sys.platform=="win32"
IS_MAC=sys.platform=="darwin"
if not IS_MAC:
    from playsound import playsound
if IS_WINDOWS:
    import pyaudiowpatch as pyaudio #windows-only pyaudio fork
    sys.modules['pyaudio'] = pyaudio
#on mac speech_recognition uses the normal pyaudio package and replies play through afplay
import speech_recognition as sr
import asyncio
import random
import math
import numpy as np
from PIL import ImageGrab
import ctypes
from ctypes import wintypes
import python_weather
import threading
from imap_tools import MailBox
import email
from email.header import decode_header
import requests
from requests.structures import CaseInsensitiveDict

from tkinter import scrolledtext
import queue

#variables
test=3 #1=echo test, 2=voice only, 3=voice + gui
bot_name="navi"
enter_msg=["hello, how can i help you?","yes master?","what do you want?"]
exit_msg=["goodbye!","see you later!","may your rule be eternal!"]
ai_prompts_prev=["you are a helpful assistant. answer the user in a concise and informative manner."]
ai_api_key="AQ.Ab8RN6IvCuUAbRVpFkS5cIylq4-_sXIn74xdWyJ60p3zjB8DYA"
restaurant_api_key="eb5c8a0e40124886a2118b0dbb248ea3"
MAIL_USER = "tomer.baras@gmail.com"
MAIL_PASSWORD = "uisi qbqt vtfp oxpl"
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
CANVAS_URL = os.environ.get("CANVAS_URL", "franklinjc.instructure.com") #e.g. https://yourschool.instructure.com
CANVAS_API_KEY = os.environ.get("CANVAS_API_KEY", "22021~YBh7cR23DTNeGZ6yTNABTt3tVMUCJPYXPxRWXwc2CxPKrKGAtP6KkVznZ7WeFf22") #canvas > account > settings > new access token
b3=True
navi=False
gui_queue=None #set when the gui is running
speak_lock=threading.Lock() #only one tts at a time (they share output.mp3)
stop_event=threading.Event() #set by the gui to stop listening
if ai_api_key=="":
    print("Please set your API key in the 'ai_api_key' variable, else the AI functionality will not work.")
#-func area
class StopListening(Exception):
    pass
def gui_post(kind, value):
    if gui_queue is not None:
        gui_queue.put((kind, value))
def dummy_func():
    global b3
    b3=False
def resturant_finder(key):
    print("finder started")
    

    url = "https://api.geoapify.com/v2/places?categories=commercial.supermarket&filter=rect%3A10.716463143326969%2C48.755151258420966%2C10.835314015356737%2C48.680903341613316&limit=20&apiKey=eb5c8a0e40124886a2118b0dbb248ea3"

    headers = CaseInsensitiveDict()
    headers["Accept"] = "application/json"

    resp = requests.get(url, headers=headers)

    print(resp)
def dropdown(title,listvalues):
    root = tk.Tk()
    root.title(title)
    if not isinstance(listvalues, list):
        print("listvalues must be a list")
        return
    label = tk.Label(root, text="Select an option:")
    label.pack()
    combo = ttk.Combobox(root, values=listvalues)
    combo.pack()
    root.mainloop()


def ai_mode(key):
    global t, b3
    t=threading.Timer(300, dummy_func)

    t.start()
    gui_post("ai", True)
    try:
        ai_loop(key)
    finally:
        gui_post("ai", False)

def ai_loop(key):
    global t, b3
    text_to_speech("AI mode activated. Say 'goodbye' to exit.")
    ai_list=["you are a helpful assistant. answer the user in a concise and informative manner.do not use formatting, do not use code blocks, do not use markdown, do not use lists, do not use bullet points, do not use headings, do not use titles, do not use subtitles, do not use paragraphs, do not use line breaks, do not use new lines, do not use empty lines, do not use whitespace, do not use indentation, or anything that cannot be spoken. answer the user in a concise and informative manner."]
    b3=True
    while b3==True:
        audio=convert_audio_to_text(get_mic_input())
        if "goodbye" in audio.lower():
            text_to_speech("A mode deactivated. Goodbye!")
            b3=False
        else:
            text=audio
            text_to_speech("processing...")
            print("You said: " + text)
            ai_list.append(text)
            tts=ai_get_response(ai_list, key)
            text_to_speech(tts)
            ai_list.append("last response answered,do not answer the last response, only answer the next prompt")
            t.cancel()
            t=threading.Timer(300, dummy_func)
            t.start()
        if t.is_alive()==False and b3==True:
            text_to_speech("AI mode deactivated due to inactivity. Goodbye!")
            b3=False
        if t.is_alive()==True and b3==False:
            t.cancel()

def receive_mail():
    global mail_list
    mail_list=[]
    with MailBox(IMAP_SERVER).login(MAIL_USER, MAIL_PASSWORD, "Inbox") as mailbox:        
        messages = mailbox.fetch(criteria="UNSEEN", limit=1)
        for msg in messages:
            if not msg.subject:
                subject = "No Subject"
            else:
                decoded_parts = decode_header(msg.subject)
                subject_bytes, encoding = decoded_parts[0]
                
                if isinstance(subject_bytes, bytes):
                    subject = subject_bytes.decode(encoding or "utf-8", errors="ignore")
                else:
                    subject = subject_bytes

            print(f"New email received: {subject}")
            text_to_speech(f"You have a new email with the subject: {subject}")
            mail_list.append(msg)

def get_homework(limit=5):
    #only hits canvas when the command runs; one request for everything due from today on
    if CANVAS_URL=="" or CANVAS_API_KEY=="":
        return "Canvas is not set up. Please set CANVAS_URL and CANVAS_API_KEY."
    from datetime import datetime
    base=CANVAS_URL.strip().rstrip("/")
    if not base.startswith(("http://","https://")):
        base="https://"+base
    try:
        resp = requests.get(
            base + "/api/v1/planner/items",
            headers={"Authorization": "Bearer " + CANVAS_API_KEY},
            params={"start_date": datetime.now().astimezone().replace(hour=0,minute=0,second=0,microsecond=0).isoformat(), "per_page": 50},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print("canvas err; {0}".format(e))
        return "I couldn't reach Canvas right now."
    todo=[]
    for item in resp.json():
        if item.get("plannable_type") not in ("assignment", "quiz", "discussion_topic"):
            continue
        sub=item.get("submissions") or {}
        if sub.get("submitted") or sub.get("excused") or (item.get("planner_override") or {}).get("marked_complete"):
            continue
        due=item.get("plannable_date")
        due_text=datetime.fromisoformat(due.replace("Z","+00:00")).astimezone().strftime("%A %B %d at %H:%M") if due else "no due date"
        todo.append(f"{item['plannable'].get('title','Untitled')} for {item.get('context_name','a course')}, due {due_text}")
    if not todo:
        return "You have no pending homework. Nice!"
    out=f"You have {len(todo)} pending assignments. "
    if len(todo)>limit:
        out+=f"Here are the next {limit}. "
    return out + ". ".join(todo[:limit]) + "."

def ai_get_response(prompt,key):
    gui_post("status", "processing")
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt
    )
    return response.text
async def get_weather():
    client = python_weather.Client()
    weather = await client.get("New York")
    await client.close()
    return f"The weather in New York is {weather.temperature} degrees Celsius,and it feels like {weather.feels_like} degrees Celsius. The humidity is {weather.humidity}% and it should be{weather.description} today. It is forecasted to be {weather.daily_forecasts[0].temperature} tomorrow, with a high of {weather.daily_forecasts[0].highest_temperature} degrees Celsius and a low of {weather.daily_forecasts[0].lowest_temperature} degrees Celsius."
def text_to_speech(text, lang='en'):
    gui_post("navi", text)
    with speak_lock:
        gui_post("status", "speaking")
        tts = gTTS(text=text, lang=lang)
        filename = "output.mp3"
        tts.save(filename)
        if IS_MAC:
            subprocess.run(["afplay", filename])
        else:
            playsound(filename)
        os.remove(filename)
    gui_post("status", "idle")
#get mic func
def get_mic_input():
    rec=sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        gui_post("status", "listening")
        #short timeouts so the gui stop button is noticed quickly
        while True:
            if stop_event.is_set():
                raise StopListening
            try:
                audio=rec.listen(source, timeout=1)
                gui_post("status", "processing")
                return audio
            except sr.WaitTimeoutError:
                pass
def convert_audio_to_text(audio):
    rec=sr.Recognizer()
    try:
        text = rec.recognize_google(audio)
        gui_post("you", text)
        return text
    except sr.UnknownValueError:
        print("Dat audio kinda buns(unclear speech)")
        gui_post("system", "Unclear speech, listening again...")
        if not not navi:
            text_to_speech("I didn't catch that. Could you please repeat?")
        return convert_audio_to_text(get_mic_input())
    except sr.RequestError as e:
        print("your wifi kinda ass(wifi err); {0}".format(e))
        gui_post("system", "Connection error: {0}".format(e))
        if not not navi:
            text_to_speech("I'm sorry, but I'm having trouble connecting to the internet.")
        return ""



#command area
def handle_command(text):
    global navi
    if "goodbye" in text.lower():
        text_to_speech(exit_msg[random.randint(0, len(exit_msg)-1)])
        navi=False
        gui_post("awake", False)
    elif "weather" in text.lower():
        out=asyncio.run(get_weather())
        text_to_speech(out)
    elif "mail" in text.lower():
        receive_mail()
        if len(mail_list)>0:
            text_to_speech("would you like me to read your latest email?")
            res=convert_audio_to_text(get_mic_input())
            if "yes" in res.lower():
                    text_to_speech(mail_list[0].text)
        else:
            gui_post("system", "No unread mail.")
    elif "homework" in text.lower() or "canvas" in text.lower() or "assignment" in text.lower():
        text_to_speech(get_homework())
    elif "ai" in text.lower():
        ai_mode(ai_api_key)
    elif "restaurant" in text.lower():
        resturant_finder(restaurant_api_key)

def assistant_loop():
    global navi
    while not stop_event.is_set():
        audio=convert_audio_to_text(get_mic_input())
        if bot_name in audio.lower():
            navi=True
            gui_post("awake", True)
            text_to_speech(enter_msg[random.randint(0, len(enter_msg)-1)])
            while navi:
                handle_command(convert_audio_to_text(get_mic_input()))

#gui area
UI_FONT="Segoe UI" if IS_WINDOWS else "Helvetica Neue" if IS_MAC else "TkDefaultFont"
class BLENDFUNCTION(ctypes.Structure):
    _fields_=[("BlendOp",ctypes.c_ubyte),("BlendFlags",ctypes.c_ubyte),("SourceConstantAlpha",ctypes.c_ubyte),("AlphaFormat",ctypes.c_ubyte)]
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_=[("biSize",wintypes.DWORD),("biWidth",wintypes.LONG),("biHeight",wintypes.LONG),("biPlanes",wintypes.WORD),
              ("biBitCount",wintypes.WORD),("biCompression",wintypes.DWORD),("biSizeImage",wintypes.DWORD),
              ("biXPelsPerMeter",wintypes.LONG),("biYPelsPerMeter",wintypes.LONG),("biClrUsed",wintypes.DWORD),("biClrImportant",wintypes.DWORD)]

class StatusOrb:
    #siri style orb in the bottom-right corner, drawn as a per-pixel alpha (layered) window
    SIZE=96
    ALPHA=0.78 #how see-through the circles are
    PALETTES={ #circle colors, speed, wobble
        "inactive":(["#0a1a5c","#12308a","#050d33"], 0.6, 3),
        "active":(["#1e90ff","#00d4ff","#3d5afe"], 1.6, 8),
        "busy":(["#00c853","#1faa00","#00e640"], 6.0, 12), #processing in normal mode
        "ai":(["#ff2d55","#ff6a3d","#d1003a"], 2.2, 9),
        "ai_busy":(["#ff6d00","#ff5000","#e65100"], 7.0, 13), #processing in ai mode
    }

    def __init__(self, root):
        self.win=tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        s=self.SIZE
        self.win.geometry(f"{s}x{s}")
        self.win.update_idletasks()
        self.x,self.y=self.place()
        self.win.geometry(f"{s}x{s}+{self.x}+{self.y}")

        self.state="inactive"
        self.loud=0.0 #extra pulse while speaking
        self.colors=[self.to_rgb(c) for c in self.PALETTES["inactive"][0]]
        self.speed,self.wobble=self.PALETTES["inactive"][1:]
        self.phase=0.0
        self.bg=0.0 #brightness of what's behind the orb, eased
        self.bg_target=0.0
        yy,xx=np.mgrid[0:s,0:s].astype(np.float32)
        self.xx,self.yy=xx,yy
        if IS_WINDOWS:
            self.setup_layered()
        else:
            self.setup_mac()
        self.sample_background()
        self.animate()

    @staticmethod
    def to_rgb(c):
        return np.array([int(c[i:i+2],16) for i in (1,3,5)], dtype=np.float32)/255

    def place(self):
        #bottom-right, just above the taskbar
        right,bottom=self.win.winfo_screenwidth(),self.win.winfo_screenheight()-48
        try:
            rect=wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0) #SPI_GETWORKAREA
            right,bottom=rect.right,rect.bottom
        except Exception:
            pass
        if IS_MAC:
            bottom=self.win.winfo_screenheight()-80 #leave room for the dock
        return right-self.SIZE-4, bottom-self.SIZE-4

    def setup_layered(self):
        #tk can't do per-pixel transparency, so we hand windows a premultiplied BGRA bitmap each frame
        user32,gdi32=ctypes.windll.user32,ctypes.windll.gdi32
        user32.UpdateLayeredWindow.argtypes=[wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
            ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        user32.GetDC.restype=wintypes.HDC
        gdi32.CreateCompatibleDC.restype=wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes=[wintypes.HDC]
        gdi32.CreateDIBSection.restype=wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes=[wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        gdi32.SelectObject.argtypes=[wintypes.HDC, wintypes.HGDIOBJ]
        self.hwnd=int(self.win.wm_frame(),16)
        ex=user32.GetWindowLongW(self.hwnd, -20) #GWL_EXSTYLE
        user32.SetWindowLongW(self.hwnd, -20, ex|0x80000|0x20|0x80) #layered, click-through, no taskbar
        s=self.SIZE
        bmi=BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), s, -s, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits=ctypes.c_void_p()
        self.mem_dc=gdi32.CreateCompatibleDC(None)
        bmp=gdi32.CreateDIBSection(self.mem_dc, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        gdi32.SelectObject(self.mem_dc, bmp)
        self.screen_dc=user32.GetDC(None)
        self.blend=BLENDFUNCTION(0, 0, 255, 1) #AC_SRC_OVER, use per-pixel alpha

    def setup_mac(self):
        #tk on macos can make a window fully transparent, so the orb is just an rgba image on it
        from PIL import ImageTk
        self.ImageTk=ImageTk
        self.win.attributes("-transparent", True)
        self.win.configure(bg="systemTransparent")
        self.label=tk.Label(self.win, bg="systemTransparent", bd=0, highlightthickness=0)
        self.label.pack()
        self.photo=None

    def show_mac(self, rgb, alpha):
        #tk wants straight (not premultiplied) alpha
        from PIL import Image
        straight=rgb/np.maximum(alpha, 1e-6)[...,None]
        rgba=np.dstack((straight, alpha))
        img=Image.fromarray((np.clip(rgba,0,1)*255).astype(np.uint8), "RGBA")
        self.photo=self.ImageTk.PhotoImage(img) #keep a reference or tk drops the image
        self.label.configure(image=self.photo)

    def show_windows(self, rgb, alpha):
        s=self.SIZE
        bgra=np.dstack((rgb[...,2], rgb[...,1], rgb[...,0], alpha))
        buf=(np.clip(bgra,0,1)*255).astype(np.uint8).tobytes()
        ctypes.memmove(self.bits, buf, len(buf))
        ctypes.windll.user32.UpdateLayeredWindow(self.hwnd, self.screen_dc,
            ctypes.byref(wintypes.POINT(self.x,self.y)), ctypes.byref(wintypes.SIZE(s,s)),
            self.mem_dc, ctypes.byref(wintypes.POINT(0,0)), 0, ctypes.byref(self.blend), 2) #ULW_ALPHA

    def sample_background(self):
        #look at a strip just left of the orb so colors can lighten/darken to stay visible
        try:
            s=self.SIZE
            strip=ImageGrab.grab(bbox=(self.x-12, self.y+s//4, self.x-2, self.y+s*3//4))
            self.bg_target=float(np.asarray(strip.convert("L"), dtype=np.float32).mean()/255)
        except Exception:
            pass
        self.win.after(1000, self.sample_background)

    def set_state(self, state):
        self.state=state

    def set_loud(self, loud):
        self.loud=1.0 if loud else 0.0

    def animate(self):
        target,speed,wobble=self.PALETTES[self.state]
        #ease toward the target state so changes fade instead of snapping
        self.colors=[c+(self.to_rgb(t)-c)*0.12 for c,t in zip(self.colors,target)]
        self.speed+=(speed-self.speed)*0.1
        self.wobble+=(wobble+self.loud*4-self.wobble)*0.1
        self.bg+=(self.bg_target-self.bg)*0.08
        self.phase+=0.033*self.speed

        s=self.SIZE; mid=s/2
        pulse=1+0.08*math.sin(self.phase*2.3)*(1+self.loud)
        light=np.zeros((s,s,3), dtype=np.float32)
        clear=np.ones((s,s), dtype=np.float32) #how much background still shows through
        for b,color in enumerate(self.colors):
            #shift colors away from the background: lighter on dark, deeper on light
            if self.bg<0.5:
                k=(0.5-self.bg)*2
                color=np.minimum(color*(1+0.8*k), 1) #brighter but same hue
                color=color+(1-color)*0.06*k
            else:
                color=color*(1-0.3*(self.bg-0.5)*2)
            ang=self.phase+b*2.094
            cx=mid+math.cos(ang)*self.wobble*(1+0.4*math.sin(self.phase*1.7+b))
            cy=mid+math.sin(ang*1.3)*self.wobble
            r=(17+2.5*math.sin(self.phase*1.1+b*1.7))*pulse
            d=np.hypot(self.xx-cx, self.yy-cy)
            disk=np.clip(r-d+0.5, 0, 1) #crisp anti-aliased edge
            shade=0.6+0.4*np.clip(1-d/r, 0, 1) #a bit brighter toward the middle
            light+=(disk*shade)[...,None]*color*1.4
            clear*=1-disk*self.ALPHA
        alpha=1-clear
        #tone map on the brightest channel so overlaps glow without shifting the hue, then premultiply
        peak=np.maximum(light.max(axis=2), 1e-6)
        rgb=light*((1-np.exp(-peak*1.3))/peak*alpha)[...,None]
        if IS_WINDOWS:
            self.show_windows(rgb, alpha)
        else:
            self.show_mac(rgb, alpha)
        self.win.after(33, self.animate)

class NoOrb:
    #other systems can't draw a see-through window, so they skip the orb
    def set_state(self, state):
        pass
    def set_loud(self, loud):
        pass

class NaviGUI:
    COLORS={"bg":"#1e1f26","panel":"#2a2c36","text":"#e6e6eb","muted":"#8b8d98",
            "you":"#7cc4ff","navi":"#b39dff","system":"#8b8d98","accent":"#6c5ce7"}
    STATUS={"stopped":("Stopped","#e05561"),"idle":("Idle","#8b8d98"),
            "listening":("Listening...","#3ecf8e"),"processing":("Thinking...","#f5b84d"),
            "speaking":("Speaking...","#7cc4ff")}

    def __init__(self, root):
        global gui_queue
        gui_queue=queue.Queue()
        self.root=root
        self.worker=None
        c=self.COLORS
        root.title(bot_name.capitalize()+" Voice Assistant")
        root.geometry("560x620")
        root.minsize(420,420)
        root.configure(bg=c["bg"])

        #header: name + status light
        header=tk.Frame(root, bg=c["bg"])
        header.pack(fill="x", padx=16, pady=(16,8))
        tk.Label(header, text=bot_name.upper(), font=(UI_FONT,20,"bold"),
                 fg=c["text"], bg=c["bg"]).pack(side="left")
        self.status_text=tk.Label(header, font=(UI_FONT,11), fg=c["muted"], bg=c["bg"])
        self.status_text.pack(side="right")
        self.dot=tk.Canvas(header, width=14, height=14, bg=c["bg"], highlightthickness=0)
        self.dot_id=self.dot.create_oval(2,2,12,12, outline="")
        self.dot.pack(side="right", padx=6)
        self.awake_text=tk.Label(root, text=f'Say "{bot_name}" to wake me up',
                                 font=(UI_FONT,10), fg=c["muted"], bg=c["bg"], anchor="w")
        self.awake_text.pack(fill="x", padx=16)

        #conversation log
        self.log=scrolledtext.ScrolledText(root, wrap="word", font=(UI_FONT,11),
                                           bg=c["panel"], fg=c["text"], relief="flat",
                                           padx=10, pady=10, state="disabled")
        self.log.pack(fill="both", expand=True, padx=16, pady=8)
        for tag in ("you","navi","system"):
            self.log.tag_config(tag, foreground=c[tag])
        self.log.tag_config("name", font=(UI_FONT,11,"bold"))

        #buttons
        buttons=tk.Frame(root, bg=c["bg"])
        buttons.pack(fill="x", padx=16)
        self.make_button(buttons, "Weather", lambda: self.run_task(handle_command, "weather")).pack(side="left", padx=(0,6))
        self.make_button(buttons, "Check mail", lambda: self.run_task(handle_command, "mail")).pack(side="left")
        self.make_button(buttons, "Homework", lambda: self.run_task(handle_command, "homework")).pack(side="left", padx=6)
        self.make_button(buttons, "Clear", self.clear_log).pack(side="right")

        #typed questions go straight to the ai
        entry_row=tk.Frame(root, bg=c["bg"])
        entry_row.pack(fill="x", padx=16, pady=(8,16))
        self.entry=tk.Entry(entry_row, font=(UI_FONT,11), bg=c["panel"], fg=c["text"],
                            insertbackground=c["text"], relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda e: self.send_text())
        self.make_button(entry_row, "Ask", self.send_text, c["accent"]).pack(side="left", padx=(6,0))

        self.awake=False
        self.ai=False
        self.processing=False
        self.typed=False
        self.orb=StatusOrb(root) if IS_WINDOWS or IS_MAC else NoOrb()

        self.set_status("stopped")
        self.add_line("system", f'Always listening. Say "{bot_name}" or type a question below.')
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll_queue)
        self.start_listening()

    def make_button(self, parent, text, command, color=None):
        c=self.COLORS
        return tk.Button(parent, text=text, command=command, font=(UI_FONT,10),
                         bg=color or c["panel"], fg=c["text"], activebackground=c["accent"],
                         activeforeground=c["text"], relief="flat", padx=12, pady=6, cursor="hand2")

    def add_line(self, kind, text):
        names={"you":"You","navi":bot_name.capitalize(),"system":"•"}
        self.log.configure(state="normal")
        self.log.insert("end", names[kind]+"  ", (kind,"name"))
        self.log.insert("end", text+"\n\n", kind)
        self.log.configure(state="disabled")
        self.log.see("end")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0","end")
        self.log.configure(state="disabled")

    def set_status(self, state):
        label,color=self.STATUS[state]
        self.status_text.configure(text=label, fg=color)
        self.dot.itemconfig(self.dot_id, fill=color)

    def poll_queue(self):
        #background threads can't touch tkinter, so they post here instead
        while not gui_queue.empty():
            kind,value=gui_queue.get()
            if kind=="status":
                if value=="idle" and self.worker is None:
                    value="stopped"
                self.set_status(value)
                self.orb.set_loud(value=="speaking")
                self.processing=value=="processing"
                self.update_orb()
            elif kind=="typed":
                self.typed=value
                self.update_orb()
            elif kind=="ai":
                self.ai=value
                self.update_orb()
            elif kind=="awake":
                self.awake=value
                self.update_orb()
                self.awake_text.configure(text="Awake - say a command (weather, mail, homework, ai, goodbye)" if value
                                          else f'Say "{bot_name}" to wake me up')
            elif kind=="stopped":
                self.worker=None
                self.set_status("stopped")
                if not stop_event.is_set():
                    self.root.after(2000, self.start_listening) #recover from errors (mic unplugged etc.)
            else:
                self.add_line(kind, value)
        self.root.after(100, self.poll_queue)

    def update_orb(self):
        #words heard while asleep are only checked for the wake word, so they don't count as busy
        busy=self.processing and (self.awake or self.ai or self.typed)
        if self.ai:
            state="ai_busy" if busy else "ai"
        elif busy:
            state="busy"
        else:
            state="active" if self.awake else "inactive"
        self.orb.set_state(state)

    def start_listening(self):
        if self.worker is None and not stop_event.is_set():
            self.worker=threading.Thread(target=self.listen_worker, daemon=True)
            self.worker.start()

    def listen_worker(self):
        global navi
        try:
            assistant_loop()
        except StopListening:
            pass
        except Exception as e:
            gui_post("system", f"Error: {e}, restarting listener...")
        navi=False
        gui_post("awake", False)
        gui_post("stopped", None)

    def run_task(self, func, *args):
        def task():
            try:
                func(*args)
            except StopListening:
                pass
            except Exception as e:
                gui_post("system", f"Error: {e}")
        threading.Thread(target=task, daemon=True).start()

    def send_text(self):
        text=self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0,"end")
        self.add_line("you", text)
        prompt=ai_prompts_prev[0]+" do not use formatting or anything that cannot be spoken. "+text
        self.typed=True
        gui_post("status", "processing")
        def answer():
            try:
                reply=ai_get_response(prompt, ai_api_key)
            finally:
                gui_post("typed", False)
            text_to_speech(reply)
        self.run_task(answer)

    def on_close(self):
        stop_event.set()
        self.root.destroy()

#main code
if test==1:
    audio = get_mic_input()
    text = convert_audio_to_text(audio)
    print("You said: " + text)
    text_to_speech(text)
elif test==2:
    try:
        assistant_loop()
    except KeyboardInterrupt:
        text_to_speech("Goodbye!")
elif test==3:
    root=tk.Tk()
    NaviGUI(root)
    root.mainloop()

