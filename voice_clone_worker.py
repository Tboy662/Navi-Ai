#navi's trained voice. This runs in its own python (the voice_clone folder, with kyutai's pocket tts and pytorch), so
#those big packages never touch navi's own. navi starts it and keeps it running; it reads one json request per line
#and answers with one json line:
#  {"cmd": "voice", "wav": "a recording.wav", "out": "voice.safetensors"}  -> turns a recording into a voice file
#  {"cmd": "say", "voice": "voice.safetensors", "text": "...", "out": "speech.wav"}
#  {"cmd": "say", "voice": "alba", ...} also works with pocket tts's own voices
import sys, json

def answer(**fields):
    sys.stdout.write(json.dumps(fields)+"\n")
    sys.stdout.flush()

try:
    import truststore
    truststore.inject_into_ssl() #the windows certificate store, like navi (needed with avg's https scanning)
except ImportError:
    pass
try:
    from pocket_tts import TTSModel, export_model_state
    import scipy.io.wavfile
    model=TTSModel.load_model()
except Exception as e:
    answer(ready=False, error=f"{type(e).__name__}: {e}")
    sys.exit(1)
answer(ready=True, sample_rate=model.sample_rate)

voices={} #voice -> its state, loaded once
for line in sys.stdin:
    try:
        req=json.loads(line)
        if req.get("cmd")=="ping": #navi warming it up, so the model is loaded before the first thing it says
            answer(ok=True)
        elif req.get("cmd")=="voice":
            export_model_state(model.get_state_for_audio_prompt(req["wav"]), req["out"])
            voices.pop(req["out"], None)
            answer(ok=True)
        elif req.get("cmd")=="say":
            if req["voice"] not in voices:
                voices[req["voice"]]=model.get_state_for_audio_prompt(req["voice"])
            audio=model.generate_audio(voices[req["voice"]], req["text"])
            pcm=(audio.detach().cpu().clamp(-1, 1)*32767).short().numpy() #16 bit, which windows' player takes
            scipy.io.wavfile.write(req["out"], model.sample_rate, pcm)
            answer(ok=True)
        else:
            answer(ok=False, error="unknown request")
    except Exception as e:
        answer(ok=False, error=f"{type(e).__name__}: {e}")
