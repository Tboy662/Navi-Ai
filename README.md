# Navi AI

Navi is an always-listening voice assistant for Windows and macOS. Say **"Navi"** to wake it up, then ask for the weather, your unread mail, your Canvas homework, or switch into a Gemini-powered AI chat. It comes with a small chat window and a glowing status orb in the corner of your screen.

## Requirements

- **Windows 10/11 or macOS.** On Linux the window works but the status orb is turned off, and it hasn't been tested there.
- **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/). On Windows, tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" selected during install (Navi's window needs tkinter). The python.org installer for Mac includes tkinter too.
- **Mac only:** [Homebrew](https://brew.sh), to install PortAudio for the microphone.
- A **microphone** and **speakers**.
- An **internet connection**. Speech recognition, text to speech, the AI and every command go online.

## Setup

1. Get the code:

   ```powershell
   git clone https://github.com/Tboy662/Navi-Ai.git
   cd Navi-Ai
   ```

2. (Recommended) Make a virtual environment so Navi's packages stay separate:

   ```powershell
   # Windows
   python -m venv .venv
   .venv\Scripts\activate
   ```

   ```bash
   # macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install the dependencies. On a Mac, install PortAudio first:

   ```bash
   brew install portaudio   # macOS only
   pip install -r requirements.txt
   ```

4. Fill in your keys (next section).

## Keys and accounts

Navi reads its settings from variables near the top of `voice.py`, under `#variables`. Each feature needs its own key. If you leave one empty, the other features still work.

| Variable | Used for | How to get it |
| --- | --- | --- |
| `ai_api_key` | The "ai" voice mode and questions typed in the window | Create a free Gemini API key at [Google AI Studio](https://aistudio.google.com/apikey). |
| `MAIL_USER` | The "mail" command | Your Gmail address. |
| `MAIL_PASSWORD` | The "mail" command | A Gmail **app password**, not your normal password. Turn on 2-Step Verification, then create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). IMAP must be enabled in Gmail settings. |
| `CANVAS_URL` | The "homework" command | Your school's Canvas address, for example `yourschool.instructure.com`. Can also be set as the `CANVAS_URL` environment variable. |
| `CANVAS_API_KEY` | The "homework" command | In Canvas go to Account > Settings > New Access Token. Can also be set as the `CANVAS_API_KEY` environment variable. |
| `restaurant_api_key` | The "restaurant" command (work in progress) | A [Geoapify](https://www.geoapify.com/) API key. |

Weather needs no key. It currently reports New York; change the city in `get_weather()` if you want somewhere else.

Keep these keys private. Don't commit a copy of `voice.py` with your real keys to a public repository.

## Running Navi

```powershell
python voice.py      # use python3 on macOS
```

The window opens and Navi starts listening right away.

On a Mac, the first run asks for **Microphone** access for your terminal app; allow it. The orb also peeks at the screen behind it to adjust its colors, which needs **Screen Recording** access (System Settings > Privacy & Security). Without it everything still works and the orb just keeps its default colors.

- Say **"Navi"** to wake it. It answers and waits for a command.
- Commands: **"weather"**, **"mail"**, **"homework"** (or "canvas" / "assignment"), **"ai"**, **"restaurant"**.
- In AI mode, just talk. Say **"goodbye"** to leave AI mode. It also exits after 5 minutes of silence.
- Say **"goodbye"** to put Navi back to sleep.
- You can also type a question in the box at the bottom and press **Ask**, or use the Weather / Check mail / Homework buttons.

The orb in the bottom-right corner shows what Navi is doing: dark blue when asleep, bright blue when awake, green while thinking, and red/orange in AI mode. On Windows you can click straight through it; on a Mac it sits on top of whatever is in that corner.

### Run modes

The `test` variable at the top of `voice.py` picks how Navi runs:

- `3` (default): voice plus the window and orb.
- `2`: voice only, in the terminal.
- `1`: echo test. Records one phrase and reads it back, handy for checking your mic and speakers.

### Sounds

In the GUI, Navi plays two quiet cues: a soft chime when it switches mode (asleep, awake, or one of the AI modes) and a short blip when it starts thinking. They play in the background through `winsound` on Windows and `afplay` on Mac, so nothing extra needs installing. The files live in `sounds/`, `tools/make_sounds.py` regenerates them, and setting `SOUNDS_ENABLED = False` in `sounds.py` mutes them.

## Troubleshooting

- **`pip install` fails on PyAudioWPatch (Windows):** make sure you have 64-bit Python 3.10+. Upgrade pip with `python -m pip install --upgrade pip` and try again.
- **`pip install` fails on PyAudio (Mac):** run `brew install portaudio` first, then install again.
- **No sound / playsound error (Windows):** keep `playsound` at version 1.2.2, as pinned in `requirements.txt`. Version 1.3.0 is broken on Windows.
- **Navi never hears you:** check that apps are allowed to use the microphone (Windows: Settings > Privacy & security > Microphone; Mac: System Settings > Privacy & Security > Microphone, for your terminal app) and that the right mic is the default input device. Run with `test=1` to check.
- **SSL / certificate errors:** Navi already uses the Windows certificate store through `truststore`, which fixes most antivirus HTTPS scanning issues (AVG, Avast).
- **"I couldn't reach Canvas":** check `CANVAS_URL` and that your access token hasn't expired.
