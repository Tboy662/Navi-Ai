# Navi AI

Navi is an always-listening voice assistant for Windows and macOS. Say **"Navi"** to wake it up, then ask for the weather, your unread mail, your Canvas homework, or switch into a Gemini-powered AI chat. It comes with a small chat window and a glowing status orb in the corner of your screen.

## Install

**Windows:** download `Navi-Setup.exe` and run it. It installs Navi just for you (no admin rights) to `%LOCALAPPDATA%\Programs\Navi`, then walks you through each key with a link to where you get it, and saves them to `.env` in that folder. Python isn't needed. Afterwards:

- **Navi** and **Navi key setup** (change your keys) are in the Start menu.
- To remove it, use Settings > Apps > Navi voice assistant, or **Uninstall Navi** in the Start menu. It removes the folder, shortcuts and registry entry. You can tick "Keep my keys" to save them for a later install.
- Running a newer `Navi-Setup.exe` updates Navi and keeps your keys, settings and notes.

**macOS:** install Python 3.10+ from [python.org](https://www.python.org/downloads/macos/) and [Homebrew](https://brew.sh), then in Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Tboy662/Navi-Ai/main/install-mac.sh | bash
```

It installs Navi's code and its own packages in `~/Library/Application Support/Navi`, makes `~/Applications/Navi.app`, clears the Gatekeeper quarantine flag on Navi's files so it opens without warnings, and opens the same key setup window. Change keys later with `open -a Navi --args --keys`. Uninstall with `bash ~/Library/Application\ Support/Navi/app/install-mac.sh --uninstall` (add `--keep-keys` to keep your `.env`).

To run from source instead, follow the steps below.

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

Navi reads its keys from a `.env` file next to `voice.py`. Copy `.env.example` to `.env` and fill it in, or run `python voice.py --keys` for the same guided key setup the installer uses. Each feature needs its own key. If you leave one empty, the other features still work.

| `.env` name | Used for | How to get it |
| --- | --- | --- |
| `GROQ_API_KEY` | Navi's main AI model | Create a free key at [console.groq.com/keys](https://console.groq.com/keys). |
| `OPENROUTER_API_KEY` | Extra backup AI models | Optional. Create one at [openrouter.ai/keys](https://openrouter.ai/keys). |
| `HF_TOKEN` | Speaking in your trained voice | Optional. Accept the conditions at [huggingface.co/kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts), then make a Read token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). |
| `GEMINI_API_KEY` | The "ai" voice mode and questions typed in the window | Create a free Gemini API key at [Google AI Studio](https://aistudio.google.com/apikey). |
| `MAIL_USER` | The "mail" command | Your Gmail address. |
| `MAIL_PASSWORD` | The "mail" command | A Gmail **app password**, not your normal password. Turn on 2-Step Verification, then create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). IMAP must be enabled in Gmail settings. |
| `IMAP_SERVER`, `IMAP_PORT` | The "mail" command | Optional. Default to `imap.gmail.com` and `993`. |
| `CANVAS_URL` | The "homework" command | Your school's Canvas address, for example `yourschool.instructure.com`. |
| `CANVAS_API_KEY` | The "homework" command | In Canvas go to Account > Settings > New Access Token. |
| `GEOAPIFY_API_KEY` | The "restaurant" command (work in progress) | A [Geoapify](https://www.geoapify.com/) API key. |

You can also view and change every key in Navi under **Settings > Keys**: they're hidden until you press Show, and saving puts them to use right away without a restart.

Real environment variables with the same names also work and take priority over `.env`.

Weather needs no key. It currently reports New York; change the city in `get_weather()` if you want somewhere else.

Keep these keys private. `.env` is in `.gitignore`, so it won't be committed. Never put real keys in `voice.py`.

## Running Navi

```powershell
python voice.py      # use python3 on macOS
```

The window opens and Navi starts listening right away.

On a Mac, the first run asks for **Microphone** access for your terminal app; allow it. The orb also peeks at the screen behind it to adjust its colors, which needs **Screen Recording** access (System Settings > Privacy & Security). Without it everything still works and the orb just keeps its default colors.

- Say **"Navi"** to wake it. It answers and waits for a command.
- Commands: **"weather"**, **"mail"**, **"homework"**, **"remind me..."**, **"what time is it"**, **"take a note"**, **"ai"** and many more. Say **"what can you do"**, or see [COMMANDS.md](COMMANDS.md) for the full list.
- In AI mode, just talk. Say **"goodbye"** to leave AI mode. It also exits after 5 minutes of silence.
- Say **"goodbye"** to put Navi back to sleep.
- You can also type a question in the box at the bottom and press **Ask**, or use the Weather / Check mail / Homework buttons.

The orb in the bottom-right corner shows what Navi is doing: dark blue when asleep, bright blue when awake, green while thinking, and red/orange in AI mode. On Windows you can click straight through it; on a Mac it sits on top of whatever is in that corner.

### On your phone

Navi has a free phone app for Android and iPhone: turn it on in **Settings > Phone app**, scan the QR code, and add it to your home screen. It works on your home Wi-Fi, and anywhere with the free Tailscale app. See [COMMANDS.md](COMMANDS.md#phone-app-new).

### Run modes

The `test` variable at the top of `voice.py` picks how Navi runs:

- `3` (default): voice plus the window and orb.
- `2`: voice only, in the terminal.
- `1`: echo test. Records one phrase and reads it back, handy for checking your mic and speakers.

### Sounds

In the GUI, Navi plays two quiet cues: a soft chime when it switches mode (asleep, awake, or one of the AI modes) and a short blip when it starts thinking. They play in the background through `winsound` on Windows and `afplay` on Mac, so nothing extra needs installing. The files live in `sounds/`, `tools/make_sounds.py` regenerates them, and setting `SOUNDS_ENABLED = False` in `sounds.py` mutes them.

## Building the Windows installer

```powershell
py -3.14 -m pip install -r requirements.txt pyinstaller
py -3.14 packaging\build_windows.py
```

This makes `dist\Navi-Setup.exe`. Navi is compiled as a folder (so it starts fast, with nothing to unpack at launch), zipped, and glued onto the end of the small setup program, which starts unpacking while you type your keys. The build copies in only the files Navi needs (sounds, the offline wake word models, the Minecraft guides) and stops if any key from your `.env` or `navi_settings.json` shows up in it, so your keys are never shipped. Agent browser mode is included; its Chromium (about 150 MB) isn't, and downloads into Navi's `agent_browser` folder the first time agent mode opens a page.

## Troubleshooting

- **`pip install` fails on PyAudioWPatch (Windows):** make sure you have 64-bit Python 3.10+. Upgrade pip with `python -m pip install --upgrade pip` and try again.
- **`pip install` fails on PyAudio (Mac):** run `brew install portaudio` first, then install again.
- **No sound / playsound error (Windows):** keep `playsound` at version 1.2.2, as pinned in `requirements.txt`. Version 1.3.0 is broken on Windows.
- **Navi never hears you:** check that apps are allowed to use the microphone (Windows: Settings > Privacy & security > Microphone; Mac: System Settings > Privacy & Security > Microphone, for your terminal app) and that the right mic is the default input device. Run with `test=1` to check.
- **SSL / certificate errors:** Navi already uses the Windows certificate store through `truststore`, which fixes most antivirus HTTPS scanning issues (AVG, Avast).
- **"I couldn't reach Canvas":** check `CANVAS_URL` and that your access token hasn't expired.
