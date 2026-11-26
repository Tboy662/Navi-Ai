#checks which spoken requests the music commands take, and what they understand. Run: py -3.14 tests/test_music_commands.py
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import music

CASES={
    "pause": ("pause", None), "pause the music": ("pause", None), "stop the music": ("pause", None), "Please pause Spotify.": ("pause", None),
    "resume": ("resume", None), "play some music": ("resume", None), "play music": ("resume", None), "continue playing": ("resume", None),
    "unpause": ("resume", None), "play": ("resume", None),
    "next song": ("next", None), "skip": ("next", None), "skip this song": ("next", None), "next": ("next", None), "play the next song": ("next", None),
    "previous song": ("previous", None), "go back a song": ("previous", None), "last track": ("previous", None),
    "what's playing": ("what", None), "what song is this": ("what", None), "who sings this": ("what", None), "what is this": ("what", None),
    "like this song": ("like", None), "save this song": ("like", None),
    "shuffle": ("shuffle", True), "turn on shuffle": ("shuffle", True), "turn off shuffle": ("shuffle", False), "stop shuffling": ("shuffle", False),
    "shuffle my liked songs": ("shuffle_play", "my liked songs"), "play drake on shuffle": ("shuffle_play", "drake"), "put it on shuffle": ("shuffle", True),
    "repeat this song": ("repeat", "track"), "put this on repeat": ("repeat", "track"), "turn off repeat": ("repeat", "off"),
    "repeat the playlist": ("repeat", "context"),
    "spotify volume 40": ("volume", 40), "set the music volume to 70": ("volume", 70), "turn the music down": ("volume_step", -15),
    "turn up the music": ("volume_step", 15),
    "restart this song": ("seek_to", 0), "play it again": ("seek_to", 0), "skip ahead 30 seconds": ("seek_by", 30), "go back 10 seconds": ("seek_by", -10),
    "play the music on my phone": ("transfer", "phone"), "switch to my phone": ("transfer", "phone"), "move the music to kitchen speaker": ("transfer", "kitchen speaker"),
    "spotify devices": ("devices", None),
    "connect spotify": ("connect", None), "disconnect spotify": ("disconnect", None),
    "play blinding lights by the weeknd": ("play", "blinding lights by the weeknd"), "play taylor swift": ("play", "taylor swift"),
    "play some jazz": ("play", "some jazz"), "queue up bohemian rhapsody": ("queue", "bohemian rhapsody"),
    "add hello by adele to the queue": ("queue", "hello by adele"), "play my chill playlist": ("play", "my chill playlist"),
    #not music: these must reach navi's other commands or the ai
    "repeat that": (None, None), "repeat": (None, None), "what time is it": (None, None), "pause the stopwatch": (None, None),
    "stop the timer": (None, None), "play minecraft": (None, None), "play a game": (None, None), "add milk to my shopping list": (None, None),
    "switch to dark mode": (None, None), "what's this word mean": (None, None), "what devices are on my network": (None, None),
    "open spotify": (None, None), "volume up": (None, None), "stop talking": (None, None), "pause the video": (None, None),
}
PARSE={
    "blinding lights by the weeknd": ("song", "blinding lights", "the weeknd"), "taylor swift": ("any", "taylor swift", ""),
    "some jazz": ("some", "jazz", ""), "my liked songs": ("liked", "", ""), "my chill playlist": ("playlist", "chill", "mine"),
    "the album thriller by michael jackson": ("album", "thriller", "michael jackson"), "songs by queen": ("artist", "queen", ""),
    "the playlist road trip": ("playlist", "road trip", ""), "despacito on spotify": ("any", "despacito", ""),
}
bad=0
for said, want in CASES.items():
    got=music.command(music.strip_polite(music.norm(said)))
    if got!=want:
        bad+=1
        print(f"FAIL {said!r}: got {got}, want {want}")
for said, want in PARSE.items():
    got=music.parse_request(said)
    if got!=want:
        bad+=1
        print(f"FAIL parse {said!r}: got {got}, want {want}")
assert music.REDIRECT_URI.startswith("http://127.0.0.1:")
print(f"{len(CASES)+len(PARSE)-bad} passed, {bad} failed")
sys.exit(1 if bad else 0)
