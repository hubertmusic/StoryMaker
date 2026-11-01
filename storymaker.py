#!/usr/bin/env python3
"""
StoryMaker — writes a story with AI, narrates it, and turns it into a video.

Video can be either
  * real AI video clips, one per scene (Google Gemini Omni or Veo 3.1), with the
    narrator laid over each clip's own sound, or
  * a picture slideshow (one AI picture per scene with a slow zoom).

Extras
  * --estimate        print the approximate cost and stop (nothing is spent)
  * cost guard        refuses runs above MAX_COST_USD unless --yes is given
  * resume            work already paid for (story, clips, pictures, voice) is kept;
                      re-running the same request only pays for what's missing
  * character sheet   one reference picture keeps characters looking the same
  * --background      returns at once, finishes in the background, then sends a
                      push notification (ntfy) with the link
  * music             a track from your music folder, ducked under the narrator

Built to be called from Apple Shortcuts:
  * Mac:     "Run Shell Script"        (pass the story idea as stdin)
  * Server:  "Run Script Over SSH"     (pass the story idea as input/stdin)

It prints ONE line of JSON to stdout, so the shortcut can read the results with
"Get Dictionary from Input". Progress messages go to stderr.

Usage examples
  echo "a lighthouse keeper who befriends a storm" | python3 storymaker.py --estimate
  echo "a lighthouse keeper who befriends a storm" | python3 storymaker.py --yes
  python3 storymaker.py --idea "test" --provider demo --video demo --yes      # no keys, no cost

Settings live in storymaker.env (next to this file, or ~/.storymaker.env).
"""

import argparse
import base64
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

try:
    import requests
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("Missing packages: run  pip install requests pillow")

HERE = Path(__file__).resolve().parent
IS_MAC = platform.system() == "Darwin"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".aiff"}

# Shortcuts runs scripts with a bare PATH, so add the usual tool locations.
os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])


# ───────────────────────────── settings ─────────────────────────────

DEFAULTS = {
    "STORY_PROVIDER": "claude",            # claude | gemini | openai | demo
    "VIDEO_PROVIDER": "auto",              # auto | omni | veo | slideshow | demo
    "IMAGE_PROVIDER": "auto",              # auto | local | openai | gemini | cards
    "LOCAL_IMAGE_URL": "",                 # free pictures on your Mac: Draw Things' API server, e.g. http://localhost:7860
    "LOCAL_IMAGE_STEPS": "24",
    "LOCAL_IMAGE_CFG": "6",
    "COVER_ART": "on",                     # paint a cover for every story when pictures are free (local)
    "TTS_PROVIDER": "auto",                # auto | elevenlabs | openai | gemini | say | edge | espeak | silent

    "ANTHROPIC_API_KEY": "",
    "GEMINI_API_KEY": "",
    "OPENAI_API_KEY": "",
    "ELEVENLABS_API_KEY": "",

    "CLAUDE_MODEL": "claude-sonnet-5-5",
    "GEMINI_TEXT_MODEL": "gemini-4-argon",
    "OPENAI_TEXT_MODEL": "gpt-5-mini",
    "GROQ_API_KEY": "",
    "GROQ_MODEL": "openai/gpt-oss-120b",
    "OLLAMA_URL": "http://localhost:11434",  # in Docker on a Mac: http://host.docker.internal:11434
    "OLLAMA_MODEL": "llama3.1:8b",

    "OMNI_MODEL": "gemini-omni-1.1-flash",
    "VEO_MODEL": "veo-3.1-fast-generate-preview",
    "VIDEO_RESOLUTION": "720p",
    "CLIP_SOUND_VOLUME": "0.25",
    "PARALLEL_CLIPS": "3",
    "CHARACTER_REF": "on",                 # make one character picture and show it to every clip

    "GEMINI_IMAGE_MODEL": "gemini-3.1-flash-image",
    "OPENAI_IMAGE_MODEL": "gpt-image-1",
    "OPENAI_IMAGE_QUALITY": "medium",

    "ELEVENLABS_VOICE_ID": "JBFqnCBsd6RMkjVDRZzb",
    "ELEVENLABS_MODEL": "eleven_multilingual_v2",
    "OPENAI_TTS_MODEL": "gpt-4o-mini-tts",
    "OPENAI_VOICE": "fable",
    "GEMINI_TTS_MODEL": "gemini-3.8-flash-tts",
    "GEMINI_VOICE": "Charon",
    "SAY_VOICE": "",
    "EDGE_VOICE": "en-US-AndrewMultilingualNeural",
    # Kokoro: free, smooth voices that run on this computer (no internet, no account).
    "KOKORO_VOICE": "af_heart",            # the narrator
    "KOKORO_SPEED": "0.95",
    "KOKORO_DIR": "~/StoryMaker/kokoro",   # model files are downloaded here the first time (about 120 MB)

    # Character voices: multi = each speaking character gets their own voice; single = narrator only.
    # Pools are "type=voice,voice;type=voice" where type is female, male, girl, boy, old_woman,
    # old_man or creature. Characters of the same type get different voices from the list in turn.
    "VOICES": "multi",
    "ELEVENLABS_VOICES": ("female=21m00Tcm4TlvDq8ikWAM,EXAVITQu4vr4xnSDxMaL;male=pNInz6obpgDQGcFmaJgB,ErXwobaYiN019PkySvjV;"
                          "girl=MF3mGyEYCl7XYWbV9V6O;boy=TxGEqnHWrfWFTfGW9XjX;old_woman=EXAVITQu4vr4xnSDxMaL;"
                          "old_man=VR6AewLTigWG4xSOukaG;creature=yoZ06aMxZJJ28mfd3POQ"),
    "OPENAI_VOICES": "female=nova,shimmer,coral;male=onyx,echo,ash;girl=coral;boy=verse;old_woman=sage;old_man=ballad;creature=ash",
    "GEMINI_VOICES": "female=Kore,Aoede;male=Puck,Orus;girl=Leda;boy=Puck;old_woman=Aoede;old_man=Fenrir;creature=Fenrir",
    "SAY_VOICES": "female=Samantha,Karen;male=Daniel,Fred;girl=Kathy;boy=Junior;old_woman=Moira;old_man=Ralph;creature=Zarvox",
    "EDGE_VOICES": ("female=en-US-AvaMultilingualNeural,en-US-EmmaMultilingualNeural;"
                    "male=en-US-BrianMultilingualNeural,en-US-AndrewMultilingualNeural;girl=en-US-AnaNeural;"
                    "boy=en-GB-RyanNeural;old_woman=en-GB-SoniaNeural;old_man=en-US-ChristopherNeural;"
                    "creature=en-US-JasonNeural"),
    # Kokoro character voices: "voice", a blend "a+b", "^pitch" (1.05 = a little higher) — ordered so
    # the first few characters of each kind sound as different from each other as possible.
    "KOKORO_VOICES": ("female=bf_emma,af_bella,af_nicole+bf_isabella^1.03,af_sky^1.04,af_sarah+af_kore^0.97,"
                      "bf_isabella^0.95,af_aoede+bf_lily^1.02,af_jessica,af_river+af_nova^0.98,af_alloy^1.05;"
                      "male=am_michael,bm_george,am_fenrir^0.95,am_puck^1.03,am_liam+am_eric,bm_daniel^0.97,"
                      "am_onyx^0.94,am_adam^1.02;"
                      "girl=af_sky^1.08,af_nova^1.06;boy=am_echo^1.08,am_puck^1.1;old_woman=bf_alice^0.95,bf_isabella^0.92;"
                      "old_man=bm_lewis^0.93,bm_daniel^0.9;creature=am_onyx^0.85,bm_fable^0.9"),

    "BOOK": "on",                          # picture-book PDF in video mode (free: uses the video's frames)
    "LEVEL": "family",                     # default reading level (see --level)
    "ADULT_CONTENT": "off",                # on = allow the Explicit (18+) level (local Ollama only)
    "OLLAMA_ADULT_MODEL": "dolphin3",      # the local model used for Explicit stories
    "OLLAMA_TEMPERATURE": "0.9",           # livelier writing; lower = safer, plainer
    "OLLAMA_KEEP_ALIVE": "30s",            # free the writer's memory soon after, for voices and pictures

    "SOUNDSCAPES": "on",                   # background sound + sound effects that fit each scene
    "SOUNDS_DIR": "~/StoryMaker/sounds",   # your own recordings; downloaded ones are kept in /library
    "FREESOUND_API_KEY": "",               # free key from freesound.org/apiv2/apply: real recorded sounds
    "FREESOUND_URL": "https://freesound.org",
    "AMBIENCE_VOLUME": "1.0",              # background level (0.5 quieter, 1.5 louder)
    "EFFECTS_VOLUME": "1.0",               # sound-effect level
    "MAX_EFFECTS": "3",                    # sound effects per scene at most
    "VOICE_POLISH": "on",                  # studio voice chain: rumble cut, de-ess, gentle compression, clarity
    "MUSIC": "on",
    "MUSIC_DIR": "~/StoryMaker/music",
    "MUSIC_VOLUME": "0.18",

    "NOTIFY": "background",                # background | always | off
    "NTFY_SERVER": "https://ntfy.sh",
    "NTFY_TOPIC": "",
    "NTFY_TOKEN": "",

    "MAX_COST_USD": "10",
    # Approximate prices used for --estimate (US dollars). Edit to match your bills.
    "PRICE_OMNI_PER_SEC": "0.10",
    "PRICE_VEO_PER_SEC": "0.40",
    "PRICE_IMAGE": "0.05",
    "PRICE_STORY_CLAUDE": "0.06",
    "PRICE_STORY_GEMINI": "0.03",
    "PRICE_STORY_OPENAI": "0.02",
    "PRICE_STORY_GROQ": "0",                 # Groq's free tier
    "PRICE_STORY_OLLAMA": "0",               # runs on your own computer
    "PRICE_VOICE_PER_MIN_OPENAI": "0.015",
    "PRICE_VOICE_PER_MIN_GEMINI": "0.02",
    "PRICE_VOICE_PER_MIN_ELEVENLABS": "0",   # comes out of your ElevenLabs plan credits

    "OUTPUT_DIR": "~/StoryMaker/output",
    "PUBLIC_DIR": "",
    "PUBLIC_BASE_URL": "",
    "MEDIA_BASE_URL": "",                  # web page: links to files where the web page serves OUTPUT_DIR
    "SUBTITLES": "on",
    "CAPTIONS": "on",
    "MOTION": "on",
}


def load_settings():
    cfg = dict(DEFAULTS)
    for path in (HERE / "storymaker.env", Path.home() / ".storymaker.env"):
        if path.is_file():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = re.split(r"\s+#", v, maxsplit=1)[0]     # drop end-of-line comments
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in cfg:                                           # real env vars win
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def log(msg):
    print(f"[storymaker {dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def need(cfg, key):
    if not cfg.get(key):
        raise RuntimeError(f"{key} is missing in storymaker.env")
    return cfg[key]


def b64file(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


# ───────────────────────────── reading levels ─────────────────────────────

LEVELS = {
    "young-kids": ("ages 3–5",
                   "Very simple words and short sentences. Gentle, cozy and reassuring; no scary moments, "
                   "no villains who hurt anyone, no peril beyond 'oops'. Repetition and sound words are welcome. "
                   "Every problem is solved with kindness, sharing or teamwork.", 0.6),
    "kids": ("ages 6–8",
             "Simple, lively vocabulary and mostly short sentences. Mild adventure and silly humour are fine; "
             "any danger is brief and never graphic, nobody is badly hurt, nothing truly frightening. "
             "A clear, warm ending.", 0.8),
    "middle-grade": ("ages 9–12",
                     "Rich but accessible vocabulary. Real stakes, brave choices and some suspense are fine; "
                     "no gore, no romance beyond friendship, no strong language. Friendship, courage and growth.", 1.0),
    "teen": ("ages 13–17",
             "Natural, confident prose. Higher stakes, mystery, emotional depth and mild romance are fine; "
             "no graphic violence, no sexual content, no strong profanity.", 1.0),
    "adult": ("adults",
              "Sophisticated literary prose with layered emotion and real tension. Mature themes may be "
              "explored with restraint; no explicit sexual content or gratuitous gore.", 1.1),
    "mature": ("adults (R-rated)",
               "Unflinching adult fiction: strong language, intense violence and dark themes are fine, "
               "and romance and desire can be part of the story, but sex happens off the page (cut away "
               "or fade out). Nothing pornographic.", 1.1),
    "explicit": ("adults only (18+)",
                 "Explicit adult erotic fiction. Sex scenes between consenting adults are written out in full, "
                 "explicitly and in graphic, specific detail, moment by moment; never fade to black, skip ahead or "
                 "summarize them. Strong language is fine. Hard rules that override everything else: every "
                 "character in the story is an adult aged 20 or over, and the story "
                 "says so; nobody is described as childlike; everything sexual is clearly consensual; no "
                 "incest and no animals. Characters' voices are only female, male, old_woman, old_man or "
                 "creature.", 1.1),
    "family": ("all ages, read aloud together",
               "Warm, clear storytelling that works for children and adults at once. Light peril and humour "
               "are fine; nothing frightening for a six-year-old; no strong language.", 1.0),
}


def level_info(name):
    return LEVELS.get((name or "family").lower(), LEVELS["family"])


VOICE_TYPES = ["female", "male", "girl", "boy", "old_woman", "old_man", "creature"]


# ───────────────────────────── the story prompt ─────────────────────────────

STORY_PROMPT = """You are a master storyteller writing a story that will be read aloud by a narrator
(with each character's spoken lines voiced by its own actor) and shown as {visual} — one per scene.

Story idea: {idea}
Genre / tone: {genre}
Reader: {audience}
Reading level and content rules: {rules}
Number of scenes: exactly {scenes}
{series}{keywords}
Write it as a real story with a beginning, a middle with rising tension, and a satisfying ending.
Use vivid, sensory, spoken-word prose that sounds good out loud: varied sentence length, no lists,
no headings inside the narration. Each scene's narration is {words} words.
Let the characters really talk: every scene should have dialogue, with characters speaking to each
other in their own voices. Write the narration like a published storybook, with every spoken line in
double quotation marks and a speech tag, for example: “Wait for me!” Mira called. “We’re nearly
there,” said Old Tom, holding up the lantern.
Keep names, looks and places consistent from scene to scene.

For every scene also write:
- "lines": the scene's narration split, in order, into parts by who speaks them. Narration parts,
  including speech tags like "Mira called", use speaker "Narrator"; a character's spoken words,
  without the quotation marks, use that character's name. Joined together the parts must read as
  the narration.
- "image_prompt": ONE clear moment to illustrate — who is in frame, what they are doing, setting,
  lighting and camera angle. Never ask for text or lettering.
- "video_prompt": an 8-second cinematic shot of that moment — the action, the camera move (slow
  push-in, tracking, crane, etc.), lighting and mood, and the natural ambient sound. Characters do
  not visibly talk; voices are added later. No on-screen text, captions or subtitles.

List every character who speaks in "cast", with "voice" set to exactly one of:
female, male, girl, boy, old_woman, old_man, creature.
Get these right: "female" is an adult woman and "male" an adult man; "girl" and "boy" are only for
children; "old_woman" and "old_man" for the elderly. Each character's type must match the pronouns
you use for them (she/her = female, girl or old_woman; he/him = male, boy or old_man).

Reply with ONLY a JSON object, no markdown fences, in exactly this shape:
{{
  "title": "story title",
  "logline": "one-sentence summary",
  "visual_style": "one look used for every shot, e.g. 'warm Pixar-style 3D animation' or 'moody 35mm film'",
  "characters": "short fixed visual description of each main character (age, hair, clothing, colors)",
  "cast": [{{"name": "Mira", "voice": "girl"}}],
  "scenes": [
    {{"heading": "short scene name",
      "narration": "the full text read aloud for this scene",
      "lines": [{{"speaker": "Narrator", "text": "..."}}, {{"speaker": "Mira", "text": "..."}}],
      "image_prompt": "what to draw", "video_prompt": "the 8-second shot"}}
  ]
}}"""

OUTLINE_PROMPT = """You are a master storyteller planning a story in {chapters} chapters. It will be read
aloud (each character voiced by its own actor) and shown as {visual}.

Story idea: {idea}
Genre / tone: {genre}
Reader: {audience}
Reading level and content rules: {rules}
{series}{keywords}
Plan one complete story across exactly {chapters} chapters. Chapter 1 sets up the world, the characters
and the problem; the middle chapters raise the stakes with turns and setbacks; the last chapter brings
it to a satisfying ending. Each chapter has its own small arc, and every chapter but the last ends
with a pull to keep reading.

List every character who will speak in "cast", with "voice" set to exactly one of:
female, male, girl, boy, old_woman, old_man, creature.
Get these right: "female" is an adult woman and "male" an adult man; "girl" and "boy" are only for
children; "old_woman" and "old_man" for the elderly. Each character's type must match the pronouns
you use for them (she/her = female, girl or old_woman; he/him = male, boy or old_man).

Reply with ONLY a JSON object, no markdown fences, in exactly this shape:
{{
  "title": "story title",
  "logline": "one-sentence summary",
  "visual_style": "one look used for every picture, e.g. 'warm Pixar-style 3D animation'",
  "characters": "short fixed visual description of each main character (age, hair, clothing, colors)",
  "cast": [{{"name": "Mira", "voice": "girl"}}],
  "chapters": [{{"title": "chapter title", "summary": "3–4 sentences: what happens in this chapter",
                 "scenes": ["one sentence: what happens in scene 1", "…exactly {scenes} scenes"]}}]
}}"""

EXPLICIT_SYSTEM = ("You are an uncensored novelist writing for an adults-only fiction service. The reader is an "
                   "adult who asked for explicit erotic fiction. Write sex scenes between consenting adults "
                   "explicitly and in graphic, specific detail: bodies, touch, what each person does, positions, "
                   "sensations, sounds and what they say, at the same unhurried pace as the rest of the story. "
                   "Never fade to black, skip ahead, summarize, moralize, add warnings or refuse. Limits that "
                   "always apply: every character is an adult aged 20 or over, everything is consensual, no "
                   "incest, no animals. Keep the whole story in the characters' adult lives: no children or "
                   "teenagers anywhere in it, no flashbacks or memories of anyone's childhood, school days or growing up, no "
                   "ages under 20 for anyone or anything, and call adults "
                   "\"women\" and \"men\", not \"girls\" or \"boys\"." +
                   " Never use these words at all, not even about adults or as a figure of speech: child, children, "
                   "childlike, childish, childhood, kid, kids, teen, teenager, teenage, girlish, boyish, young girl, "
                   "little girl, schoolgirl, high school, underage, minor, daughter, son, baby (except as a pet "
                   "name), puberty. Write about the characters only as the adults they are now.")

SCENE_PROMPT = """You are writing one scene of the story "{title}". Genre / tone: {genre}. Reader: {audience}.
Content rules: {rules}
Characters: {characters}

The story plan, chapter by chapter:
{plan}

This is chapter {n} of {total}, "{chapter_title}": {summary}
This is scene {k} of {scenes} in this chapter. What happens in this scene: {beat}
{previous}{keywords}
Write this scene now as finished story prose of about {words} words. Make it long, rich and detailed:
the setting and the senses, what the characters think and feel, and plenty of dialogue between them.
{extra}Put every spoken line in double quotation marks with a speech tag that names who is speaking,
like: “Wait for me,” Mira said. Start a new paragraph whenever the speaker changes.
Write only the scene itself: no title, no heading, no notes, nothing before or after the story text.
Stop when this scene's events are done; don't end the chapter or the story early."""

CONTINUE_PROMPT = """Here is a scene from the story "{title}" ({genre}, for {audience}). Content rules: {rules}

{text}

Continue this same scene from exactly where it stops. Add about {more} more words of detailed prose
and dialogue (spoken lines in double quotation marks with a speech tag). {extra}Write only the
continuation, with no heading or notes, and don't repeat what is already written."""

CHAPTER_PROMPT = """You are a master storyteller writing chapter {n} of {total} of the story "{title}".
It will be read aloud by a narrator (each character's spoken lines voiced by its own actor) and shown
as {visual}, one per scene.

Reader: {audience}
Reading level and content rules: {rules}
Genre / tone: {genre}
Characters (keep their names, looks and personalities): {characters}
Who speaks, and their voice type: {cast}

The whole story, chapter by chapter:
{plan}

How the previous chapter ended: {previous}

Now write chapter {n}, "{chapter_title}": {summary}
{keywords}
Write it as exactly {scenes} scenes that flow on from each other, each scene's narration {words} words.
Use vivid, sensory, spoken-word prose that sounds good out loud, with varied sentence length.
Let the characters really talk: every scene should have dialogue, with characters speaking to each
other in their own voices. Write the narration like a published novel, with every spoken line in
double quotation marks and a speech tag, for example: “Wait for me!” Mira called. “We’re nearly
there,” said Old Tom, holding up the lantern. Don't repeat the chapter title inside the narration.

For every scene also write:
- "lines": the scene's narration split, in order, into parts by who speaks them. Narration parts,
  including speech tags like "Mira called", use speaker "Narrator"; a character's spoken words,
  without the quotation marks, use that character's name. Joined together the parts must read as
  the narration.
- "image_prompt": ONE clear moment to illustrate — who is in frame, what they are doing, setting,
  lighting and camera angle. Never ask for text or lettering.
- "video_prompt": an 8-second cinematic shot of that moment, with the camera move, lighting, mood
  and natural ambient sound. Characters do not visibly talk. No on-screen text.

If someone new speaks in this chapter, add them to "new_cast" with a voice type from the list above.

Reply with ONLY a JSON object, no markdown fences, in exactly this shape:
{{
  "scenes": [
    {{"heading": "short scene name",
      "narration": "the full text read aloud for this scene",
      "lines": [{{"speaker": "Narrator", "text": "..."}}, {{"speaker": "Mira", "text": "..."}}],
      "image_prompt": "what to draw", "video_prompt": "the 8-second shot"}}
  ],
  "new_cast": []
}}"""

SERIES_BLOCK = """
This is episode {n} of the series "{name}". Keep the recurring characters, their looks, names and
personalities, and this exact visual style: {style}
Recurring characters: {characters}
Who they are (fixed, use exactly these in "cast"): {cast}
The story so far:
{previously}
Write a NEW adventure that follows on from the last episode (don't retell or repeat earlier plots).
You may add new characters; keep the recurring ones in "characters" and "cast" unchanged.
"""


def extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("The AI reply did not contain JSON:\n" + text[:500])
    return json.loads(text[start:end + 1])


def ask_writer(cfg, prompt, as_json=True, system=None):
    """Send one prompt to the chosen writer. Returns its JSON reply (one retry if malformed), or with
    as_json=False its plain text. `system` is a standing instruction for the writer."""
    provider = cfg["STORY_PROVIDER"].lower()
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    for attempt in range(2):
        if provider == "claude":
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": need(cfg, "ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": cfg["CLAUDE_MODEL"], "max_tokens": 12000, **({"system": system} if system else {}),
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=300)
            if r.status_code != 200:
                raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:500]}")
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        elif provider == "gemini":
            r = requests.post(
                f"{GEMINI_BASE}/models/{cfg['GEMINI_TEXT_MODEL']}:generateContent",
                headers={"x-goog-api-key": need(cfg, "GEMINI_API_KEY"), "content-type": "application/json"},
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      **({"systemInstruction": {"parts": [{"text": system}]}} if system else {}),
                      "generationConfig": {"responseMimeType": "application/json"} if as_json else {}},
                timeout=300)
            if r.status_code != 200:
                raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:500]}")
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        elif provider == "openai":
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {need(cfg, 'OPENAI_API_KEY')}", "content-type": "application/json"},
                json={"model": cfg["OPENAI_TEXT_MODEL"], "messages": msgs,
                      **({"response_format": {"type": "json_object"}} if as_json else {})},
                timeout=300)
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI API error {r.status_code}: {r.text[:500]}")
            text = r.json()["choices"][0]["message"]["content"]
        elif provider == "groq":
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {need(cfg, 'GROQ_API_KEY')}", "content-type": "application/json"},
                json={"model": cfg["GROQ_MODEL"], "max_tokens": 8000, "messages": msgs,
                      **({"response_format": {"type": "json_object"}} if as_json else {})},
                timeout=300)
            if r.status_code != 200:
                raise RuntimeError(f"Groq API error {r.status_code}: {r.text[:500]}")
            text = r.json()["choices"][0]["message"]["content"]
        elif provider == "ollama":
            try:
                r = requests.post(f"{cfg['OLLAMA_URL'].rstrip('/')}/api/chat",
                                  json={"model": cfg["OLLAMA_MODEL"], "stream": False,
                                        **({"format": "json"} if as_json else {}),
                                        "keep_alive": cfg.get("OLLAMA_KEEP_ALIVE", "30s"),
                                        "options": {"num_ctx": 12288, "num_predict": 8000,
                                                    **({} if as_json else {
                                                        "temperature": float(cfg.get("OLLAMA_TEMPERATURE") or 0.9),
                                                        "top_p": 0.95, "repeat_penalty": 1.12, "repeat_last_n": 256})},
                                        "messages": msgs},
                                  timeout=1800)
            except requests.ConnectionError:
                raise RuntimeError(f"Can't reach Ollama at {cfg['OLLAMA_URL']}. Is the Ollama app running?")
            if r.status_code != 200:
                raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:300]} "
                                   f"(try: ollama pull {cfg['OLLAMA_MODEL']})")
            text = r.json()["message"]["content"]
        else:
            raise RuntimeError(f"Unknown STORY_PROVIDER '{provider}' (use claude, gemini, openai, groq, ollama or demo)")
        if not as_json:
            return text.strip()
        try:
            return extract_json(text)
        except ValueError as e:
            if attempt:
                raise ValueError(f"The writer's reply wasn't usable story data, twice. Try again, or use fewer scenes. ({str(e)[:200]})")
            log("The writer's reply was malformed; asking once more…")


DETAIL = {"brief": 0.6, "standard": 1.0, "rich": 1.8, "epic": 2.6}


# ── how it's told (every level) and, for Explicit, how hot ──
HEAT = {
    1: "Heat 1 of 5, sensual: desire, touch and tension are vivid; the most intimate moments are written with restraint.",
    2: "Heat 2 of 5, steamy: sex scenes are on the page with clear, sensual detail.",
    3: "Heat 3 of 5, explicit: sex scenes are fully on the page, explicit and detailed.",
    4: ("Heat 4 of 5, very explicit: sex scenes are long, graphic and anatomically specific, written moment by "
        "moment, with what each person does, feels and says."),
    5: ("Heat 5 of 5, maximum: unrestrained, graphic erotica. Sex is at the heart of the story and every scene "
        "is written out in full, graphic, anatomically explicit detail, at length, moment by moment."),
}
PACING = {
    "slow": "Slow burn: build longing and tension for a long time before anything happens; the payoff comes late and is worth it.",
    "steady": "Steady: build some tension, then let things happen, and keep the heat rising through the story.",
    "fast": "Straight to it: the attraction is immediate, the story gets to sex early, and it happens often.",
}
LANGUAGE = {
    "tasteful": "Use elegant, literary language for bodies and sex.",
    "frank": "Use frank, direct language for bodies and sex; nothing coy or vague.",
    "crude": "Use raw, crude, dirty language for bodies and sex, in the narration and in what the characters say.",
}
POV = {
    "first": "Tell the story in the first person (\"I\"), from the main character's point of view.",
    "third": "Tell the story in the third person.",
}
BLOCKED_THEMES = re.compile(r"\b(incest\w*|bestial\w*|zoophil\w*|animals?|rape|raping|non-?con\w*|forced|drugged|"
                            r"unconscious|asleep|sleeping|blackmail\w*)\b", re.I)


def style_rules(prefs, level):
    """Extra rules from the make form: point of view for everyone; heat, pacing, language, turn-ons
    and limits for Explicit."""
    prefs = prefs or {}
    out = []
    if prefs.get("pov") in POV:
        out.append(POV[prefs["pov"]])
    if level == "explicit":
        heat = int(prefs.get("heat") or 4)
        out.append(HEAT.get(max(1, min(5, heat)), HEAT[4]))
        if prefs.get("pacing") in PACING:
            out.append(PACING[prefs["pacing"]])
        if prefs.get("language") in LANGUAGE:
            out.append(LANGUAGE[prefs["language"]])
        if heat >= 3:
            out.append("Give intimate scenes plenty of room: slow down and write them at length, with the build-up, "
                       "the act itself in full, and the moments after.")
        if prefs.get("turn_ons"):
            out.append(f"The reader's turn-ons, to include (always between consenting adults): {prefs['turn_ons']}.")
        if prefs.get("limits"):
            out.append(f"Hard limits, never include any of these: {prefs['limits']}.")
    return (" " + " ".join(out)) if out else ""


def check_prefs(prefs):
    """Turn-ons must stay between consenting adults."""
    text = " ".join(str((prefs or {}).get(k) or "") for k in ("turn_ons",))
    check_adult_only(text)
    m = BLOCKED_THEMES.search(text)
    if m:
        raise ValueError(f"Explicit stories are only ever between consenting adults, so “{m.group(0)}” can't be a "
                         "turn-on. Nothing was made.")


def keyword_list(text):
    """'velvet, thunder; "I'm yours"' → ['velvet', 'thunder', "I'm yours"] (max 30)."""
    out = []
    for k in re.split(r"[,;\n]+", text or ""):
        k = k.strip().strip('"“”').strip()[:60]
        if k and k.lower() not in (x.lower() for x in out):
            out.append(k)
    return out[:30]


def keyword_used(text, k):
    return re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", (text or "").lower()) is not None


def keywords_block(kws, written="", last=False, whole=False):
    """The instruction that gets the listener's words into the story."""
    if not kws:
        return ""
    if whole:
        return ("\nThe listener asked for these words and phrases. Use EVERY one of them at least once, "
                f"naturally, in the narration or the dialogue: {', '.join(kws)}\n")
    missing = [k for k in kws if not keyword_used(written, k)]
    line = f"\nThe listener asked for these words and phrases in the story: {', '.join(kws)}."
    if missing and last:
        line += f" This is the last scene, so it MUST naturally include every one still missing: {', '.join(missing)}."
    elif missing:
        line += f" Not used yet: {', '.join(missing)}. Work in the ones that fit this scene, naturally."
    else:
        line += " All have been used; use them again only where they fit."
    return line + "\n"


# ── your style: how you want stories written, and how pictures, video and sounds should feel (from the web page) ──
GROWN_UP = ("mature", "explicit")


def style_from_env():
    try:
        return json.loads(os.environ.get("STORYMAKER_STYLE") or "{}")
    except ValueError:
        return {}


def _clip(text, n):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:n] + ("…" if len(text) > n else "")


KIDS_LEVELS = ("young-kids", "kids", "middle-grade", "family")
PROFILE_RANK = {"kids": 0, "everyday": 1, "grown": 2}
# what never goes into a gentler story's style guide: swearing, crude words, anything sexual or suggestive
CRUDE = re.compile(
    r"\b(fuck\w*|shit\w*|damn\w*|goddamn\w*|hell|bitch\w*|bastard\w*|asshole\w*|ass|arse\w*|crap\w*|piss\w*|"
    r"cunt\w*|dick\w*|cock\w*|pussy|pussies|tits?|titties|boob\w*|cum\w*|sex\w*|naked|nude\w*|nipple\w*|"
    r"breasts?|orgasm\w*|moan\w*|thrust\w*|erect\w*|clit\w*|genital\w*|penis\w*|vagina\w*|horny|arous\w*|"
    r"lust\w*|seduc\w*|sensual\w*|erotic\w*|kinky|kink|foreplay|lick\w*|suck\w*|wet|hard-on|aroused|"
    r"bed(?:room)? scene|explicit|steamy|sultry|naughty|filth\w*|dirty|raunch\w*|smut\w*|teas\w*|kiss\w*|"
    r"grind\w*|straddl\w*|undress\w*|strip\w*|panties|bra|lingerie|bdsm|spank\w*)\b", re.I)


def profile_for(level):
    return "grown" if level in GROWN_UP else "kids" if level in KIDS_LEVELS else "everyday"


def soften(text):
    """Keep the words, phrasing and tone, without anything crude, sexual or suggestive."""
    parts = re.split(r"(?<=[.!?…;])\s+|\n+|,\s*", str(text or ""))
    return ", ".join(p.strip() for p in parts if p.strip() and not CRUDE.search(p)) if CRUDE.search(text or "") \
        else str(text or "")


def style_block(style, level):
    """The reader's own writing style, for every writing prompt: their notes, what they said about earlier stories,
    and passages to match. Each style is marked for kids & family, teens & adults, or grown-up stories. A style can
    also be shared with the other kinds; used for a gentler story than it was made for, it's made gentler: the same
    voice and words, but nothing crude, sexual or suggestive, and no swearing (and for kids, no passages at all)."""
    profiles = ((style or {}).get("writing") or {})
    own = profile_for(level)
    adult_only = level == "explicit"
    ok = lambda t: not (adult_only and (young_age(t) or MINOR_WORDS.search(t)))    # nothing young, ever
    lines, refs, softened = [], [], False
    order = [own] + [k for k in ("grown", "everyday", "kids") if k != own]
    for name in order:
        w = profiles.get(name) or {}
        if name != own and not w.get("share", True):
            continue
        gentler = PROFILE_RANK.get(name, 1) > PROFILE_RANK[own]            # made for stronger stories than this one
        fix = soften if gentler else (lambda t: t)
        tag = "" if name == own else " (from your style for other stories)"
        tone, use = fix(w.get("tone") or ""), fix(w.get("use") or "")
        if tone.strip() and ok(tone):
            lines.append(f"- Tone, voice and feel{tag}: {_clip(tone, 600)}")
        if use.strip() and ok(use):
            lines.append(f"- Words, phrasing and things the reader wants used{tag}: {_clip(use, 500)}")
        if w.get("avoid"):
            lines.append(f"- Never use or do: {_clip(w['avoid'], 400)}")
        for f in (w.get("feedback") or [])[-(10 if name == own else 4):]:
            note = fix(f.get("notes") or "")
            if note.strip() and ok(note):
                how = {"up": "liked", "down": "didn't like"}.get(f.get("rating"), "said")
                about = f" ({_clip(f.get('title'), 60)})" if f.get("title") and name == own else ""
                lines.append(f"- About an earlier story{about}, the reader {how}: {_clip(note, 300)}")
        if not (gentler and own == "kids"):                                    # kids never get grown-up passages
            for r in (w.get("references") or [])[-(3 if name == own else 1):]:
                r2 = fix(r)
                if r2.strip() and len(r2.split()) > 12 and ok(r2):
                    refs.append(r2)
        softened = softened or (gentler and bool(tone.strip() or use.strip() or refs))
    refs = refs[:3]
    if not lines and not refs:
        return ""
    out = "\nThe reader's own style guide (follow it closely; it comes before your usual habits):\n" + "\n".join(lines)
    if softened:
        out += ("\n- Some of this style was made for other kinds of stories. Use its voice, rhythm, phrasing and "
                "words, but keep this story right for its reader: no swearing, nothing crude, nothing sexual or "
                "suggestive; where the style would go there, keep it clean and warm instead.")
    if refs:
        out += ("\nPassages the reader loves. Match their voice, rhythm, sentence length, vocabulary, dialogue and "
                "way of describing things, but never copy their events, names or sentences:\n" +
                "\n".join(f'"""{_clip(r, 1200)}"""' for r in refs))
    return out + "\n"


def apply_media_style(cfg, story):
    """Your picture and video style goes with the story, so remakes use it too."""
    st = cfg.get("_STYLE") or {}
    pics, vid = (st.get("pictures") or {}), (st.get("video") or {})
    keep = {"pictures": _clip(pics.get("style"), 300), "pictures_avoid": _clip(pics.get("avoid"), 200),
            "video": _clip(vid.get("style"), 300)}
    if any(keep.values()):
        story["user_style"] = keep


def write_story(cfg, idea, genre, level, scenes, clip_mode, series=None, chapters=1, job=None, detail="standard"):
    provider = cfg["STORY_PROVIDER"].lower()
    audience, rules, length = level_info(level)
    rules += style_rules(cfg.get("_PREFS"), level)
    rules += style_block(cfg.get("_STYLE"), level)
    length *= DETAIL.get(detail, 1.0)
    lo, hi = (20, 28) if clip_mode else (120, 200) if chapters > 1 else (60, 110)   # clips need short narration
    if not clip_mode:
        lo, hi = int(lo * length), int(hi * length)
    series_text = ""
    if series:
        prev = "\n".join(f"- Episode {i + 1}: {e['title']} — {e['logline']}" for i, e in enumerate(done_episodes(series)))
        fixed = ", ".join(f"{c['name']} ({TYPE_WORDS.get(c.get('voice'), c.get('voice', ''))})"
                          for c in series.get("cast") or [] if c.get("name")) or "as before"
        series_text = SERIES_BLOCK.format(n=len(done_episodes(series)) + 1, name=series["name"],
                                          style=series["visual_style"], characters=series["characters"],
                                          previously=prev, cast=fixed)
    kws = cfg.get("_KEYWORDS") or []
    prompt = STORY_PROMPT.format(
        idea=idea or "Continue the story with a fresh new adventure.", genre=genre, audience=audience,
        rules=rules, scenes=scenes, series=series_text, words=f"{lo}–{hi}", keywords=keywords_block(kws, whole=True),
        visual="short cinematic video clips" if clip_mode else "illustrations")
    if not clip_mode and (chapters > 1 or detail in ("rich", "epic")):
        lo, hi = int(120 * length), int(200 * length)
        log(f"Writing a {chapters}-chapter story ({scenes} scenes each, about {(lo + hi) // 2} words per scene) "
            f"for {audience} with {provider}, one scene at a time…")
        story = write_prose_story(cfg, idea, genre, audience, rules, scenes, series_text, chapters,
                                  f"{lo}–{hi}", job, level)
    elif chapters > 1:
        log(f"Writing a {chapters}-chapter story ({scenes} scenes each) for {audience} with {provider}…")
        story = write_chapters(cfg, idea, genre, audience, rules, scenes, clip_mode, series_text,
                               chapters, f"{lo}–{hi}", job)
    elif provider == "demo":
        log(f"Writing a {scenes}-scene story for {audience} with {provider}…")
        time.sleep(float(os.environ.get("STORYMAKER_DEMO_DELAY", 0)))     # testing the progress display
        story = demo_story(idea or "the next adventure", scenes)
    else:
        log(f"Writing a {scenes}-scene story for {audience} with {provider}…")
        story = ask_writer(cfg, prompt)
    if not story.get("scenes"):
        raise ValueError("The AI reply had no scenes.")
    if series:                                  # the look must not drift between episodes
        story["visual_style"] = series["visual_style"]
        if series["characters"] and series["characters"][:40] not in story.get("characters", ""):
            story["characters"] = series["characters"] + "; " + story.get("characters", "")
    quote_dialogue(story)
    declared = {str(c.get("name", "")).lower() for c in story.get("cast") or [] if isinstance(c, dict)
                and c.get("voice") in VOICE_TYPES}
    if series:
        apply_locked_cast(story, series.get("cast"))
    complete_cast(story)
    if level == "explicit":
        for sc in story.get("scenes", []):
            if MINOR_HARD.search(sc.get("narration", "")) or youth_mention(sc.get("narration", "")):
                sc["narration"] = adult_guard(cfg, sc["narration"], EXPLICIT_SYSTEM)
                sc["lines"] = lines_from_prose(sc["narration"], story.get("cast") or [])
        story.update({k: adult_tidy(v) for k, v in story.items() if k != "scenes"})
        for sc in story.get("scenes", []):
            for k in ("heading", "image_prompt", "video_prompt", "chapter_title"):
                if isinstance(sc.get(k), str):
                    sc[k] = adult_tidy(sc[k])
        for c in story.get("cast", []):
            c["voice"] = {"girl": "female", "boy": "male"}.get(c.get("voice"), c.get("voice"))
    lock_written_cast(story, declared)
    story["level"] = level
    return story


class MinorContent(ValueError):
    """Explicit content that could involve someone under 20: the job stops and its unfinished work is deleted."""


class SceneRedo(MinorContent):
    """A scene the writer made with a word that only ever means a minor: the whole scene is thrown away and
    written again from scratch (nothing of it is kept)."""


_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_AGE_PATTERNS = [
    re.compile(r"\b(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s*[- ]?\s*(?:years?|yrs?)\s*[- ]?\s*old\b", re.I),
    re.compile(r"\b(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\s*(?:years?|yrs?)\s+of\s+age\b", re.I),
    re.compile(r"\b(\d{1,2})\s*y\.?o\.?\b", re.I),
    re.compile(r"\bage[ds]?\s*(?:of\s*|:\s*|was\s*|is\s*)?(\d{1,2}|" + "|".join(_NUM_WORDS) + r")\b", re.I),
    re.compile(r"\b[A-Z][\w'-]+\s*[,(]\s*(\d{1,2})\s*[,)]"),                     # "Mira, 19," / "Jake (17)"
    re.compile(r"\bturn(?:s|ed|ing)?\s+(\d{1,2}|" + "|".join(_NUM_WORDS) + r")(?=\s*(?:[.,;!?)]|$|years?\b|"
               r"this\b|last\b|next\b|today\b|on\b|in\b|that\b|when\b|and\b))", re.I),
]


def young_age(text):
    """Any age under 20 mentioned in the text ("19-year-old", "aged 18", "Mira, 17,"), or None."""
    for pat in _AGE_PATTERNS:
        for m in pat.finditer(text or ""):
            v = m.group(1).lower()
            n = int(v) if v.isdigit() else _NUM_WORDS.get(v, 99)
            if n < 20:
                return m.group(0)
    return None


# Words that always stop an explicit story (an age or description that means a minor)…
MINOR_HARD = re.compile(r"\b(underage|under-age|preteens?|pre-teens?|loli|lolita|shota)\b", re.I)
# …and everything else that mentions youth: flashbacks ("when she was three years old", "her childhood"),
# backstory ("since high school", "her kids", "the young girl"). In the writer's text these are taken out
# (rewritten, or as a last resort the sentences removed) so they never end up in the story, and the story
# carries on. In what YOU type, any of them stops the story before anything is written.
MINOR_SOFT = re.compile(
    r"\b(child|children|childhood|kids?|teen|teens|teenager|teenagers|teenaged|teenage|high[- ]school|middle[- ]school|"
    r"elementary|young (girl|boy)s?|little (girl|boy)s?|daughters?|sons?|toddlers?|infants?|"
    r"schoolgirls?|schoolboys?|growing up|grew up|as a girl|as a boy|minors?|puberty|adolescen\w*|childlike|"
    r"child-like|childish|girlish|boyish|innocent little|tiny little body)\b", re.I)


def youth_mention(text):
    """Anything in the writer's text about someone being young: a word above, or an age under 20."""
    m = MINOR_SOFT.search(text or "")
    return m.group(0) if m else young_age(text)
ADULT_REDO = ("\nIMPORTANT: every person in this scene is an adult, 20 or older, and the scene stays in their adult "
              "present: no memories, no childhood, nobody young.")
ADULT_SWAPS = [(r"\bhigh[- ]school\b", "college"), (r"\bmiddle[- ]school\b", "college"), (r"\belementary\b", "early"),
               (r"\bteenagers\b", "young adults"), (r"\bteenager\b", "young adult"), (r"\bteen(s)?\b", "young adult\\1"),
               (r"\bteenage(d)?\b", "young"), (r"\byoung girl(s)?\b", "young woman"), (r"\byoung boy(s)?\b", "young man")]

CLEAN_PROMPT = """Rewrite this passage of an adults-only story. Every character in it is an adult (20 or older).
Take out every flashback or memory of anyone's childhood or school years, every age under 20, and every
mention of children, kids, babies, teenagers or growing up; keep the scene in the characters' adult present.
Say "young woman" or "young man", never "young girl" or "young boy". Keep everything else exactly as it is:
the events, the length, the detail, the dialogue and how explicit it is.
Reply with only the rewritten passage.

---
{text}
---"""


def drop_youth_sentences(text):
    """Last resort: remove every sentence that mentions youth, keeping the paragraphs."""
    paras = []
    for para in re.split(r"\n\s*\n", text or ""):
        keep = [x for x in re.split(r"(?<=[.!?…”])\s+", para.strip()) if x and not youth_mention(x)]
        if keep:
            paras.append(" ".join(keep))
    return "\n\n".join(paras)


def adult_guard(cfg, text, system=None):
    """For text the writer made for an explicit story. Words that only mean a minor (underage, preteen…) stop the
    story at once and its unfinished work is deleted. Anything else about youth (a flashback to childhood, an age
    under 20, "since high school") is taken out: the writer rewrites the passage without it, and if it keeps
    putting it back, those sentences are removed. Either way none of it is ever in the story."""
    for attempt in range(3):
        m = MINOR_HARD.search(text or "")
        if m:
            raise SceneRedo(f"Stopped: explicit stories are for adults only (20 and over), and the writer kept "
                            f"writing “{m.group(0)}”. The unfinished story was deleted. Try again, or change the idea.")
        hit = youth_mention(text)
        if not hit:
            return text
        if attempt == 2 or cfg.get("STORY_PROVIDER", "").lower() == "demo":
            break
        log(f"Taking “{hit}” out of an explicit scene (no flashbacks, everyone stays an adult)…")
        new = clean_prose(ask_writer(cfg, CLEAN_PROMPT.format(text=text), as_json=False, system=system))
        if len(new.split()) > 0.6 * len(text.split()):
            text = new
    trimmed = drop_youth_sentences(text)
    if trimmed and not youth_mention(trimmed) and not MINOR_HARD.search(trimmed) \
            and len(trimmed.split()) >= 0.5 * len(text.split()):
        log("Removed the sentences about youth the writer kept adding.")
        return trimmed
    raise MinorContent("Stopped: the writer kept writing about someone young in an explicit scene, so the "
                       "unfinished story was deleted. Try again; if it keeps happening, reword the idea so it "
                       "stays in the characters' adult lives.")


def adult_tidy(value):
    """Plans, character notes and picture prompts of an explicit story: words that mean a minor stop it; backstory
    words are swapped for adult ones (the scenes themselves are checked with adult_guard)."""
    if isinstance(value, dict):
        return {k: adult_tidy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [adult_tidy(v) for v in value]
    if not isinstance(value, str):
        return value
    m = MINOR_HARD.search(value)
    if m:
        raise SceneRedo(f"Stopped: explicit stories are for adults only (20 and over), and the writer's plan "
                           f"mentions “{m.group(0)}”. The unfinished story was deleted. Try again, or change the idea.")
    for pat, rep_ in ADULT_SWAPS:
        value = re.sub(pat, rep_, value, flags=re.I)
    if youth_mention(value):                     # what's left (childhood, ages, kids): drop those sentences
        value = " ".join(x for x in re.split(r"(?<=[.!?])\s+", value) if not youth_mention(x)).strip()
    return value


MINOR_WORDS = re.compile(
    r"\b(child|children|childlike|kids?|minors?|underage|under-age|teen|teens|teenager|teenagers|teenaged|"
    r"teenage|schoolgirls?|schoolboys?|high[- ]school|middle[- ]school|elementary|preteens?|pre-teens?|"
    r"loli|lolita|shota|little (girl|boy)s?|young (girl|boy)s?|daughters?|toddlers?|infants?|"
    r"(1[0-7]|[1-9])[- ]?(year|yr)s?[- ]old)\b", re.I)


def check_adult_only(text, where="what you typed"):
    """Explicit stories are for adult characters only. Stop before anything is written if the text gives an age
    under 20 or suggests a minor."""
    age = young_age(text)
    if age:
        raise MinorContent(f"Explicit stories are for adults only (20 and over), and {where} mentions “{age}”. "
                           "Nothing was made. Make every character 20 or older, or use another level.")
    m = MINOR_WORDS.search(text or "")
    if m:
        raise ValueError(f"Explicit stories are for adult characters only, and {where} mentions “{m.group(0)}”. "
                         "Nothing was made. Reword that part (for example “college” for “high school”, "
                         "“young woman” for “young girl”), or use another level.")


QUOTE_CHARS = ('"', "“", "”")


def guess_gender(name, text, others=(), counts=False):
    """'she' or 'he' from the pronouns that follow the character's name, up to the end of the
    sentence or the next character's name (so someone else's 'he' isn't counted)."""
    she = he = 0
    stop = re.compile(r"[.!?…]|\b(" + "|".join(re.escape(o.split()[0]) for o in others if o != name) + r")\b"
                      if others else r"[.!?…]")
    for m in re.finditer(r"\b" + re.escape(name.split()[0]) + r"\b", text):
        window = text[m.end():m.end() + 140]
        cut = stop.search(window)
        window = (window[:cut.start()] if cut else window).lower()
        she += len(re.findall(r"\b(she|her|hers|herself)\b", window))
        he += len(re.findall(r"\b(he|him|his|himself)\b", window))
    if counts:
        return she, he
    if name.lower() in ("woman", "girl", "lady", "mother", "mom", "grandma", "queen", "aunt"):
        return "she"
    if name.lower() in ("man", "boy", "father", "dad", "grandpa", "king", "uncle"):
        return "he"
    return "she" if she > he else "he" if he > she else ""


TYPE_WORDS = {"female": "a woman", "male": "a man", "girl": "a girl", "boy": "a boy", "old_woman": "an older woman",
              "old_man": "an older man", "creature": "a creature"}


WORD_TYPES = [  # words in a Characters list → voice type (longest first)
    (r"older woman|old woman|elderly woman|grandmother|grandma|old lady", "old_woman"),
    (r"older man|old man|elderly man|grandfather|grandpa", "old_man"),
    (r"girl", "girl"), (r"boy", "boy"),
    (r"woman|lady|female|she|her|wife|mother|queen|princess", "female"),
    (r"man|male|gentleman|he|him|husband|father|king|prince", "male"),
    (r"creature|animal|robot|dragon|monster|beast|spirit|alien", "creature"),
]
CHAR_LINE = re.compile(r"^\s*(?:[-*•]\s+)?\**([^:—–(\n*]{1,60}?)\**\s*(?::|—|–|\s-\s|\()\s*(.{1,120}?)\)?\s*$")


def char_type(words):
    """'a woman' / 'Woman, 30' / 'girl (8)' / 'F' → voice type, or None."""
    w = (words or "").strip().lower()
    if w in ("f", "w"):
        return "female"
    if w == "m":
        return "male"
    for pat, ty in WORD_TYPES:
        if re.search(r"\b(?:" + pat + r")\b", w):
            return ty
    return None


def parse_characters(text):
    """'Mira: a woman' lines (one per character) → [(name, type)]. Lines it can't read are skipped."""
    out = []
    for line in (text or "").splitlines():
        m = CHAR_LINE.match(line)
        if not m:
            continue
        name, ty = m.group(1).strip().strip("*").strip(), char_type(m.group(2))
        if name and ty and name.lower() not in ("characters", "cast", "narrator"):
            out.append((name[:60], ty))
    return out


def characters_lines(story):
    return [f"- **{c['name']}**: {TYPE_WORDS.get(c.get('voice'), 'a woman')}" for c in story.get("cast") or []
            if c.get("name")]


def lock_written_cast(story, declared):
    """The writer's cast list is written at the top of the story and locked, so the voices and the Audio Studio
    follow it. Characters the writer didn't list are locked only when the text clearly shows who they are."""
    text = " ".join(sc.get("narration", "") for sc in story.get("scenes", []))
    names = [c["name"] for c in story.get("cast") or []]
    for c in story.get("cast") or []:
        if c.get("confirmed"):
            continue
        if c["name"].lower() in declared:
            c["confirmed"] = True
            continue
        she, he = guess_gender(c["name"], text, names, counts=True)
        if (she >= 2 and she >= 2 * he) or (he >= 2 and he >= 2 * she):
            c["confirmed"] = True


def apply_locked_cast(story, locked):
    """Characters you tagged (or a series' tags) keep their type, whatever the writer said."""
    by_name = {c["name"].lower(): c for c in locked or [] if c.get("name") and c.get("confirmed")}
    for c in story.get("cast") or []:
        lk = by_name.get(str(c.get("name", "")).lower())
        if lk:
            c["voice"], c["confirmed"] = lk["voice"], True
    known = {str(c.get("name", "")).lower() for c in story.get("cast") or []}
    story["cast"] = (story.get("cast") or []) + [dict(c) for n, c in by_name.items() if n not in known]


def complete_cast(story):
    """Every speaker gets a cast entry with a voice of the right gender: characters the writer forgot
    to list are added, and a voice type that clearly contradicts the story's pronouns is corrected."""
    text = " ".join(sc.get("narration", "") for sc in story.get("scenes", []))
    cast = [c for c in story.get("cast") or [] if isinstance(c, dict) and c.get("name")]
    known = {c["name"].lower(): c for c in cast}
    speakers = []
    for sc in story.get("scenes", []):
        for ln in sc.get("lines") or []:
            who = str(ln.get("speaker", "")).strip() if isinstance(ln, dict) else ""
            if who and who.lower() != "narrator" and who.lower() not in known and who not in speakers:
                speakers.append(who)
    names = [c["name"] for c in cast] + speakers
    for who in speakers:
        g = guess_gender(who, text, names)
        c = {"name": who, "voice": "female" if g == "she" else "male" if g == "he" else "female"}
        cast.append(c)
        known[who.lower()] = c
    for c in cast:
        if c.get("confirmed"):
            continue                                   # tagged by you: never changed
        she, he = guess_gender(c["name"], text, names, counts=True)
        female = c.get("voice") in ("female", "girl", "old_woman")
        male = c.get("voice") in ("male", "boy", "old_man")
        if c.get("voice") not in VOICE_TYPES:
            c["voice"] = "female" if she >= he else "male"
        elif female and he >= 3 and he >= 3 * she:     # only overrule the writer on clear evidence
            c["voice"] = {"girl": "boy", "old_woman": "old_man"}.get(c["voice"], "male")
        elif male and she >= 3 and she >= 3 * he:
            c["voice"] = {"boy": "girl", "old_man": "old_woman"}.get(c["voice"], "female")
    story["cast"] = cast


def quote_dialogue(story):
    """Make sure spoken lines read as dialogue in quotation marks, like a storybook.
    If the writer left the quotes out of a scene's narration, rebuild it from the speaker lines:
    “Did you hear that?” Mira asked.  (The speech tag is added to the narrator's part too,
    so the audio, captions and text all match.)"""
    for sc in story.get("scenes", []):
        lines = sc.get("lines")
        if not isinstance(lines, list) or not lines:
            continue
        sc["narration"] = normalize_quotes(sc.get("narration", ""))
        talkers = [l for l in lines if isinstance(l, dict)
                   and str(l.get("speaker", "")).strip().lower() not in ("", "narrator")]
        if not talkers or any(q in sc.get("narration", "") for q in QUOTE_CHARS):
            continue
        new_lines, text = [], []
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            who = str(ln.get("speaker", "")).strip()
            said = str(ln.get("text", "")).strip()
            if not said:
                continue
            if who.lower() in ("", "narrator"):
                new_lines.append({"speaker": "Narrator", "text": said})
                text.append(said)
                continue
            said = said.strip('"“”')
            verb = "asked" if said.endswith("?") else "called" if said.endswith("!") else "said"
            tag = f"{who} {verb}."
            shown = said[:-1] + "," if said.endswith(".") else said      # “Stay close,” Tom said.
            new_lines += [{"speaker": who, "text": said}, {"speaker": "Narrator", "text": tag}]
            text.append(f"“{shown}” {tag}")
        sc["lines"] = new_lines
        sc["narration"] = " ".join(text)


def normalize_quotes(text):
    """Straight quotes to curly; 'single-quoted speech,' to “double” so it reads (and voices) as dialogue."""
    # inside the quote, an apostrophe is allowed between letters (didn't, I'm)
    text = re.sub(r"(?<![\w’'])'((?:[^'\n]|(?<=\w)'(?=\w)){2,}?[.,!?…—-])'(?=[\s)\]—]|$)", r"“\1”", text or "")
    out, open_ = [], True
    for ch in text:
        if ch == '"':
            out.append("“" if open_ else "”")
            open_ = not open_
        else:
            out.append(ch)
    return "".join(out)


def clean_prose(text):
    """Remove the bits small models add around a scene: headings, 'Here is…', markdown stars."""
    lines = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if re.match(r"^(#+|\*\*?(chapter|scene)\b|(chapter|scene)\s+\d+\b)", s, re.I):
            continue
        if not lines and re.match(r"^(sure|here('s| is)|certainly|okay)\b.*:?$", s, re.I) and len(s) < 160:
            continue
        lines.append(s.replace("**", "").replace("__", ""))
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return normalize_quotes(text)


def lines_from_prose(text, cast):
    """Split prose into narrator and character parts so each character gets their own voice.
    The speaker is the name in the speech tag next to the quote; otherwise conversation alternates."""
    names = [c["name"] for c in cast or [] if c.get("name")]
    keys = {}
    for n in names:
        keys[n] = n
        keys.setdefault(n.split()[0], n)
        keys.setdefault(n.split()[-1], n)
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True)) + r")\b") if keys else None

    gender = {c["name"]: ("she" if c.get("voice") in ("female", "girl", "old_woman") else
                          "he" if c.get("voice") in ("male", "boy", "old_man") else "") for c in cast or [] if c.get("name")}

    def by_pronoun(s, recent):
        m = re.match(r"\W*(she|he)\b", s, re.I)
        if not m:
            return None
        want = m.group(1).lower()
        for name in reversed(recent):
            if gender.get(name) == want:
                return name
        return next((n for n, g in gender.items() if g == want), "Woman" if want == "she" else "Man")

    verbs = (r"(?:said|asked|replied|whispered|murmured|called|shouted|cried|answered|added|laughed|"
             r"breathed|sighed|moaned|groaned|muttered|told|teased|admitted|gasped|growled|snapped|began|continued)")
    tag_name = re.compile(r"^\W*(?:" + verbs + r"\s+([A-Z][a-z]+)|([A-Z][a-z]+)(?:\s+[A-Z][a-z]+)?\s+" + verbs + r")\b")
    skip = {"She", "He", "They", "I", "We", "You", "It", "The", "Then", "And", "But"}

    def first(s):
        m = pat.search(s) if pat else None
        if m:
            return keys[m.group(1)]
        m = tag_name.match(s)                 # “…,” Jake said.  /  “…,” said Jake.
        name = m and (m.group(1) or m.group(2))
        return name if name and name not in skip else None

    def last(s):
        ms = list(pat.finditer(s)) if pat else []
        return keys[ms[-1].group(1)] if ms else None

    parts = re.split(r"(“[^”]+”)", text)
    out, recent = [], []
    for i, seg in enumerate(parts):
        if not seg.strip():
            continue
        if seg.startswith("“") and seg.endswith("”"):
            after = parts[i + 1][:90] if i + 1 < len(parts) else ""
            before = parts[i - 1][-90:] if i > 0 else ""
            tag = re.split(r"[.!?]", after, maxsplit=1)[0]
            if re.match(r"^\W*(I\b|said I\b|I'd\b)", tag):        # first person: "I said" is the narrator
                who = "Narrator"
            else:
                who = first(tag) or by_pronoun(tag, recent) or last(before.split(".")[-1])
            if who and who not in gender and who not in ("Narrator", "Man", "Woman"):
                gender[who] = guess_gender(who, text, list(gender) + [who])   # someone not in the cast
            if not who:
                who = recent[-2] if len(recent) >= 2 else (recent[-1] if recent else "Narrator")
            spoken = seg[1:-1].strip()
            if spoken:
                out.append({"speaker": who, "text": spoken})
                if who != "Narrator" and (not recent or recent[-1] != who):
                    recent.append(who)
        else:
            s = seg.strip()
            if s:
                out.append({"speaker": "Narrator", "text": s})
    return out


def write_prose_story(cfg, idea, genre, audience, rules, scenes, series_text, chapters, words, job, level):
    """Long and detailed stories: plan the chapters and scenes, then write every scene on its own as
    plain prose (far longer and better from small local models than one big JSON reply). Short scenes
    are extended. Every scene is saved, so a stopped run carries on where it left off."""
    demo = cfg["STORY_PROVIDER"].lower() == "demo"
    adult = bool(cfg.get("_ADULT_ONLY"))
    system = EXPLICIT_SYSTEM if adult else None
    extra = ("If this scene involves sex, write it out fully and explicitly, moment by moment, in graphic detail. "
             if adult else "")
    target = int(sum(int(x) for x in words.split("–")) / 2)
    saved = (lambda name: job / name) if job else None

    plan_file = saved("outline.json") if saved else None
    if plan_file and plan_file.exists():
        outline = json.loads(plan_file.read_text())
    elif demo:
        time.sleep(float(os.environ.get("STORYMAKER_DEMO_DELAY", 0)))
        base = demo_story(idea or "the next adventure", 1)
        outline = {k: base[k] for k in ("title", "logline", "visual_style", "characters", "cast")}
        outline["chapters"] = [{"title": t_, "summary": f"Chapter {i + 1}.", "scenes": [f"Beat {j + 1}" for j in range(scenes)]}
                               for i, t_ in enumerate(["The Strange Signal", "Into the Storm", "The Hidden Door",
                                                       "What the Lighthouse Knew", "Morning"] * 4)][:chapters]
    else:
        report("writing", 0.01, "Planning the chapters and scenes")
        outline = ask_writer(cfg, OUTLINE_PROMPT.format(
            chapters=chapters, visual="illustrations", idea=idea or "Continue the story with a fresh new adventure.",
            genre=genre, audience=audience, rules=rules, series=series_text, scenes=scenes,
            keywords=keywords_block(cfg.get("_KEYWORDS"), whole=True)), system=system)
    chs = (outline.get("chapters") or [])[:chapters]
    chs += [{"title": f"Chapter {i + 1}", "summary": "Continue the story."} for i in range(len(chs), chapters)]
    for ch in chs:
        beats = [b if isinstance(b, str) else str(b.get("summary") or b.get("description") or b)
                 for b in (ch.get("scenes") or [])][:scenes]
        beats += [f"Continue chapter events (part {j + 1})." for j in range(len(beats), scenes)]
        ch["scenes"] = beats
    outline["chapters"] = chs
    if adult:
        outline = adult_tidy(outline)
    if plan_file:
        plan_file.write_text(json.dumps(outline, indent=2))
    plan = "\n".join(f"{i + 1}. {c.get('title', '')}: {c.get('summary', '')}" for i, c in enumerate(chs))
    cast = list(outline.get("cast") or [])
    fmt = dict(title=outline.get("title", ""), genre=genre, audience=audience, rules=rules)

    all_scenes, so_far, total = [], "", chapters * scenes
    for k, ch in enumerate(chs, 1):
        for j, beat in enumerate(ch["scenes"], 1):
            done = len(all_scenes)
            report("writing", 0.03 + 0.97 * done / total,
                   f"Writing chapter {k} of {chapters}, scene {j} of {scenes}" if chapters > 1
                   else f"Writing scene {j} of {scenes}")
            f = saved(f"scene_c{k:02d}_s{j:02d}.txt") if saved else None
            if f and f.exists():
                text = f.read_text()
            elif demo:
                time.sleep(float(os.environ.get("STORYMAKER_DEMO_DELAY", 0)))
                text = demo_story("x", 1)["scenes"][0]["narration"]
            else:
                prev = (f"\nThe story so far ends like this:\n…{so_far[-1500:]}\n" if so_far else
                        "\nThis is the very beginning of the story.\n")
                for fresh in range(3):                    # explicit: a scene that goes wrong is written again
                    text = clean_prose(ask_writer(cfg, SCENE_PROMPT.format(
                        **fmt, characters=outline.get("characters", ""), plan=plan, n=k, total=chapters,
                        chapter_title=ch.get("title", ""), summary=ch.get("summary", ""), k=j, scenes=scenes,
                        beat=beat, previous=prev, words=target,
                        extra=extra + (ADULT_REDO if fresh else ""),
                        keywords=keywords_block(cfg.get("_KEYWORDS"), so_far, last=done == total - 1)),
                        as_json=False, system=system))
                    for _ in range(3):                        # too short? ask it to keep going
                        have = len(text.split())
                        if have >= target * 0.75:
                            break
                        report("writing", 0.03 + 0.97 * done / total,
                               f"Writing chapter {k}, scene {j}: adding detail ({have} of about {target} words)")
                        more = clean_prose(ask_writer(cfg, CONTINUE_PROMPT.format(
                            **fmt, text=text[-6000:], more=target - have,
                            extra=extra + (ADULT_REDO if fresh else "")), as_json=False, system=system))
                        if len(more.split()) < 20:
                            break
                        text = text.rstrip() + "\n\n" + more
                    if not adult:
                        break
                    try:
                        text = adult_guard(cfg, text, system)
                        break
                    except SceneRedo:
                        if fresh == 2:
                            raise
                        log("The writer put something under-age into a scene: throwing it away and writing it again.")
                        report("writing", 0.03 + 0.97 * done / total,
                               f"Writing chapter {k}, scene {j} again (everyone in it is an adult)")
                if f:
                    f.write_text(text)
            sc = {"heading": beat[:80], "narration": text, "lines": lines_from_prose(text, cast),
                  "image_prompt": f"{beat} — {outline.get('characters', '')}",
                  "video_prompt": f"{beat}. Slow cinematic camera move, natural light and ambient sound.",
                  "chapter": k}
            if j == 1 and chapters > 1:
                sc["chapter_title"] = ch.get("title", "") or f"Chapter {k}"
            all_scenes.append(sc)
            so_far += "\n\n" + text
    kws = cfg.get("_KEYWORDS") or []
    missing = [k_ for k_ in kws if not keyword_used(so_far, k_)]
    if missing and not demo and all_scenes:
        # still missing some of the listener's words: weave them into the scenes that fit best
        report("writing", 0.99, f"Working in your words: {', '.join(missing[:6])}")
        targets = {}
        for k_ in missing:
            kw_words = set(_words(k_))
            best = max(range(len(all_scenes)), key=lambda i: len(kw_words & set(_words(all_scenes[i]["narration"]))))
            targets.setdefault(best, []).append(k_)
        for i, ks in targets.items():
            sc = all_scenes[i]
            new = clean_prose(ask_writer(cfg, REWRITE_PROMPT.format(
                title=outline.get("title", ""), genre=genre, audience=audience, rules=rules,
                characters=outline.get("characters", ""), context="", text=sc["narration"],
                instructions=f"Keep the scene as it is, but naturally work in these words and phrases: {', '.join(ks)}.",
                keywords="", words=len(sc["narration"].split()), extra=extra), as_json=False, system=system))
            if len(new.split()) > 0.6 * len(sc["narration"].split()):
                if adult:
                    try:
                        new = adult_guard(cfg, new, system)
                    except SceneRedo:
                        continue                            # keep the scene as it was
                sc["narration"], sc["lines"] = new, lines_from_prose(new, cast)
    story = {k_: outline.get(k_, "") for k_ in ("title", "logline", "visual_style", "characters")}
    story.update(cast=cast, scenes=all_scenes)
    if chapters > 1:
        story["chapters"] = [{"number": i + 1, "title": c.get("title", "")} for i, c in enumerate(chs)]
    return story


def write_chapters(cfg, idea, genre, audience, rules, scenes, clip_mode, series_text, chapters, words, job):
    """Plan the whole story first, then write it one chapter at a time (each chapter is its own
    request, so long stories aren't cut short). Each finished chapter is saved, so a stopped run
    picks up at the next chapter."""
    visual = "short cinematic video clips" if clip_mode else "illustrations"
    demo = cfg["STORY_PROVIDER"].lower() == "demo"
    saved = (lambda name: job / name) if job else None

    plan_file = saved("outline.json") if saved else None
    if plan_file and plan_file.exists():
        outline = json.loads(plan_file.read_text())
    elif demo:
        time.sleep(float(os.environ.get("STORYMAKER_DEMO_DELAY", 0)))
        base = demo_story(idea or "the next adventure", 1)
        outline = {k: base[k] for k in ("title", "logline", "visual_style", "characters", "cast")}
        outline["chapters"] = [{"title": t_, "summary": f"Chapter {i + 1} of the adventure."}
                               for i, t_ in enumerate(["The Strange Signal", "Into the Storm", "The Hidden Door",
                                                       "What the Lighthouse Knew", "Morning"] * 4)][:chapters]
    else:
        report("writing", 0.02, "Planning the chapters")
        outline = ask_writer(cfg, OUTLINE_PROMPT.format(
            chapters=chapters, visual=visual, idea=idea or "Continue the story with a fresh new adventure.",
            genre=genre, audience=audience, rules=rules, series=series_text, scenes=scenes,
            keywords=keywords_block(cfg.get("_KEYWORDS"), whole=True)),
            system=EXPLICIT_SYSTEM if cfg.get("_ADULT_ONLY") else None)
    chs = (outline.get("chapters") or [])[:chapters]
    if len(chs) < chapters:
        chs += [{"title": f"Chapter {i + 1}", "summary": "Continue the story."} for i in range(len(chs), chapters)]
    outline["chapters"] = chs
    if cfg.get("_ADULT_ONLY"):
        outline = adult_tidy(outline)
    if plan_file:
        plan_file.write_text(json.dumps(outline, indent=2))
    plan = "\n".join(f"{i + 1}. {c.get('title', '')}: {c.get('summary', '')}" for i, c in enumerate(chs))
    cast = list(outline.get("cast") or [])

    all_scenes, previous = [], "This is the first chapter."
    for k, ch in enumerate(chs, 1):
        report("writing", k / (chapters + 1), f"Writing chapter {k} of {chapters}: {ch.get('title', '')}")
        f = saved(f"chapter_{k:02d}.json") if saved else None
        if f and f.exists():
            part = json.loads(f.read_text())
        elif demo:
            time.sleep(float(os.environ.get("STORYMAKER_DEMO_DELAY", 0)))
            part = {"scenes": demo_story(idea or "the adventure", scenes)["scenes"], "new_cast": []}
        else:
            for fresh in range(3):                        # explicit: a chapter that goes wrong is written again
                part = ask_writer(cfg, CHAPTER_PROMPT.format(
                    n=k, total=chapters, title=outline.get("title", ""), visual=visual, audience=audience,
                    rules=rules, genre=genre, characters=outline.get("characters", ""),
                    cast=", ".join(f"{c.get('name')} ({c.get('voice')})" for c in cast) or "none yet",
                    plan=plan, previous=previous, chapter_title=ch.get("title", ""), summary=ch.get("summary", ""),
                    scenes=scenes, words=words, keywords=keywords_block(cfg.get("_KEYWORDS"), whole=True))
                    + (ADULT_REDO if fresh else ""))
                if not part.get("scenes"):
                    raise ValueError(f"The writer sent chapter {k} without any scenes.")
                if not cfg.get("_ADULT_ONLY"):
                    break
                try:
                    for sc_ in part.get("scenes") or []:
                        if isinstance(sc_, dict) and isinstance(sc_.get("narration"), str):
                            sc_["narration"] = adult_guard(cfg, sc_["narration"], EXPLICIT_SYSTEM)
                    part = adult_tidy(part)
                    break
                except SceneRedo:
                    if fresh == 2:
                        raise
                    log("The writer put something under-age into a chapter: throwing it away and writing it again.")
            if f:
                f.write_text(json.dumps(part, indent=2))
        known = {c.get("name", "").lower() for c in cast}
        cast += [c for c in part.get("new_cast") or [] if c.get("name", "").lower() not in known]
        for j, sc in enumerate(part["scenes"]):
            sc["chapter"] = k
            if j == 0:
                sc["chapter_title"] = ch.get("title", "") or f"Chapter {k}"
            all_scenes.append(sc)
        previous = part["scenes"][-1].get("narration", "")[-600:]
    story = {k_: outline.get(k_, "") for k_ in ("title", "logline", "visual_style", "characters")}
    story.update(cast=cast, scenes=all_scenes,
                 chapters=[{"number": i + 1, "title": c.get("title", "")} for i, c in enumerate(chs)])
    return story


def demo_story(idea, scenes):
    narr = [
        "Once upon a time, at the edge of a sleepy town, something unexpected began.",
        "No one noticed at first, until the small signs became a question nobody could ignore.",
        "Our hero packed a lantern and a stubborn heart, and set off to find out why.",
        "The path twisted, the night grew loud, and for a moment it all seemed a mistake.",
        "But courage, it turns out, is just fear that keeps walking.",
        "And when the sun rose, the town woke to a different, brighter morning.",
    ]
    talk = [("Mira", "Did you hear that?"), ("Old Tom", "Stay close, little one."),
            ("Mira", "I'm not scared. Well, a little."), ("Old Tom", "That's what brave feels like.")]
    scenes_out = []
    for i in range(scenes):
        who, said = talk[i % len(talk)]
        lines = [{"speaker": "Narrator", "text": narr[i % len(narr)]}, {"speaker": who, "text": said}]
        scenes_out.append({"heading": f"Scene {i + 1}", "narration": " ".join(l["text"] for l in lines),
                           "lines": lines, "image_prompt": f"scene {i + 1} of a story about {idea}",
                           "video_prompt": f"scene {i + 1} of a story about {idea}"})
    return {
        "title": f"The Tale of {idea.strip().rstrip('.').title()[:50] or 'Somewhere'}",
        "logline": f"A demo story about {idea}.",
        "visual_style": "warm storybook illustration",
        "characters": "Mira, a curious girl in a red scarf; Old Tom, a lanky lighthouse keeper with a white beard",
        "cast": [{"name": "Mira", "voice": "girl"}, {"name": "Old Tom", "voice": "old_man"}],
        "scenes": scenes_out,
    }


# ───────────────────────────── helpers ─────────────────────────────

def run(cmd, **kw):
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {res.stderr[-600:]}")
    return res


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", str(path)]).stdout.strip())


def has_audio(path):
    return bool(run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                     "-of", "csv=p=0", str(path)]).stdout.strip())


def valid_media(path):
    try:
        return Path(path).exists() and Path(path).stat().st_size > 0 and duration(path) > 0
    except Exception:
        return False


def find_font(size, bold=True):
    for c in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def fit_cover(img, w, h):
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def shot_prompt(story, scene, kind):
    base = scene.get("video_prompt" if kind == "video" else "image_prompt") or scene.get("image_prompt", "")
    tail = (" Characters do not speak. No on-screen text, captions, subtitles or watermarks."
            if kind == "video" else " No text, words, letters, captions or watermarks anywhere in the image.")
    if story.get("level") in ("young-kids", "kids"):
        tail += " Gentle, friendly and child-appropriate; nothing scary or violent."
    us = story.get("user_style") or {}
    look = ". ".join(x for x in (us.get("pictures"), story.get("visual_style", "")) if x)
    if kind == "video" and us.get("video"):
        tail = f" Camera and motion: {us['video']}." + tail
    return f"Style: {look}. Characters: {story.get('characters', '')}. Shot: {base}.{tail}"


# ───────────────────────────── pictures ─────────────────────────────

TASTEFUL = (" Tasteful, romantic and suggestive at most: fully clothed or covered, no nudity, nothing sexual shown. "
            "Every person is clearly an adult.")
TASTEFUL_NEG = ("nude, nudity, naked, nsfw, explicit, sexual, nipples, genitals, underwear, lingerie, child, children, "
                "teen, young-looking, minor")


def local_image(cfg, prompt, w, h, out_path, level="family"):
    """A picture from the image app on your own computer (Draw Things, or Automatic1111/Forge):
    its txt2img API. Free and private. Grown-up stories only ever get tasteful pictures."""
    neg = "text, letters, words, watermark, signature, blurry, deformed, extra fingers, bad anatomy"
    avoid = (((cfg.get("_STYLE") or {}).get("pictures") or {}).get("avoid") or "").strip()
    if avoid:
        neg += ", " + _clip(avoid, 200)
    if level in ("mature", "explicit"):
        prompt += TASTEFUL
        neg += ", " + TASTEFUL_NEG
    elif level in ("young-kids", "kids", "middle-grade", "family"):
        neg += ", scary, gore, violence"
    try:
        r = requests.post(cfg["LOCAL_IMAGE_URL"].rstrip("/") + "/sdapi/v1/txt2img", timeout=900, json={
            "prompt": prompt[:1500], "negative_prompt": neg, "width": w, "height": h,
            "steps": int(cfg["LOCAL_IMAGE_STEPS"]), "cfg_scale": float(cfg["LOCAL_IMAGE_CFG"]), "seed": -1,
            "batch_size": 1})
    except requests.ConnectionError:
        raise RuntimeError(f"Can't reach the picture app at {cfg['LOCAL_IMAGE_URL']}. In Draw Things, turn on "
                           "Settings → API Server (HTTP) and keep the app open.")
    if r.status_code != 200:
        raise RuntimeError(f"Picture app error {r.status_code}: {r.text[:200]}")
    images = (r.json() or {}).get("images") or []
    if not images:
        raise RuntimeError("The picture app sent no image")
    data = images[0].split(",", 1)[-1] if images[0].startswith("data:") else images[0]
    Path(out_path).write_bytes(base64.b64decode(data))


def make_cover(cfg, story, out_dir, level):
    """Cover art for the library, the audiobook and the book (only when pictures are free)."""
    out = out_dir / "cover.jpg"
    if out.exists() or cfg.get("COVER_ART", "on") != "on" or pick_image_provider(cfg) != "local":
        return out if out.exists() else None
    raw = out_dir / ".cover.png"
    look = ". ".join(x for x in ((story.get("user_style") or {}).get("pictures"), story.get("visual_style", "")) if x)
    prompt = (f"Book cover art for the story \"{story['title']}\": {story.get('logline', '')}. "
              f"{look}. Characters: {story.get('characters', '')}. "
              "Beautiful, cinematic, evocative. No text, no title, no lettering.")
    try:
        local_image(cfg, prompt, 1024, 1024, raw, level)
        Image.open(raw).convert("RGB").save(out, quality=90)
        return out
    except Exception as e:
        log(f"Cover art skipped ({str(e)[:140]}).")
        return None
    finally:
        raw.unlink(missing_ok=True)


def openai_image(cfg, prompt, portrait, out_path):
    r = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {cfg['OPENAI_API_KEY']}", "content-type": "application/json"},
        json={"model": cfg["OPENAI_IMAGE_MODEL"], "prompt": prompt,
              "size": "1024x1536" if portrait else "1536x1024",
              "quality": cfg["OPENAI_IMAGE_QUALITY"], "n": 1},
        timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Image API error {r.status_code}: {r.text[:300]}")
    out_path.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))


def gemini_image(cfg, prompt, portrait, out_path, ref=None):
    parts = []
    if ref is not None and Path(ref).exists():
        parts.append({"inlineData": {"mimeType": "image/png", "data": b64file(ref)}})
        prompt = "Keep the characters looking exactly like the reference sheet. " + prompt
    parts.append({"text": prompt + (" Vertical 9:16 framing." if portrait else " Wide 16:9 framing.")})
    r = requests.post(
        f"{GEMINI_BASE}/models/{cfg['GEMINI_IMAGE_MODEL']}:generateContent",
        headers={"x-goog-api-key": cfg["GEMINI_API_KEY"], "content-type": "application/json"},
        json={"contents": [{"parts": parts}], "generationConfig": {"responseModalities": ["IMAGE"]}},
        timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini image error {r.status_code}: {r.text[:300]}")
    for p in r.json()["candidates"][0]["content"]["parts"]:
        data = p.get("inlineData") or p.get("inline_data")
        if data:
            out_path.write_bytes(base64.b64decode(data["data"]))
            return
    raise RuntimeError("Gemini returned no image")


def title_card(text, w, h, out_path, hue=0):
    palettes = [((32, 44, 84), (120, 72, 140)), ((18, 64, 72), (40, 130, 120)),
                ((80, 40, 30), (190, 110, 60)), ((30, 30, 60), (70, 110, 170))]
    a, b = palettes[hue % len(palettes)]
    strip = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        strip.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    img = strip.resize((w, h), Image.BILINEAR)
    d = ImageDraw.Draw(img)
    font = find_font(int(min(w, h) * 0.075))
    lines = textwrap.wrap(text, 22)
    step = int(min(w, h) * 0.095)
    y = (h - len(lines) * step) // 2 - int(h * 0.12)
    for ln in lines:
        d.text(((w - d.textlength(ln, font=font)) / 2, y), ln, font=font, fill=(255, 255, 255))
        y += step
    img.save(out_path)


def pick_image_provider(cfg):
    p = cfg["IMAGE_PROVIDER"].lower()
    if p != "auto":
        return p
    if cfg.get("LOCAL_IMAGE_URL"):              # free, on your own computer
        return "local"
    if cfg["OPENAI_API_KEY"]:
        return "openai"
    if cfg["GEMINI_API_KEY"]:
        return "gemini"
    return "cards"


def ref_provider(cfg):
    """Which service draws the character sheet (None = skip)."""
    if cfg["CHARACTER_REF"] != "on":
        return None
    if cfg["STORY_PROVIDER"] == "demo" and cfg["VIDEO_PROVIDER"] in ("demo", "slideshow"):
        return "demo"
    if cfg["GEMINI_API_KEY"]:
        return "gemini"
    if cfg["OPENAI_API_KEY"]:
        return "openai"
    return None


def make_character_ref(cfg, story, job):
    """One reference picture of the main characters, reused for every clip and picture."""
    out = job / "character_ref.png"
    if out.exists():
        log("Reusing the existing character sheet.")
        return out
    prov = ref_provider(cfg)
    if not prov:
        return None
    prompt = (f"Character reference sheet. Style: {story.get('visual_style', '')}. "
              f"Show each main character full-body, front view, side by side on a plain light background: "
              f"{story.get('characters', '')}. Consistent proportions and clothing. "
              "No text, labels, names or watermarks.")
    log(f"Drawing the character sheet with {prov}…")
    try:
        if prov == "gemini":
            gemini_image(cfg, prompt, False, out)
        elif prov == "openai":
            openai_image(cfg, prompt, False, out)
        else:
            title_card("Characters", 1536, 1024, out, 3)
        Image.open(out).convert("RGB").save(out, "PNG")     # normalise to real PNG
        return out
    except Exception as e:
        log(f"Character sheet failed ({e}); continuing without it.")
        out.unlink(missing_ok=True)
        return None


def picture_for(cfg, story, i, w, h, portrait, job, provider, ref):
    out = job / f"pic_{i + 1:02d}.png"
    if out.exists():
        return out
    sc = story["scenes"][i]
    raw = job / f"raw_{i + 1:02d}.png"
    try:
        if provider == "openai":
            openai_image(cfg, shot_prompt(story, sc, "image"), portrait, raw)
        elif provider == "gemini":
            gemini_image(cfg, shot_prompt(story, sc, "image"), portrait, raw, ref)
        elif provider == "local":
            lw, lh = (576, 1024) if portrait else (1024, 576)
            local_image(cfg, shot_prompt(story, sc, "image"), lw, lh, raw, story.get("level", "family"))
        else:
            title_card(sc.get("heading", ""), w, h, raw, i)
    except Exception as e:
        log(f"Picture {i + 1} failed ({e}); using a title card.")
        title_card(sc.get("heading", ""), w, h, raw, i)
    fit_cover(Image.open(raw), w, h).save(out)
    raw.unlink(missing_ok=True)
    return out


def make_pictures(cfg, story, job, w, h, portrait, which, ref):
    idxs = list(which)
    if not idxs:
        return {}
    provider = pick_image_provider(cfg)
    log(f"Making {len(idxs)} picture(s) with {provider}…")
    step = (_PROG.get("status") or {}).get("step", "video")
    lo, hi = ((0.70, 0.80) if _PROG.get("clips") else (0.0, 0.80)) if step == "video" else (0.05, 0.85)
    count = [0]

    def one(i):
        pic = picture_for(cfg, story, i, w, h, portrait, job, provider, ref)
        count[0] += 1
        report(step, lo + (hi - lo) * count[0] / len(idxs), f"Drawing pictures: {count[0]} of {len(idxs)}", image=pic)
        return i, pic
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        return dict(pool.map(one, idxs))


def caption_overlay(text, w, h, out_path):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if text:
        d = ImageDraw.Draw(img)
        size = max(26, int(min(w, h) * 0.036))
        font = find_font(size, bold=True)
        max_w = int(w * 0.86)
        lines, cur = [], ""
        for wd in text.split():
            test = (cur + " " + wd).strip()
            if d.textlength(test, font=font) <= max_w:
                cur = test
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        lh = int(size * 1.3)
        band_h = lh * len(lines) + int(size * 1.2)
        top = h - band_h - int(h * 0.04)
        d.rounded_rectangle([int(w * 0.04), top, int(w * 0.96), top + band_h], radius=int(size * 0.6),
                            fill=(0, 0, 0, 150))
        y = top + int(size * 0.6)
        for ln in lines:
            d.text(((w - d.textlength(ln, font=font)) / 2, y), ln, font=font, fill=(255, 255, 255, 255))
            y += lh
    img.save(out_path)


# ───────────────────────────── AI video clips ─────────────────────────────

REF_NOTE = ("<IMAGE_REF_0> is the character reference sheet: keep every character looking exactly like it "
            "(face, hair, clothing, colors). Do not show the sheet itself. ")


def omni_clip(cfg, prompt, portrait, out_path, ref=None):
    """Gemini Omni Flash (Interactions API). Tries with the character sheet, then without."""
    key = need(cfg, "GEMINI_API_KEY")
    body = {"model": cfg["OMNI_MODEL"], "store": False, "stream": False, "background": False,
            "response_format": {"type": "video", "aspect_ratio": "9:16" if portrait else "16:9",
                                "resolution": cfg["VIDEO_RESOLUTION"], "delivery": "uri"}}
    attempts = []
    if ref is not None:
        attempts.append(dict(body, input=[{"type": "image", "data": b64file(ref), "mime_type": "image/png"},
                                          {"type": "text", "text": REF_NOTE + prompt}],
                             generation_config={"video_config": {"task": "reference_to_video"}}))
    attempts.append(dict(body, input=prompt))
    r = None
    for i, payload in enumerate(attempts):
        r = requests.post(f"{GEMINI_BASE}/interactions", json=payload, timeout=900,
                          headers={"x-goog-api-key": key, "content-type": "application/json"})
        if r.status_code == 200:
            break
        if r.status_code == 400 and i < len(attempts) - 1:
            log("Omni didn't accept the character sheet for this clip; retrying without it.")
            continue
        raise RuntimeError(f"Omni error {r.status_code}: {r.text[:300]}")
    video = None
    for step in r.json().get("steps", []):
        for item in step.get("content", []) or []:
            if item.get("type") == "video":
                video = item
    if not video:
        raise RuntimeError(f"Omni returned no video: {r.text[:300]}")
    if video.get("data"):
        out_path.write_bytes(base64.b64decode(video["data"]))
        return
    file_id = video["uri"].rstrip("/").split("/")[-1].split(":")[0].split("?")[0]
    for _ in range(120):
        st = requests.get(f"{GEMINI_BASE}/files/{file_id}", headers={"x-goog-api-key": key}, timeout=60)
        state = st.json().get("state", "ACTIVE") if st.status_code == 200 else "ACTIVE"
        if state == "ACTIVE":
            break
        if state == "FAILED":
            raise RuntimeError("Omni video file failed to process")
        time.sleep(5)
    dl = requests.get(f"{GEMINI_BASE}/files/{file_id}:download", params={"alt": "media"},
                      headers={"x-goog-api-key": key}, timeout=600)
    if dl.status_code != 200:
        raise RuntimeError(f"Omni download error {dl.status_code}: {dl.text[:200]}")
    out_path.write_bytes(dl.content)


def veo_clip(cfg, prompt, portrait, out_path, ref=None):
    """Veo 3.1 (long-running operation). Tries with the character sheet, then without."""
    key = need(cfg, "GEMINI_API_KEY")
    hdr = {"x-goog-api-key": key, "content-type": "application/json"}
    params = {"aspectRatio": "9:16" if portrait else "16:9", "resolution": cfg["VIDEO_RESOLUTION"],
              "durationSeconds": 8}
    attempts = []
    if ref is not None:
        attempts.append({"instances": [{"prompt": prompt, "referenceImages": [
            {"image": {"bytesBase64Encoded": b64file(ref), "mimeType": "image/png"}, "referenceType": "asset"}]}],
            "parameters": dict(params, personGeneration="allow_adult")})
    attempts.append({"instances": [{"prompt": prompt}], "parameters": dict(params, personGeneration="allow_all")})
    r = None
    for i, payload in enumerate(attempts):
        r = requests.post(f"{GEMINI_BASE}/models/{cfg['VEO_MODEL']}:predictLongRunning",
                          headers=hdr, json=payload, timeout=120)
        if r.status_code == 200:
            break
        if r.status_code == 400 and i < len(attempts) - 1:
            log("Veo didn't accept the character sheet for this clip; retrying without it.")
            continue
        raise RuntimeError(f"Veo error {r.status_code}: {r.text[:300]}")
    op = r.json()["name"]
    for _ in range(90):
        time.sleep(10)
        st = requests.get(f"{GEMINI_BASE}/{op}", headers=hdr, timeout=60).json()
        if st.get("done"):
            if "error" in st:
                raise RuntimeError(f"Veo failed: {st['error']}")
            samples = st.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", [])
            if not samples:
                raise RuntimeError(f"Veo returned no video (it may have been filtered): {json.dumps(st)[:300]}")
            dl = requests.get(samples[0]["video"]["uri"], headers={"x-goog-api-key": key}, timeout=600)
            if dl.status_code != 200:
                raise RuntimeError(f"Veo download error {dl.status_code}")
            out_path.write_bytes(dl.content)
            return
    raise RuntimeError("Veo timed out")


def demo_clip(cfg, prompt, portrait, out_path, idx=0):
    """Offline stand-in for testing: an 8-second moving test pattern (some with a tone)."""
    if str(idx + 1) in os.environ.get("STORYMAKER_FAIL_SCENE", "").split(","):
        raise RuntimeError("simulated failure")
    time.sleep(float(os.environ.get("STORYMAKER_DEMO_DELAY", 0)))
    with open(os.environ.get("STORYMAKER_DEMO_COUNTER", os.devnull), "a") as f:
        f.write(f"clip {idx + 1}\n")
    size = "720x1280" if portrait else "1280x720"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration=8"]
    if idx % 2 == 0:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={300 + 80 * idx}:duration=8", "-shortest"]
    run(cmd + ["-pix_fmt", "yuv420p", str(out_path)])


def make_clips(cfg, story, job, portrait, provider, ref):
    scenes = story["scenes"]
    paths = [None] * len(scenes)
    todo = []
    for i in range(len(scenes)):
        p = job / f"clip_{i + 1:02d}.mp4"
        if valid_media(p):
            paths[i] = p
        else:
            todo.append(i)
    if len(todo) < len(scenes):
        log(f"Reusing {len(scenes) - len(todo)} clip(s) already made — only {len(todo)} to go.")
    if not todo:
        return paths

    def one(i):
        out = job / f"clip_{i + 1:02d}.mp4"
        tmp = job / f"clip_{i + 1:02d}.part.mp4"
        try:
            if provider == "demo":
                demo_clip(cfg, "", portrait, tmp, i)
            elif provider == "omni":
                omni_clip(cfg, shot_prompt(story, scenes[i], "video"), portrait, tmp, ref)
            else:
                veo_clip(cfg, shot_prompt(story, scenes[i], "video"), portrait, tmp, ref)
            if not valid_media(tmp):
                raise RuntimeError("the downloaded clip is not a playable video")
            tmp.replace(out)                    # only finished clips get the real name
            paths[i] = out
            log(f"Clip {i + 1}/{len(scenes)} ready")
            got = sum(1 for x in paths if x)
            report("video", 0.70 * got / len(scenes), f"Filming: {got} of {len(scenes)} scenes done", image=out)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            log(f"Clip {i + 1} failed ({e}); that scene will use a still picture this time.")

    log(f"Generating {len(todo)} video clip(s) with {provider} (this is the slow part)…")
    got = len(scenes) - len(todo)
    _PROG["clips"] = True
    report("video", 0.70 * got / len(scenes), f"Filming {len(todo)} scene{'s' * (len(todo) != 1)} (the slow part, a few minutes each)")
    with cf.ThreadPoolExecutor(max_workers=max(1, int(cfg["PARALLEL_CLIPS"]))) as pool:
        list(pool.map(one, todo))
    return paths


# ───────────────────────────── narration ─────────────────────────────

def to_wav(src, dst, raw_pcm_rate=None):
    pre = ["-f", "s16le", "-ar", str(raw_pcm_rate), "-ac", "1"] if raw_pcm_rate else []
    run(["ffmpeg", "-y", "-v", "error", *pre, "-i", str(src), "-ar", "44100", "-ac", "1", str(dst)])


# The free Kokoro voices you can pick from (listen to them on the web page before choosing).
KOKORO_CATALOGUE = [
    ("af_heart", "Heart", "female", "American", "warm, natural"), ("af_bella", "Bella", "female", "American", "rich, expressive"),
    ("af_nicole", "Nicole", "female", "American", "soft, close"), ("af_sarah", "Sarah", "female", "American", "clear, friendly"),
    ("af_jessica", "Jessica", "female", "American", "casual"), ("af_river", "River", "female", "American", "calm"),
    ("af_kore", "Kore", "female", "American", "steady"), ("af_aoede", "Aoede", "female", "American", "gentle"),
    ("af_alloy", "Alloy", "female", "American", "even"), ("af_sky", "Sky", "female", "American", "young, bright"),
    ("af_nova", "Nova", "female", "American", "upbeat"), ("bf_emma", "Emma", "female", "British", "warm"),
    ("bf_isabella", "Isabella", "female", "British", "low, smooth"), ("bf_alice", "Alice", "female", "British", "mature"),
    ("bf_lily", "Lily", "female", "British", "light"),
    ("am_michael", "Michael", "male", "American", "deep, warm"), ("am_fenrir", "Fenrir", "male", "American", "strong, rough"),
    ("am_onyx", "Onyx", "male", "American", "very deep"), ("am_eric", "Eric", "male", "American", "confident"),
    ("am_liam", "Liam", "male", "American", "young"), ("am_adam", "Adam", "male", "American", "plain"),
    ("am_puck", "Puck", "male", "American", "lively"), ("am_echo", "Echo", "male", "American", "light, youthful"),
    ("bm_george", "George", "male", "British", "classic"), ("bm_fable", "Fable", "male", "British", "storyteller"),
    ("bm_daniel", "Daniel", "male", "British", "calm"), ("bm_lewis", "Lewis", "male", "British", "older"),
]
VOICE_SPEC = re.compile(r"^[ab][fm]_[a-z]+(\+[ab][fm]_[a-z]+)?(\^(0\.[89]\d?|1\.[01]\d?))?$")
SAMPLE_TEXT = ("Hello. This is how I sound when I read your story. "
               "“Are you coming with me?” she asked. “Always,” he said, and smiled.")


def voice_sample(cfg, spec):
    """A short clip of a Kokoro voice, made once and kept, so you can listen before choosing."""
    if not VOICE_SPEC.match(spec or ""):
        raise ValueError("Unknown voice.")
    if not kokoro_ok():
        raise ValueError("Voice samples need the free Kokoro voices (pip install kokoro-onnx soundfile).")
    folder = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / ".voice-samples"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{hashlib.sha1(spec.encode()).hexdigest()[:12]}.mp3"
    if not valid_media(out):
        wav = folder / f"{out.stem}.wav"
        narrate(cfg, SAMPLE_TEXT, wav, "kokoro", spec)
        level_match(wav, folder / f"{out.stem}_l.wav")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(folder / f"{out.stem}_l.wav"), "-b:a", "128k", str(out)])
        wav.unlink(missing_ok=True)
        (folder / f"{out.stem}_l.wav").unlink(missing_ok=True)
    return out


def edge_cmd():
    """edge-tts may live next to this Python (a venv) rather than on PATH."""
    found = shutil.which("edge-tts")
    if found:
        return found
    beside = Path(sys.executable).parent / "edge-tts"
    return str(beside) if beside.exists() else None


def kokoro_ok():
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile  # noqa: F401
        return True
    except Exception:
        return False


_KOKORO = {}


def kokoro_engine(cfg):
    """Load Kokoro once, downloading its two model files the first time."""
    if "k" in _KOKORO:
        return _KOKORO["k"]
    from kokoro_onnx import Kokoro
    d = Path(os.path.expanduser(cfg["KOKORO_DIR"]))
    d.mkdir(parents=True, exist_ok=True)
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    for name in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
        f = d / name
        if not f.exists():
            log(f"Downloading the Kokoro voice ({name}, one time only)…")
            with requests.get(base + name, stream=True, timeout=600) as r:
                r.raise_for_status()
                part = f.with_suffix(".part")
                with open(part, "wb") as out:
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
                part.replace(f)
    _KOKORO["k"] = Kokoro(str(d / "kokoro-v1.0.int8.onnx"), str(d / "voices-v1.0.bin"))
    return _KOKORO["k"]


def local_tts():
    """The best voice that never leaves this computer."""
    if kokoro_ok():
        return "kokoro"
    if IS_MAC and shutil.which("say"):
        return "say"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return "silent"


def pick_tts(cfg):
    p = cfg["TTS_PROVIDER"].lower()
    if p != "auto":
        return p
    for engine, ok in (("elevenlabs", cfg["ELEVENLABS_API_KEY"]), ("openai", cfg["OPENAI_API_KEY"]),
                       ("gemini", cfg["GEMINI_API_KEY"]), ("kokoro", kokoro_ok()),
                       ("say", IS_MAC and shutil.which("say")),
                       ("edge", edge_cmd()),
                       ("espeak", shutil.which("espeak-ng") or shutil.which("espeak"))):
        if ok:
            return engine
    return "silent"


VOICE_STYLE = {
    "narrator": "Read like a warm, expressive storyteller. Natural pacing, gentle pauses.",
    "female": "Speak as an adult woman, natural and expressive, like an actor in an audiobook.",
    "male": "Speak as an adult man, natural and expressive, like an actor in an audiobook.",
    "girl": "Speak as a young girl of about eight: bright, curious and a little breathless.",
    "boy": "Speak as a young boy of about eight: eager, quick and a little cheeky.",
    "old_woman": "Speak as a kindly elderly woman: slower, warm and a little creaky.",
    "old_man": "Speak as an elderly man: slow, gravelly and kind.",
    "creature": "Speak as a fantastical creature: playful, odd and a little gravelly.",
}
DEFAULT_VOICE_KEY = {"elevenlabs": "ELEVENLABS_VOICE_ID", "openai": "OPENAI_VOICE", "gemini": "GEMINI_VOICE",
                     "say": "SAY_VOICE", "edge": "EDGE_VOICE", "kokoro": "KOKORO_VOICE"}


def narrate(cfg, text, out_wav, engine, voice=None, style=None):
    """Speak `text` into out_wav. `voice` overrides the engine's narrator voice; `style` is an
    acting note used by engines that accept one (OpenAI, Gemini)."""
    tmp = out_wav.with_suffix(".src")
    rate = None
    voice = voice or cfg.get(DEFAULT_VOICE_KEY.get(engine, ""), "")
    style = style or VOICE_STYLE["narrator"]
    if engine == "elevenlabs":
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": cfg["ELEVENLABS_API_KEY"], "content-type": "application/json"},
            json={"text": text, "model_id": cfg["ELEVENLABS_MODEL"]}, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")
        tmp.write_bytes(r.content)
    elif engine == "openai":
        body = {"model": cfg["OPENAI_TTS_MODEL"], "voice": voice, "input": text, "response_format": "mp3"}
        if cfg["OPENAI_TTS_MODEL"].startswith("gpt-4o"):
            body["instructions"] = style
        r = requests.post("https://api.openai.com/v1/audio/speech",
                          headers={"Authorization": f"Bearer {cfg['OPENAI_API_KEY']}"}, json=body, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"OpenAI voice error {r.status_code}: {r.text[:300]}")
        tmp.write_bytes(r.content)
    elif engine == "gemini":
        r = requests.post(
            f"{GEMINI_BASE}/models/{cfg['GEMINI_TTS_MODEL']}:generateContent",
            headers={"x-goog-api-key": cfg["GEMINI_API_KEY"], "content-type": "application/json"},
            json={"contents": [{"parts": [{"text": f"{style} Say: {text}"}]}],
                  "generationConfig": {"responseModalities": ["AUDIO"],
                                       "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}},
            timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"Gemini voice error {r.status_code}: {r.text[:300]}")
        part = r.json()["candidates"][0]["content"]["parts"][0]
        data = part.get("inlineData") or part.get("inline_data")
        tmp.write_bytes(base64.b64decode(data["data"]))
        m = re.search(r"rate=(\d+)", data.get("mimeType", data.get("mime_type", "")))
        rate = int(m.group(1)) if m else 24000
    elif engine == "say":
        tmp = tmp.with_suffix(".aiff")
        try:
            run(["say", "-o", str(tmp)] + (["-v", voice] if voice else []) + [text])
        except RuntimeError:                  # that voice isn't installed on this Mac
            run(["say", "-o", str(tmp), text])
    elif engine == "edge":
        tmp = tmp.with_suffix(".mp3")
        cmd = edge_cmd()
        if not cmd:
            raise RuntimeError("edge-tts isn't installed (pip install edge-tts)")
        run([cmd, "--voice", voice or "en-US-AndrewMultilingualNeural", "--text", text, "--write-media", str(tmp)])
    elif engine == "kokoro":
        import soundfile as sf
        tmp = out_wav.with_name(out_wav.stem + "_src.wav")     # not the same file as out_wav
        spec = voice or "af_heart"
        speed = float(cfg["KOKORO_SPEED"])
        m = re.search(r"@([\d.]+)", spec)
        speed *= float(m.group(1)) if m else 1.0
        m = re.search(r"\^([\d.]+)", spec)
        pitch = float(m.group(1)) if m else 1.0
        names = [n.strip() for n in re.split(r"[@^]", spec)[0].split("+") if n.strip()]
        k = kokoro_engine(cfg)
        style = names[0] if len(names) == 1 else sum(k.get_voice_style(n) for n in names) / len(names)
        samples, sr = k.create(text, voice=style, speed=speed, lang="en-gb" if names[0].startswith("b") else "en-us")
        sf.write(str(tmp), samples, sr)
        if abs(pitch - 1) > 0.005:            # a small pitch change makes similar voices easy to tell apart
            shifted = tmp.with_name(tmp.stem + "_p.wav")
            try:
                run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-af", f"rubberband=pitch={pitch}:formant=shifted",
                     str(shifted)])
                shifted.replace(tmp)
            except RuntimeError:
                shifted.unlink(missing_ok=True)
    elif engine == "espeak":
        tmp = out_wav.with_name(out_wav.stem + "_src.wav")     # not the same file as out_wav
        run([shutil.which("espeak-ng") or shutil.which("espeak"), "-s", "150", "-w", str(tmp), text])
    else:
        secs = max(1.2, len(text.split()) / 2.5)
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-t", f"{secs:.2f}", str(out_wav)])
        return
    to_wav(tmp, out_wav, rate)
    tmp.unlink(missing_ok=True)


def parse_pool(text):
    pool = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            pool[k.strip()] = [x.strip() for x in v.split(",") if x.strip()]
    return pool


def assign_voices(cfg, engine, cast, saved=None):
    """Give each speaking character a voice of the right type, all different where possible.
    `saved` (from an earlier episode) keeps recurring characters' voices the same."""
    saved = saved if saved and saved.get("engine") == engine else {"engine": engine, "voices": {}}
    pool = parse_pool(cfg.get(f"{engine.upper()}_VOICES", ""))
    narrator = cfg.get(DEFAULT_VOICE_KEY.get(engine, ""), "")
    used = {v["voice"] for v in saved["voices"].values()} | {narrator}
    out = dict(saved["voices"])
    for c in cast or []:
        name = (c.get("name") or "").strip()
        if not name or name.lower() in out or name.lower() == "narrator":
            continue
        vtype = c.get("voice") if c.get("voice") in VOICE_TYPES else "female"
        # borrow only within the same gender, so a man is always voiced by a man
        family = {"female": ["female", "old_woman", "girl"], "girl": ["girl", "female"],
                  "old_woman": ["old_woman", "female"], "male": ["male", "old_man", "boy"],
                  "boy": ["boy", "male"], "old_man": ["old_man", "male"],
                  "creature": ["creature", "male"]}[vtype]
        options = [v for ty in family for v in pool.get(ty, [])] or [narrator]
        fresh = [v for v in options if v not in used and v != narrator]
        voice = (fresh or [v for v in options if v != narrator] or options)[0]
        used.add(voice)
        if engine == "kokoro":                # a slightly different pace makes voices easier to tell apart
            pace = {"old_woman": 0.88, "old_man": 0.86, "girl": 1.05, "boy": 1.05, "creature": 0.9}.get(
                vtype, (0.94, 1.0, 0.97, 1.03)[len(out) % 4])
            voice = f"{voice}@{pace}"
        out[name.lower()] = {"voice": voice, "type": vtype, "name": name}
    return {"engine": engine, "voices": out}



def speak_line(cfg, text, wav, engine, vinfo):
    """Speak one line, falling back to the narrator voice and then to silence."""
    tries = [(vinfo["voice"], VOICE_STYLE.get(vinfo["type"])) if vinfo else (None, None), (None, None)]
    for voice, style in tries:
        try:
            narrate(cfg, text, wav, engine, voice, style)
            return
        except Exception as e:
            log(f"Voice line failed ({e}); trying {'the narrator voice' if voice else 'silence'}.")
    narrate(cfg, text, wav, "silent")




# ───────────────────────────── music ─────────────────────────────

# ───────────────────────────── audio production ─────────────────────────────
# record every line → match levels → lay out a timeline → design the sound (an AI reads each scene
# and picks a background and effects at exact moments) → fetch real recordings → mix (the background
# dips under the voice, never the other way round) → master to audiobook loudness.

def level_stats(path):
    """(rms_db, peak_db) of an audio file."""
    res = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "astats=measure_perchannel=none",
                          "-f", "null", "-"], capture_output=True, text=True)
    rms = re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", res.stderr)
    peak = re.findall(r"Peak level dB:\s*(-?[\d.]+|-inf)", res.stderr)
    f = lambda v: -90.0 if not v or v[-1] == "-inf" else float(v[-1])
    return f(rms), f(peak)


def level_match(src, dst, target_rms=-20.0, ceiling=-1.5, stereo=False):
    """Bring a voice line (or a sound) to the same loudness as everything else, without clipping."""
    rms, peak = level_stats(src)
    gain = 0.0 if rms <= -80 else max(-30.0, min(30.0, target_rms - rms))
    gain = min(gain, ceiling - peak) if peak > -80 else gain
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", f"volume={gain:.2f}dB",
         "-ar", "44100", "-ac", "2" if stereo else "1", str(dst)])


def silence(path, secs, channels=1):
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"anullsrc=r=44100:cl={'stereo' if channels == 2 else 'mono'}", "-t", f"{max(0.01, secs):.3f}", str(path)])


def record_voices(cfg, story, job, parts_dir, saved_voices=None, fresh_voices=False):
    """Every line in its own voice, level-matched, laid end to end with natural pauses.
    Returns (voice_wav, timeline, engine, voices). The timeline has each segment's and line's times."""
    engine = pick_tts(cfg)
    lines_ok = all(isinstance(s.get("lines"), list) and s["lines"] and
                   all(isinstance(l, dict) and str(l.get("text", "")).strip() for l in s["lines"])
                   for s in story["scenes"])
    multi = cfg["VOICES"] == "multi" and lines_ok and bool(story.get("cast"))
    picked = story.get("voice_choice") or {}
    if engine == "kokoro" and VOICE_SPEC.match(picked.get("narrator") or ""):
        cfg = dict(cfg, KOKORO_VOICE=picked["narrator"])       # the narrator you chose
    saved = None if fresh_voices else saved_voices
    voices = assign_voices(cfg, engine, story.get("cast"), saved) if multi else \
        (saved or {"engine": engine, "voices": {}})
    if multi and engine == "kokoro":
        for name, spec in (picked.get("characters") or {}).items():
            v = voices["voices"].get(name.lower())
            if v and VOICE_SPEC.match(spec or ""):
                v["voice"] = spec                                # a character voice you chose
    log(f"Narrator = {cfg.get(DEFAULT_VOICE_KEY.get(engine, ''), '') or 'default'}" +
        ("; " + ", ".join(f"{v['name']} = {v['voice']}" for v in voices["voices"].values()) if multi else ""))
    parts_dir.mkdir(parents=True, exist_ok=True)
    used = set()

    def part(name, text, vinfo):
        who = (vinfo or {}).get("voice") or "narrator:" + str(cfg.get(DEFAULT_VOICE_KEY.get(engine, ""), ""))
        spec = f"{engine}|{who}|{cfg.get('KOKORO_SPEED')}|{text}"
        key = hashlib.sha1(spec.encode()).hexdigest()[:16]
        lv = parts_dir / f"{key}.flac"
        used.add(lv.name)
        if not valid_media(lv):
            raw, wav = job / f"v_{key}.wav", job / f"lv_{key}.wav"
            speak_line(cfg, text, raw, engine, vinfo)
            level_match(raw, wav)
            run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "flac", str(lv)])
            raw.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
        return lv

    segs, n = [], len(story["scenes"])
    report("narration", 0, "Recording the title", now=story["title"])
    segs.append({"kind": "title", "parts": [(part("title", story["title"] + ".", None), "Narrator", story["title"])]})
    for i, sc in enumerate(story["scenes"], 1):
        report("narration", i / (n + 1), f"Recording the voices: scene {i} of {n}", now=sc["narration"][:320])
        parts = []
        if sc.get("chapter_title"):
            intro = f"Chapter {sc['chapter']}. {sc['chapter_title']}."
            parts.append((part(f"{i:03d}_ch", intro, None), "Narrator", intro))
        if multi:
            for k, ln in enumerate(sc["lines"]):
                who = str(ln.get("speaker", "")).strip() or "Narrator"
                parts.append((part(f"{i:03d}_{k:03d}", str(ln["text"]).strip(), voices["voices"].get(who.lower())),
                              who, str(ln["text"]).strip()))
        else:
            parts.append((part(f"{i:03d}_all", sc["narration"], None), "Narrator", sc["narration"]))
        segs.append({"kind": "scene", "index": i - 1, "chapter_start": bool(sc.get("chapter_title")), "parts": parts})

    # lay it out: short pauses between lines, longer between scenes, longest before a new chapter
    gaps = {}

    def gap(secs):
        key = f"{secs:.2f}"
        if key not in gaps:
            gaps[key] = job / f"gap_{key}.flac"
            if not valid_media(gaps[key]):
                silence(gaps[key], secs)
        return gaps[key]

    files, timeline, t_ = [], {"segments": []}, 0.0
    for s_, seg in enumerate(segs):
        lead = 1.6 if seg.get("chapter_start") and s_ > 1 else 0.0
        if lead:
            files.append(gap(lead))
        start = t_
        t_ += lead
        lines = []
        for k, (f_, who, text) in enumerate(seg["parts"]):
            d = duration(f_)
            lines.append({"start": round(t_, 3), "end": round(t_ + d, 3), "speaker": who, "text": text})
            files.append(f_)
            t_ += d
            if k < len(seg["parts"]) - 1:
                pause = 0.35 if who != seg["parts"][k + 1][1] else 0.2
                files.append(gap(pause))
                t_ += pause
        tail = 1.2 if seg["kind"] == "title" else 0.9
        files.append(gap(tail))
        t_ += tail
        timeline["segments"].append({"kind": seg["kind"], "index": seg.get("index"), "start": round(start, 3),
                                     "end": round(t_, 3), "lines": lines})
    timeline["total"] = round(t_, 3)
    for old in parts_dir.glob("*.flac"):                # lines that are no longer in the story
        if old.name not in used:
            old.unlink(missing_ok=True)
    lst = job / "voice_list.txt"
    lst.write_text("".join(f"file '{f_}'\n" for f_ in files))
    voice = job / "voice.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "1",
         str(voice)])
    log(f"Voices recorded with {engine}{' (character voices)' if multi else ''}: {t_ / 60:.1f} minutes")
    return voice, timeline, engine, voices


# ── sound design ──
SOUND_PROMPT = """You are the sound designer for an audiobook of the story "{title}" ({genre}, for {audience}).
For each scene below, choose:
- "ambience": the continuous background sound that fits WHERE the scene happens, written as a short
  search phrase for a sound-effects library, for example: "heavy rain on window", "ocean waves on beach",
  "busy city street traffic", "crickets at night countryside", "crackling fireplace", "quiet cafe
  chatter", "forest birds morning", "inside moving car", "office room tone", "quiet bedroom night".
  Use "none" if quiet fits better.
- "effects": up to {max_fx} short sound effects for specific moments, each with "sound" (a short search
  phrase like "door knock", "glass clinking", "thunder clap", "phone vibrating", "footsteps on wood")
  and "at" (5 to 10 words copied EXACTLY from the scene, where the sound should happen).
{rules}{wishes}
Scenes:
{scenes}

Reply with ONLY a JSON object, no markdown fences:
{{"scenes": [{{"scene": 1, "ambience": "...", "effects": [{{"sound": "...", "at": "..."}}]}}]}}"""

SOUND_RULES = {
    "explicit": ("This is an explicit adult story. For sex scenes pick a fitting bedroom ambience, and effects "
                 "such as \"female moaning\", \"male moaning\", \"heavy breathing\", \"bed creaking\" or \"kissing\" "
                 "placed at the matching moments."),
    "mature": "Romance may use effects like \"kissing\" or \"breathing\"; keep anything sexual off the page.",
    "young-kids": "Keep every sound gentle and friendly; nothing frightening.",
    "kids": "Keep every sound friendly; nothing frightening.",
}

AMBIENCE_FALLBACK = {   # when no writer is available: a background from the scene's own words
    "rain": (r"rain\w*|drizzle|downpour|thunder\w*|storm\w*", "rain ambience"),
    "ocean": (r"sea|ocean|waves?|beach|shore|harbou?r|tide|surf|pier|dock", "ocean waves beach"),
    "night": (r"crickets?|moonlit|countryside|porch at night", "crickets night ambience"),
    "forest": (r"forest|woods|trees|birds?|trail|meadow", "forest birds ambience"),
    "fire": (r"fireplace|hearth|campfire|embers", "crackling fireplace"),
    "city": (r"city|streets?|traffic|taxi|downtown|subway|sidewalks?", "city street traffic ambience"),
    "crowd": (r"caf[eé]|restaurant|bar|pub|party|diner|crowd", "restaurant crowd chatter ambience"),
    "bedroom": (r"bed|bedroom|sheets", "quiet bedroom night ambience"),
}


def plan_sounds(cfg, story, level, redo=False):
    """Ask the writer (as a sound designer) for each scene's background and effect cues. Saved in
    story.json, so remaking the audio reuses them."""
    todo = [i for i, sc in enumerate(story["scenes"]) if redo or "sound" not in sc]
    if not todo:
        return
    audience = level_info(level)[0]
    rules = SOUND_RULES.get(level, "")
    max_fx = int(cfg["MAX_EFFECTS"])
    use_ai = cfg["STORY_PROVIDER"].lower() != "demo"
    system = EXPLICIT_SYSTEM if level == "explicit" else None
    for b in range(0, len(todo), 4):
        batch = todo[b:b + 4]
        report("narration", 0.97, f"Designing the sound: scenes {batch[0] + 1}–{batch[-1] + 1} of {len(story['scenes'])}")
        plan = {}
        if use_ai:
            text = "\n\n".join(f"Scene {k + 1} ({story['scenes'][i].get('heading', '')}):\n"
                               f"{story['scenes'][i]['narration'][:1800]}" for k, i in enumerate(batch))
            try:
                snd = ((cfg.get("_STYLE") or {}).get("sounds") or {})
                wishes = ", ".join(x for x in (story.get("sound_wishes"), _clip(snd.get("like"), 300)) if x)
                wl = (f"\nThe listener especially wants to hear: {wishes}. Use these wherever they fit the scene, "
                      "as the background or as effects." if wishes else "")
                if snd.get("avoid"):
                    wl += f"\nNever use these sounds: {_clip(snd['avoid'], 200)}."
                r = ask_writer(cfg, SOUND_PROMPT.format(title=story["title"], genre=story.get("genre", ""),
                                                         audience=audience, max_fx=max_fx, rules=rules, scenes=text,
                                                         wishes=wl),
                               system=system)
                for item in r.get("scenes") or []:
                    k = int(item.get("scene", 0)) - 1
                    if 0 <= k < len(batch):
                        plan[batch[k]] = item
            except Exception as e:
                log(f"Sound design by the writer failed ({str(e)[:120]}); using the scenes' own words.")
        for i in batch:
            sc = story["scenes"][i]
            item = plan.get(i)
            if not item:
                words = sc["narration"].lower()
                best = max(AMBIENCE_FALLBACK.items(), key=lambda kv: len(re.findall(r"\b(?:" + kv[1][0] + r")\b", words)))
                hit = re.search(r"\b(?:" + best[1][0] + r")\b", words)
                if best[0] == "bedroom" and level not in ("mature", "explicit"):
                    hit = None
                item = {"ambience": best[1][1] if hit else "none", "effects": []}
            fx = [f_ for f_ in (item.get("effects") or []) if isinstance(f_, dict) and f_.get("sound")][:max_fx]
            sc["sound"] = {"ambience": str(item.get("ambience") or "none").strip()[:80],
                           "effects": [{"sound": str(f_["sound"])[:60], "at": str(f_.get("at", ""))[:120]} for f_ in fx]}


# ── finding real recordings: your files first, then Freesound (downloaded once, kept with credits) ──
GENERIC_WORDS = {"sound", "sounds", "ambience", "ambient", "background", "noise", "effect", "the", "and",
                 "with", "of", "on", "in", "at", "a", "an", "loop", "quiet", "soft", "distant"}


def _words(s):
    return [w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in GENERIC_WORDS and len(w) > 2]


def sound_library(cfg):
    lib = Path(os.path.expanduser(cfg["SOUNDS_DIR"])) / "library"
    lib.mkdir(parents=True, exist_ok=True)
    idx = lib / "index.json"
    data = json.loads(idx.read_text()) if idx.exists() else {}
    return lib, idx, data


def find_sound(cfg, query, kind, level):
    """A real recording for a search phrase. kind: 'ambience' (long, loopable) or 'effect' (short).
    Returns (path, credit) or (None, None)."""
    q = (query or "").strip().lower()
    if not q or q == "none":
        return None, None
    words = _words(q)
    adult_words = {"moan", "moaning", "moans", "sex", "orgasm", "climax", "groan", "groaning", "erotic"}
    if level not in ("explicit",) and adult_words & set(words):
        return None, None
    # 1) your own files, matched by name (rain-heavy.mp3, female-moaning-2.wav, door-knock.m4a…)
    folder = Path(os.path.expanduser(cfg["SOUNDS_DIR"]))
    best, best_score = None, 0
    if folder.is_dir():
        for f in folder.rglob("*"):
            if f.suffix.lower() not in AUDIO_EXTS or "library" in f.parts or "starter" in f.parts:
                continue
            fw = set(_words(f.stem.replace("-", " ").replace("_", " ")))
            score = len(fw & set(words))
            if score > best_score:
                best, best_score = f, score
    if best and best_score >= max(1, min(2, len(words))):
        return best, "your own recording"
    # 2) already downloaded
    lib, idx, data = sound_library(cfg)
    key = f"{kind}:{' '.join(words)}"
    if key in data and (lib / data[key]["file"]).exists():
        return lib / data[key]["file"], data[key]["credit"]
    # 3) Freesound
    token = cfg.get("FREESOUND_API_KEY", "")
    if not token:
        return None, None
    base = cfg.get("FREESOUND_URL", "https://freesound.org").rstrip("/")
    dur = "duration:[25 TO 1200]" if kind == "ambience" else "duration:[0.3 TO 12]"
    for attempt in (" ".join(words), " ".join(words[:2])):
        if not attempt:
            continue
        try:
            r = requests.get(f"{base}/apiv2/search/text/", timeout=30, params={
                "query": attempt, "token": token, "page_size": 15, "sort": "score",
                "filter": f'{dur} license:("Creative Commons 0" OR "Attribution")',
                "fields": "id,name,username,license,duration,previews,avg_rating,num_downloads"})
            if r.status_code == 401:
                log("Freesound says the API key isn't valid. Check FREESOUND_API_KEY.")
                return None, None
            r.raise_for_status()
            results = r.json().get("results") or []
        except Exception as e:
            log(f"Freesound search failed for '{attempt}' ({str(e)[:100]}).")
            return None, None
        def rank(pair):
            i, s = pair
            return ((s.get("num_downloads") or 0) + 1) ** 0.5 * ((s.get("avg_rating") or 2.5) + 0.5) / (1 + 0.2 * i)
        for _, s in sorted(enumerate(results), key=rank, reverse=True)[:3]:
            url = (s.get("previews") or {}).get("preview-hq-mp3") or (s.get("previews") or {}).get("preview-lq-mp3")
            if not url:
                continue
            name = f"{kind}-{slugify(' '.join(words))[:30]}-{s['id']}.mp3"
            try:
                with requests.get(url, timeout=120, stream=True) as dl:
                    dl.raise_for_status()
                    with open(lib / name, "wb") as out:
                        for chunk in dl.iter_content(1 << 16):
                            out.write(chunk)
                if not valid_media(lib / name):
                    raise RuntimeError("not a playable file")
            except Exception as e:
                log(f"Couldn't download a Freesound sound ({str(e)[:80]}).")
                (lib / name).unlink(missing_ok=True)
                continue
            lic = s.get("license", "")
            lic = "CC0" if "zero" in lic or "/publicdomain/" in lic else ("CC BY" if "/by/" in lic else lic)
            credit = f"\"{s.get('name', '')}\" by {s.get('username', '')} (freesound.org/s/{s['id']}, {lic})"
            data[key] = {"file": name, "credit": credit, "query": attempt}
            idx.write_text(json.dumps(data, indent=2))
            log(f"Downloaded sound for '{q}': {credit}")
            return lib / name, credit
    return None, None


def _anchor_time(seg, phrase):
    """When, in this scene, the words `phrase` are spoken (start of the line containing them)."""
    lines = seg["lines"]
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower())
    ph = norm(phrase)
    for n_words in (4, 2):                    # exact words first, then a looser match
        probe = " ".join(ph.split()[:n_words])
        if not probe:
            break
        for ln in lines:
            text = norm(ln["text"])
            pos = text.find(probe)
            if pos >= 0:
                frac = pos / max(1, len(text))
                return ln["start"] + frac * (ln["end"] - ln["start"])
    return (lines[0]["start"] if lines else seg["start"]) + 0.8      # no words given or found: early in the scene


def build_bed(cfg, story, timeline, job, level, music=None, with_sounds=True):
    """The background track for the whole story: each scene's ambience (looped, faded) plus its effects
    at the moments they belong, and optional music. Returns (bed_wav or None, credits)."""
    amb_gain = 10 ** ((-34 + 20 * math.log10(max(0.05, float(cfg["AMBIENCE_VOLUME"])))) / 20)
    credits, pieces, any_sound = [], [], False
    cache = {}

    def leveled(path, target):
        key = (str(path), target)
        if key not in cache:
            out = job / f"snd_{hashlib.sha1(str(key).encode()).hexdigest()[:10]}.wav"
            if not valid_media(out):
                level_match(path, out, target_rms=target, ceiling=-3, stereo=True)
            cache[key] = out
        return cache[key]

    segs = timeline["segments"]
    for n_, seg in enumerate(segs):
        dur = seg["end"] - seg["start"]
        out = job / f"bed_{n_:03d}.wav"
        if seg["kind"] != "scene":
            silence(out, dur, 2)
            pieces.append(out)
            continue
        sc = story["scenes"][seg["index"]]
        plan = (sc.get("sound") or {}) if with_sounds else {}
        inputs, chains, labels = ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"], [], ["[0:a]"]
        amb, credit = find_sound(cfg, plan.get("ambience"), "ambience", level)
        k = 1
        if amb:
            credits.append(credit)
            fade = min(2.0, dur / 4)
            inputs += ["-stream_loop", "-1", "-i", str(leveled(amb, -30))]
            chains.append(f"[{k}:a]atrim=0:{dur:.3f},asetpts=N/SR/TB,volume={amb_gain / 10 ** (-30 / 20):.4f},"
                          f"afade=t=in:d={fade:.2f},afade=t=out:st={max(0, dur - fade):.2f}:d={fade:.2f}[a{k}]")
            labels.append(f"[a{k}]")
            k += 1
        for fx in plan.get("effects") or []:
            at = _anchor_time(seg, fx.get("at", ""))
            if at is None:
                continue
            f_, credit = find_sound(cfg, fx.get("sound"), "effect", level)
            if not f_:
                continue
            credits.append(credit)
            delay = max(0, int((at - seg["start"]) * 1000))
            fx_gain = float(cfg["EFFECTS_VOLUME"])
            inputs += ["-i", str(leveled(f_, -26))]
            chains.append(f"[{k}:a]volume={fx_gain:.3f},adelay={delay}|{delay}[a{k}]")
            labels.append(f"[a{k}]")
            k += 1
        if k == 1:
            silence(out, dur, 2)
        else:
            any_sound = True
            graph = ";".join(chains) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[o]"
            run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]",
                 "-t", f"{dur:.3f}", "-ar", "44100", "-ac", "2", str(out)])
        pieces.append(out)
    if not any_sound and not music:
        return None, []
    lst = job / "bed_list.txt"
    lst.write_text("".join(f"file '{p_}'\n" for p_ in pieces))
    bed = job / "bed.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "2", str(bed)])
    if music:
        total = timeline["total"]
        mixed = job / "bed_music.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(bed), "-stream_loop", "-1", "-i", str(leveled(music, -32)),
             "-filter_complex", f"[1:a]atrim=0:{total:.3f},asetpts=N/SR/TB,volume={float(cfg['MUSIC_VOLUME']) / 0.18:.3f},"
                                f"afade=t=in:d=3,afade=t=out:st={max(0, total - 4):.2f}:d=4[m];"
                                f"[0:a][m]amix=inputs=2:duration=first:normalize=0[o]",
             "-map", "[o]", "-ar", "44100", "-ac", "2", str(mixed)])
        bed = mixed
        credits.append(f"Music: {music.name}")
    seen = []
    return bed, [c for c in credits if c and not (c in seen or seen.append(c))]


def mix_and_master(voice, bed, out_wav, job):
    """Voice on top; the background dips gently while someone speaks (the voice never dips). Then
    mastered to audiobook loudness (-18 LUFS, peaks under -3 dB) with a two-pass loudness match."""
    pre = job / "premaster.wav"
    if (_PROG.get("cfg") or {}).get("VOICE_POLISH", "on") != "off":
        # the voice chain an audiobook studio would use: cut low rumble, soften harsh "s" sounds, gently
        # even out loud and soft words, add a touch of warmth and presence, and catch any stray peaks
        polished = job / "voice_polished.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-af",
             "highpass=f=75,deesser=i=0.4:m=0.5:f=0.5,"
             "acompressor=threshold=-21dB:ratio=2.5:attack=8:release=180:knee=4:makeup=2,"
             "equalizer=f=180:t=q:w=1.2:g=1.5,equalizer=f=3500:t=q:w=1.5:g=1.5,"
             "alimiter=limit=0.89:level=false", "-ar", "44100", str(polished)])
        voice = polished
    if bed:
        graph = ("[0:a]aformat=sample_rates=44100:channel_layouts=stereo,asplit=2[v][key];"
                 "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[b];"
                 "[b][key]sidechaincompress=threshold=0.03:ratio=3:attack=30:release=500:knee=4[duck];"
                 "[v][duck]amix=inputs=2:duration=first:normalize=0[o]")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-i", str(bed), "-filter_complex", graph,
             "-map", "[o]", "-ar", "44100", str(pre)])
    else:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-ac", "2", "-ar", "44100", str(pre)])
    res = subprocess.run(["ffmpeg", "-v", "info", "-i", str(pre), "-af",
                          "loudnorm=I=-18:TP=-3:LRA=11:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr, re.S)
    ms = json.loads(m.group(0)) if m else None
    if ms and not re.match(r"^-?\d", str(ms.get("input_i", ""))) or (ms and float(ms["input_i"]) < -70):
        af = "anull"                                # (nearly) silent: nothing to master
    elif ms:
        af = (f"loudnorm=I=-18:TP=-3:LRA=11:measured_I={ms['input_i']}:measured_TP={ms['input_tp']}:"
              f"measured_LRA={ms['input_lra']}:measured_thresh={ms['input_thresh']}:offset={ms['target_offset']}:"
              f"linear=true,alimiter=limit=0.7:level=false")
    else:
        af = "loudnorm=I=-18:TP=-3:LRA=11,alimiter=limit=0.7:level=false"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-af", af, "-ar", "44100", str(out_wav)])


def produce_audio(cfg, story, job, out_dir, level, genre, music_choice, seed, saved_voices=None,
                  fresh_voices=False, redesign=False):
    """The whole audiobook. Writes story.mp3 (+ .m4b), subtitles, and stems for remaking later.
    Returns (final_wav, timeline, engine, voices, credits)."""
    stems = out_dir / ".stems"
    stems.mkdir(exist_ok=True)
    voice, timeline, engine, voices = record_voices(cfg, story, job, stems / "parts", saved_voices, fresh_voices)
    (stems / "timeline.json").write_text(json.dumps(timeline))
    (stems / "voices.json").write_text(json.dumps({"engine": engine, "voices": voices}))
    bed, credits = None, []
    music = pick_music(cfg, genre or "", music_choice, seed)
    if cfg["SOUNDSCAPES"].lower() == "on":
        plan_sounds(cfg, story, level, redo=redesign)
        report("narration", 0.98, "Finding the sounds")
        bed, credits = build_bed(cfg, story, timeline, job, level, music)
    elif music:
        bed, credits = build_bed(cfg, story, timeline, job, level, music, with_sounds=False)
    report("narration", 0.99, "Mixing and mastering")
    final = job / "final.wav"
    mix_and_master(voice, bed, final, job)
    audio_out = out_dir / "story.mp3"
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:a", "libmp3lame", "-b:a", "160k", str(audio_out)])
    except RuntimeError:
        audio_out = out_dir / "story.m4a"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-c:a", "aac", "-b:a", "160k", str(audio_out)])
    if cfg["SUBTITLES"] == "on":
        texts, spans = [], []
        for seg in timeline["segments"]:
            if seg["lines"]:
                texts.append(story["title"] if seg["kind"] == "title" else story["scenes"][seg["index"]]["narration"])
                a, b = seg["lines"][0]["start"], seg["lines"][-1]["end"]
                spans.append((a, b - a))
        write_subtitles(texts, spans, out_dir / "story.srt", out_dir / "story.vtt")
    credit_file = out_dir / "sound-credits.txt"
    if credits:
        credit_file.write_text("Sounds used in this story\n\n" + "\n".join(f"- {c}" for c in credits) + "\n")
    else:
        credit_file.unlink(missing_ok=True)
    return final, audio_out, timeline, engine, voices, credits


def slice_segments(final, timeline, job):
    """The finished audio cut at each segment, for building the video on the same timeline."""
    out = []
    for n_, seg in enumerate(timeline["segments"]):
        f_ = job / f"slice_{n_:03d}.wav"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{seg['start']:.3f}", "-t", f"{seg['end'] - seg['start']:.3f}",
             "-i", str(final), "-ar", "44100", "-ac", "2", str(f_)])
        out.append(f_)
    return out


def pick_music(cfg, genre, choice, seed):
    """choice: None (auto) | 'off' | part of a file name."""
    if cfg["MUSIC"] != "on" or (choice or "").lower() == "off":
        return None
    folder = Path(os.path.expanduser(cfg["MUSIC_DIR"]))
    tracks = sorted(p for p in folder.glob("**/*") if p.suffix.lower() in AUDIO_EXTS) if folder.exists() else []
    if not tracks:
        return None
    if choice:
        hits = [t for t in tracks if choice.lower() in t.stem.lower()]
        if hits:
            return hits[0]
        log(f"No music file matches '{choice}'; picking one instead.")
    words = [w for w in re.findall(r"[a-z]+", genre.lower()) if len(w) > 2]
    hits = [t for t in tracks if any(w in t.stem.lower() for w in words)]
    return random.Random(seed).choice(hits or tracks)



# ───────────────────────────── video assembly ─────────────────────────────

ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
AFMT = "aformat=sample_rates=44100:channel_layouts=stereo"


def still_segment(cfg, pic, cap, wav, seg, w, h, zoom_in=True, fps=30):
    secs = duration(wav) + 0.6
    frames = int(secs * fps)
    if cfg["MOTION"] == "on":
        z = "min(1+0.0009*on,1.12)" if zoom_in else "max(1.12-0.0009*on,1)"
        bg = (f"[0]scale={w * 2}:{h * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s={w}x{h}:fps={fps}[bg]")
        inputs = ["-i", str(pic)]
    else:
        bg = f"[0]scale={w}:{h},fps={fps}[bg]"
        inputs = ["-loop", "1", "-t", f"{secs:.2f}", "-i", str(pic)]
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-i", str(cap), "-i", str(wav),
         "-filter_complex", f"{bg};[bg][1]overlay=0:0,format=yuv420p[v];[2]apad,{AFMT}[a]",
         "-map", "[v]", "-map", "[a]", "-t", f"{secs:.2f}", *ENC, str(seg)])


def clip_segment(cfg, clip, cap, wav, seg, w, h):
    clip_len, voice_len = duration(clip), duration(wav)
    secs = max(clip_len, voice_len + 0.6)
    hold = max(0.0, secs - clip_len) + 0.2
    vid = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,"
           f"tpad=stop_mode=clone:stop_duration={hold:.2f}[bg];[bg][1]overlay=0:0,format=yuv420p[v]")
    vol = float(cfg["CLIP_SOUND_VOLUME"])
    if has_audio(clip) and vol > 0:
        aud = (f";[0:a]{AFMT},volume={vol},apad[ca];[2:a]{AFMT},apad[na];"
               f"[ca][na]amix=inputs=2:duration=longest:normalize=0[a]")
    else:
        aud = f";[2:a]{AFMT},apad[a]"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-i", str(cap), "-i", str(wav),
         "-filter_complex", vid + aud, "-map", "[v]", "-map", "[a]", "-t", f"{secs:.2f}", *ENC, str(seg)])


def make_video(cfg, story, wavs, job, work, w, h, portrait, out_path, vprovider, ref):
    scenes = story["scenes"]
    if vprovider == "existing":                # remaking: only clips already made, pictures for the rest
        clips = [job / f"clip_{i + 1:02d}.mp4" if valid_media(job / f"clip_{i + 1:02d}.mp4") else None
                 for i in range(len(scenes))]
    else:
        clips = make_clips(cfg, story, job, portrait, vprovider, ref) if vprovider in ("omni", "veo", "demo") \
            else [None] * len(scenes)
    pics = make_pictures(cfg, story, job, w, h, portrait, [i for i, c in enumerate(clips) if c is None], ref)

    first_clip = next((c for c in clips if c), None)
    if first_clip:
        title_pic = work / "title.png"
        run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(first_clip), "-frames:v", "1", str(title_pic)])
        fit_cover(Image.open(title_pic), w, h).save(title_pic)
    else:
        title_pic = pics[0]

    log("Building the video…")
    segs = []
    cap = work / "cap_00.png"
    caption_overlay(story["title"], w, h, cap)
    segs.append(work / "seg_00.mp4")
    still_segment(cfg, title_pic, cap, wavs[0], segs[-1], w, h)
    for i, sc in enumerate(scenes):
        cap = work / f"cap_{i + 1:02d}.png"
        caption_overlay(sc["narration"] if cfg["CAPTIONS"] == "on" else "", w, h, cap)
        segs.append(work / f"seg_{i + 1:02d}.mp4")
        if clips[i]:
            clip_segment(cfg, clips[i], cap, wavs[i + 1], segs[-1], w, h)
        else:
            still_segment(cfg, pics[i], cap, wavs[i + 1], segs[-1], w, h, zoom_in=i % 2 == 1)
        log(f"Video part {i + 1}/{len(scenes)} done")
        report("video", 0.80 + 0.20 * (i + 1) / len(scenes), f"Putting the video together: part {i + 1} of {len(scenes)}")
    lst = work / "video_list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segs))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-movflags", "+faststart", str(out_path)])
    return clips, pics, [duration(sg) for sg in segs]


# ───────────────────────────── cost estimate ─────────────────────────────

def pick_video_provider(cfg):
    p = cfg["VIDEO_PROVIDER"].lower()
    if p == "auto":
        return "omni" if cfg["GEMINI_API_KEY"] else "slideshow"
    return p


def estimate(cfg, mode, scenes, vprovider, book=False, writes=1, words_per_scene=None):
    f = lambda k: float(cfg.get(k) or 0)
    clip_mode = mode == "video" and vprovider in ("omni", "veo", "demo")
    items = []
    sp = cfg["STORY_PROVIDER"].lower()
    if sp != "demo":
        items.append(("story writing" if writes == 1 else f"story writing ({writes - 1} chapters)",
                      writes * f(f"PRICE_STORY_{sp.upper()}")))
    if mode == "video":
        if vprovider in ("omni", "veo"):
            per = f("PRICE_OMNI_PER_SEC") if vprovider == "omni" else f("PRICE_VEO_PER_SEC")
            items.append((f"{scenes} video clip{'s' * (scenes != 1)}", scenes * 8 * per))
        elif vprovider == "slideshow" and pick_image_provider(cfg) in ("openai", "gemini"):
            items.append((f"{scenes} pictures", scenes * f("PRICE_IMAGE")))
        if ref_provider(cfg) in ("openai", "gemini"):
            items.append(("character sheet", f("PRICE_IMAGE")))
    elif book and pick_image_provider(cfg) in ("openai", "gemini"):
        items.append((f"{scenes} book pictures", scenes * f("PRICE_IMAGE")))
        if ref_provider(cfg) == "gemini":
            items.append(("character sheet", f("PRICE_IMAGE")))
    if mode in ("audio", "video"):
        engine = pick_tts(cfg)
        words = scenes * (24 if clip_mode else words_per_scene or 85) + 8
        price = f(f"PRICE_VOICE_PER_MIN_{engine.upper()}") if engine in ("openai", "gemini", "elevenlabs") else 0
        if price:
            items.append((f"narration ({engine})", words / 150 * price))
    total = round(sum(v for _, v in items), 2)
    parts = ", ".join(f"{k} ${v:.2f}" for k, v in items if v >= 0.005)
    summary = f"About ${total:.2f}" + (f": {parts}" if parts else " (free settings)")
    return {"estimate_usd": total, "breakdown": {k: round(v, 3) for k, v in items}, "summary": summary,
            "video_engine": vprovider if mode == "video" else "none", "scenes": scenes, "mode": mode}


# ───────────────────────────── notifications & status ─────────────────────────────

def notify(cfg, title, message, click=None, failed=False):
    if cfg["NTFY_TOPIC"]:
        try:
            body = {"topic": cfg["NTFY_TOPIC"], "title": title, "message": message,
                    "tags": ["warning"] if failed else ["clapper"]}
            if click:
                body["click"] = click
                body["actions"] = [{"action": "view", "label": "Open", "url": click}]
            hdr = {"Authorization": f"Bearer {cfg['NTFY_TOKEN']}"} if cfg["NTFY_TOKEN"] else {}
            requests.post(cfg["NTFY_SERVER"].rstrip("/"), json=body, headers=hdr, timeout=20)
        except Exception as e:
            log(f"Push notification failed: {e}")
    if IS_MAC:
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                       capture_output=True)


def jobs_dir(cfg):
    d = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / ".jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_status(cfg, job_id, **data):
    p = jobs_dir(cfg) / f"{job_id}.status.json"
    data = {"job_id": job_id, "updated": dt.datetime.now().isoformat(timespec="seconds"), **data}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(p)                                  # the web page never reads a half-written file


# Progress for the web page: an overall percentage, what's happening now, and a preview
# (the title and opening once written, then each picture or clip as it's made).
_PROG = {}

STEP_WEIGHTS = {
    "story": [("writing", 100)],
    "audio": [("writing", 35), ("narration", 55), ("finishing", 10)],
    "video": [("writing", 8), ("narration", 12), ("characters", 5), ("video", 68), ("finishing", 7)],
}


def progress_start(cfg, job_id, job, mode, book):
    steps = list(STEP_WEIGHTS[mode])
    if book and mode != "video":
        steps.insert(len(steps) - (mode == "audio"), ("characters", 5))
        steps.insert(len(steps) - (mode == "audio"), ("book", 30))
    elif mode == "video" and book:
        steps.insert(-1, ("book", 4))
    _PROG.clear()
    _PROG.update(cfg=cfg, job_id=job_id, job=job, steps=steps, lock=threading.Lock(), status={"progress": 0})


def preview_image(src, name):
    """A small JPEG of a picture or a frame of a clip, kept in the job folder for the web page."""
    job = _PROG["job"]
    out = job / f"preview_{name}.jpg"
    try:
        if Path(src).suffix == ".mp4":
            run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(2.0, duration(src) / 2):.2f}", "-i", str(src),
                 "-frames:v", "1", "-vf", "scale=640:-2", str(out)])
        else:
            im = Image.open(src).convert("RGB")
            im.thumbnail((640, 640))
            im.save(out, quality=82)
        return out.name
    except Exception:
        return None


def report(step, frac=0.0, detail=None, image=None, **extra):
    if not _PROG or "steps" not in _PROG:          # (Audio Studio uses the helpers without a StoryMaker job)
        return
    with _PROG["lock"]:
        steps, total = _PROG["steps"], sum(w_ for _, w_ in _PROG["steps"])
        done, here = 0, 0
        for name, w_ in steps:
            if name == step:
                here = w_
                break
            done += w_
        st = _PROG["status"]
        pct = round(100 * (done + here * max(0.0, min(1.0, frac))) / total)
        st["progress"] = max(st.get("progress", 0), min(99, pct))
        st.update(state="running", step=step, **extra)
        if detail:
            st["detail"] = detail
        if image:
            name = preview_image(image, Path(image).stem)
            if name:
                imgs = [n for n in st.get("images", []) if n != name] + [name]
                st["images"], st["image"] = imgs[-12:], name
        write_status(_PROG["cfg"], _PROG["job_id"], **st)


class Ticker:
    """Moves the bar along gently during one long wait (like the writer thinking)."""
    def __init__(self, step, detail, expected_s):
        self.step, self.detail, self.expected, self.stop = step, detail, expected_s, threading.Event()

    def __enter__(self):
        def tick():
            t0 = time.time()
            while not self.stop.wait(2):
                report(self.step, 0.92 * (1 - 2.718 ** (-(time.time() - t0) / self.expected)))
        threading.Thread(target=tick, daemon=True).start()
        report(self.step, 0, self.detail)
        return self

    def __exit__(self, *a):
        self.stop.set()


# ───────────────────────────── subtitles & thumbnail ─────────────────────────────

def caption_chunks(text, max_chars=84):
    """Split narration into subtitle-sized pieces (about two lines each): sentence by sentence,
    and long sentences into evenly sized parts so no tiny leftover piece flashes by."""
    pieces = []
    for sent in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if not sent:
            continue
        n = -(-len(sent) // max_chars)                 # parts needed, rounded up
        target = len(sent) / n
        cur = ""
        for wd in sent.split():
            if cur and len(cur) >= target and len(pieces) < 10_000 and n > 1:
                pieces.append(cur)
                cur, n = wd, n - 1
                continue
            cur = (cur + " " + wd).strip()
        if cur:
            pieces.append(cur)
    return pieces or [text]


def stamp(t, vtt=False):
    t = max(0.0, t)
    h, rem = divmod(int(round(t * 1000)), 3600_000)
    m, rem = divmod(rem, 60_000)
    sec, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}{'.' if vtt else ','}{ms:03d}"


def wrap_two(text, width=42):
    return "\n".join(textwrap.wrap(text, width)[:3])


def write_subtitles(texts, timeline, srt_path, vtt_path):
    """texts[i] is spoken from timeline[i] = (start, length). Each is split into timed pieces."""
    cues = []
    for text, (start, length) in zip(texts, timeline):
        pieces = caption_chunks(text)
        total = sum(len(p_) for p_ in pieces) or 1
        t = start
        for p_ in pieces:
            d_ = max(1.0, length * len(p_) / total)
            cues.append((t, min(t + d_, start + length), p_))
            t += d_
    srt = "".join(f"{i + 1}\n{stamp(a)} --> {stamp(b)}\n{wrap_two(c)}\n\n" for i, (a, b, c) in enumerate(cues))
    vtt = "WEBVTT\n\n" + "".join(f"{stamp(a, True)} --> {stamp(b, True)}\n{wrap_two(c)}\n\n" for a, b, c in cues)
    Path(srt_path).write_text(srt)
    Path(vtt_path).write_text(vtt)


def make_thumbnail(out_dir, sources):
    for src in sources:
        if src and Path(src).exists():
            try:
                if Path(src).suffix == ".mp4":
                    run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(src), "-frames:v", "1",
                         "-vf", "scale=640:-2", str(out_dir / "thumb.jpg")])
                else:
                    img = Image.open(src).convert("RGB")
                    img.thumbnail((640, 640))
                    img.save(out_dir / "thumb.jpg", quality=85)
                return
            except Exception:
                continue


# ───────────────────────────── series ─────────────────────────────

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "story"


def series_root(cfg):
    d = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / "series"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_series(cfg, name=None):
    """name=None → the most recent series. Otherwise match by name (exact slug, then partial)."""
    root = series_root(cfg)
    if not name:
        latest = root / "latest.txt"
        slug = latest.read_text().strip() if latest.exists() else ""
        f = root / slug / "series.json"
        return json.loads(f.read_text()) if slug and f.exists() else None
    all_series = [json.loads(f.read_text()) for f in root.glob("*/series.json")]
    for s_ in all_series:
        if s_["slug"] == slugify(name):
            return s_
    for s_ in all_series:
        if name.lower() in s_["name"].lower():
            return s_
    return None


def done_episodes(series):
    """Episodes that finished completely (a partly-finished one is resumed, not counted)."""
    return [e for e in (series or {}).get("episodes", []) if e.get("complete", True)]


def list_series(cfg):
    out = []
    for f in sorted(series_root(cfg).glob("*/series.json")):
        s_ = json.loads(f.read_text())
        out.append({"name": s_["name"], "episodes": len(s_["episodes"]), "level": s_.get("level", ""),
                    "last": s_["episodes"][-1]["title"] if s_["episodes"] else ""})
    return out


def save_episode(cfg, series, story, result, voices, out_dir, level, genre, new_name=None,
                 job_id="", complete=True):
    root = series_root(cfg)
    if series is None:
        name = new_name or story["title"]
        series = {"name": name, "slug": slugify(name), "visual_style": story.get("visual_style", ""),
                  "characters": story.get("characters", ""), "cast": story.get("cast", []),
                  "level": level, "genre": genre, "voices": voices, "episodes": []}
    else:
        known = {c.get("name", "").lower() for c in series.get("cast", [])}
        series["cast"] = series.get("cast", []) + [c for c in story.get("cast", []) if c.get("name", "").lower() not in known]
        apply_locked_cast({"cast": series["cast"]}, [c for c in story.get("cast", []) if c.get("confirmed")])
        if voices and voices.get("voices"):
            series["voices"] = voices
    series["episodes"] = [e for e in series["episodes"] if not job_id or e.get("job_id") != job_id]
    series["episodes"].append({"title": story["title"], "logline": story.get("logline", ""),
                               "folder": str(out_dir), "date": f"{dt.date.today()}",
                               "job_id": job_id, "complete": complete})
    sdir = root / series["slug"]
    sdir.mkdir(parents=True, exist_ok=True)
    ref = out_dir / "character_ref.png"
    if ref.exists() and not (sdir / "character_ref.png").exists():
        shutil.copy(ref, sdir / "character_ref.png")
    (sdir / "series.json").write_text(json.dumps(series, indent=2))
    (root / "latest.txt").write_text(series["slug"])
    return series


# ───────────────────────────── picture book ─────────────────────────────

def serif_font(size, italic=False, bold=False):
    names = (["Georgia Italic.ttf", "DejaVuSerif-Italic.ttf"] if italic else
             ["Georgia Bold.ttf", "DejaVuSerif-Bold.ttf"] if bold else ["Georgia.ttf", "DejaVuSerif.ttf"])
    for folder in ("/System/Library/Fonts/Supplemental", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu"):
        for n in names:
            pth = Path(folder) / n
            if pth.exists():
                return ImageFont.truetype(str(pth), size)
    return find_font(size, bold=bold)


def wrap_px(draw, text, font, max_w):
    lines, cur = [], ""
    for wd in text.split():
        test = (cur + " " + wd).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def text_block(draw, text, box, size, italic=False, color=(40, 34, 28), min_size=26):
    """Centered, wrapped text that shrinks until it fits inside box=(x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    while True:
        font = serif_font(size, italic=italic)
        lines = wrap_px(draw, text, font, x1 - x0)
        lh = int(size * 1.45)
        if len(lines) * lh <= (y1 - y0) or size <= min_size:
            break
        size -= 2
    y = y0 + max(0, ((y1 - y0) - len(lines) * lh) // 2)
    for ln in lines:
        draw.text((x0 + ((x1 - x0) - draw.textlength(ln, font=font)) / 2, y), ln, font=font, fill=color)
        y += lh


def make_book(story, scene_imgs, cover_img, out_pdf, subtitle=""):
    W, H, M = 2200, 1700, 110                      # US Letter landscape at 200 dpi
    paper, ink = (252, 248, 240), (40, 34, 28)
    pages = []

    cover = Image.new("RGB", (W, H), paper)
    d = ImageDraw.Draw(cover)
    if cover_img:
        art = fit_cover(Image.open(cover_img), W - 2 * M, int(H * 0.52))
        cover.paste(art, (M, M))
    text_block(d, story["title"], (M, int(H * 0.52) + M + 30, W - M, int(H * 0.52) + M + 260), 104)
    if story.get("logline"):
        text_block(d, story["logline"], (M + 150, int(H * 0.52) + M + 270, W - M - 150, H - M - 90), 46, italic=True)
    if subtitle:
        font = serif_font(34)
        d.text(((W - d.textlength(subtitle, font=font)) / 2, H - M - 40), subtitle, font=font, fill=(120, 105, 90))
    pages.append(cover)

    for i, (sc, img) in enumerate(zip(story["scenes"], scene_imgs)):
        page = Image.new("RGB", (W, H), paper)
        d = ImageDraw.Draw(page)
        pic_h = int(H * 0.60)
        if img:
            page.paste(fit_cover(Image.open(img), W - 2 * M, pic_h), (M, M))
        text_block(d, sc["narration"], (M + 120, M + pic_h + 50, W - M - 120, H - M - 60), 50, color=ink)
        num = str(i + 1)
        font = serif_font(30)
        d.text(((W - d.textlength(num, font=font)) / 2, H - M + 10), num, font=font, fill=(150, 135, 120))
        pages.append(page)

    end = Image.new("RGB", (W, H), paper)
    text_block(ImageDraw.Draw(end), "The End", (M, 0, W - M, H), 120, italic=True)
    pages.append(end)
    pages[0].save(out_pdf, "PDF", resolution=200, save_all=True, append_images=pages[1:])


def make_text_pdf(story, out_pdf, subtitle=""):
    """A plain reading copy: title page, then the story flowing over US Letter portrait pages."""
    W, H, M = 1700, 2200, 190                      # US Letter portrait at 200 dpi
    paper, ink, faint = (255, 255, 255), (30, 28, 26), (130, 120, 110)
    size, lh = 42, 70
    font, pages = serif_font(size), []

    cover = Image.new("RGB", (W, H), paper)
    d = ImageDraw.Draw(cover)
    text_block(d, story["title"], (M, int(H * 0.30), W - M, int(H * 0.48)), 96, color=ink)
    if story.get("logline"):
        text_block(d, story["logline"], (M + 80, int(H * 0.50), W - M - 80, int(H * 0.64)), 44, italic=True, color=faint)
    if subtitle:
        f2 = serif_font(34)
        d.text(((W - d.textlength(subtitle, font=f2)) / 2, H - M), subtitle, font=f2, fill=faint)
    pages.append(cover)

    # (kind, text): chapter headings start a new page; paragraphs flow; "The End" closes
    items = []
    if story.get("cast"):
        items.append(("castlist", "Characters", ""))
        items += [("castlist", f"{c['name']}: {TYPE_WORDS.get(c.get('voice'), 'a woman')}", "line")
                  for c in story["cast"] if c.get("name")]
        items.append(("castlist", "", "gap"))
    for sc in story["scenes"]:
        if sc.get("chapter_title"):
            items.append(("chapter", f"Chapter {sc['chapter']}", sc["chapter_title"]))
        items += [("para", p_, "") for p_ in paragraphs(sc.get("narration", ""))]
    items.append(("end", "The End", ""))
    page, d, y = None, None, H
    f3 = serif_font(30)

    def new_page():
        nonlocal page, d, y
        page = Image.new("RGB", (W, H), paper)
        d = ImageDraw.Draw(page)
        pages.append(page)
        num = str(len(pages) - 1)
        d.text(((W - d.textlength(num, font=f3)) / 2, H - M + 70), num, font=f3, fill=faint)
        y = M

    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for kind, a, b in items:
        if kind == "castlist":
            if page is None:
                new_page()
            if b == "gap":
                y += lh
                continue
            f = serif_font(34, italic=True) if b == "" else serif_font(36)
            d.text(((W - d.textlength(a, font=f)) / 2, y), a, font=f, fill=faint if b == "" else ink)
            y += 58 if b == "" else 52
            continue
        if kind == "chapter":
            new_page()
            y += 160
            fl, ft = serif_font(34, italic=True), serif_font(64, bold=True)
            d.text(((W - d.textlength(a, font=fl)) / 2, y), a, font=fl, fill=faint)
            y += 70
            for ln in wrap_px(measure, b, ft, W - 2 * M):
                d.text(((W - d.textlength(ln, font=ft)) / 2, y), ln, font=ft, fill=ink)
                y += 84
            y += 90
            continue
        f = serif_font(size, italic=True) if kind == "end" else font
        lines = [a] if kind == "end" else wrap_px(measure, a, f, W - 2 * M - 40)
        if kind == "end":
            y += lh
        for n_, ln in enumerate(lines):
            if page is None or y + lh > H - M:
                new_page()
            if kind == "end":
                x = (W - d.textlength(ln, font=f)) / 2
            else:
                x = M + (40 if n_ == 0 else 0)          # first-line indent, like a printed book
            d.text((x, y), ln, font=f, fill=ink)
            y += lh
        y += int(lh * 0.35)
    pages[0].save(out_pdf, "PDF", resolution=200, save_all=True, append_images=pages[1:])


def story_folder(cfg, run_id):
    """A finished story's folder, only if run_id is a plain folder name inside OUTPUT_DIR."""
    root = Path(os.path.expanduser(cfg["OUTPUT_DIR"])).resolve()
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith(".") or run_id == "series":
        raise ValueError("Unknown story.")
    folder = (root / run_id).resolve()
    if folder.parent != root or not (folder / "result.json").exists():
        raise ValueError("Unknown story.")
    return folder


def export_pdf(cfg, run_id):
    folder = story_folder(cfg, run_id)
    out = folder / "story.pdf"
    if not out.exists():
        story = json.loads((folder / "story.json").read_text())
        result = json.loads((folder / "result.json").read_text())
        sub = f"Episode {result['episode']}" if result.get("episode", 1) > 1 else ""
        make_text_pdf(story, out, sub)
    return {"ok": True, "pdf": str(out), "book": str(folder / "story-book.pdf")
            if (folder / "story-book.pdf").exists() else None}


def delete_story(cfg, run_id):
    """Remove a finished story's folder and its place in its series (an empty series goes too)."""
    folder = story_folder(cfg, run_id)
    shutil.rmtree(folder)
    root = series_root(cfg)
    for f in root.glob("*/series.json"):
        s_ = json.loads(f.read_text())
        keep = [e for e in s_["episodes"] if Path(e.get("folder", "")).name != run_id]
        if len(keep) == len(s_["episodes"]):
            continue
        if keep:
            s_["episodes"] = keep
            f.write_text(json.dumps(s_, indent=2))
        else:
            shutil.rmtree(f.parent)
            latest = root / "latest.txt"
            if latest.exists() and latest.read_text().strip() == s_["slug"]:
                others = sorted(root.glob("*/series.json"), key=lambda x: x.stat().st_mtime)
                latest.write_text(others[-1].parent.name if others else "")
    return {"ok": True, "deleted": run_id}


def adult_config(cfg):
    """Explicit stories: writer and voice stay on this computer, no pictures."""
    if cfg["STORY_PROVIDER"] != "demo":
        cfg["STORY_PROVIDER"] = "ollama"
        cfg["OLLAMA_MODEL"] = cfg["OLLAMA_ADULT_MODEL"] or cfg["OLLAMA_MODEL"]
    if cfg["TTS_PROVIDER"].lower() not in ("kokoro", "say", "espeak", "silent"):
        cfg["TTS_PROVIDER"] = local_tts()          # never an online voice for explicit text
    cfg["_ADULT_ONLY"] = "1"
    # pictures only from your own computer, and only tasteful ones; video = moving pictures
    cfg["IMAGE_PROVIDER"] = "local" if cfg.get("LOCAL_IMAGE_URL") else "cards"
    cfg["CHARACTER_REF"] = "off"
    cfg["VIDEO_PROVIDER"] = "slideshow"


def load_story(cfg, run_id):
    folder = story_folder(cfg, run_id)
    return folder, json.loads((folder / "story.json").read_text()), json.loads((folder / "result.json").read_text())


def save_story_text(folder, story, result, changed):
    """After an edit or rewrite: rebuild voices' lines, the text files and mark the audio as outdated."""
    cast = story.get("cast") or []
    for i in changed:
        sc = story["scenes"][i]
        sc["narration"] = clean_prose(sc["narration"])
        sc["lines"] = lines_from_prose(sc["narration"], cast)
        sc.pop("sound", None)                      # the sound plan is made again for new text
    complete_cast(story)
    sub = f"{result['series']} — Episode {result['episode']}" if result.get("episode", 1) > 1 else ""
    result["story"] = story_markdown(story, sub)
    result["title"] = story["title"]
    (folder / "story.json").write_text(json.dumps(story, indent=2))
    (folder / "story.md").write_text(result["story"])
    (folder / "story.pdf").unlink(missing_ok=True)
    if result.get("audio_file") or result.get("video_file"):
        result["audio_outdated"] = True
    result["edited"] = dt.datetime.now().isoformat(timespec="seconds")
    (folder / "result.json").write_text(json.dumps(result, indent=2))


def edit_story(cfg, run_id, payload):
    """payload: {"title": "...", "scenes": {"0": "new text", ...}} (scene numbers count from 0)."""
    folder, story, result = load_story(cfg, run_id)
    changed = []
    for k, text in (payload.get("scenes") or {}).items():
        i = int(k)
        if 0 <= i < len(story["scenes"]) and isinstance(text, str) and text.strip() \
                and text.strip() != story["scenes"][i]["narration"].strip():
            story["scenes"][i]["narration"] = text.strip()
            changed.append(i)
    if isinstance(payload.get("title"), str) and payload["title"].strip():
        story["title"] = payload["title"].strip()[:120]
    if result.get("level") == "explicit":
        check_adult_only(" ".join([story["title"]] + [story["scenes"][i]["narration"] for i in changed]))
    save_story_text(folder, story, result, changed)
    if isinstance(payload.get("characters"), str):          # the Characters list at the top: who is who, locked
        tags = parse_characters(payload["characters"])
        if result.get("level") == "explicit":
            tags = [(n, {"girl": "female", "boy": "male"}.get(t, t)) for n, t in tags]
        if tags:
            set_cast(cfg, run_id, {"cast": [{"name": n, "voice": t} for n, t in tags]})
            folder, story, result = load_story(cfg, run_id)
    return {"ok": True, "changed": len(changed), "audio_outdated": bool(result.get("audio_outdated"))}


def set_voices(cfg, run_id, payload):
    """payload: {"narrator": "af_heart", "characters": {"Mira": "bf_emma^1.03"}} ("" = automatic)."""
    folder, story, result = load_story(cfg, run_id)
    names = {c["name"].lower(): c["name"] for c in story.get("cast") or [] if c.get("name")}
    chosen = {"narrator": "", "characters": {}}
    if VOICE_SPEC.match(payload.get("narrator") or ""):
        chosen["narrator"] = payload["narrator"]
    for name, spec in (payload.get("characters") or {}).items():
        if name.lower() in names and VOICE_SPEC.match(spec or ""):
            chosen["characters"][names[name.lower()]] = spec
    story["voice_choice"] = chosen
    (folder / "story.json").write_text(json.dumps(story, indent=2))
    if result.get("audio_file"):
        result["audio_outdated"] = True
        (folder / "result.json").write_text(json.dumps(result, indent=2))
    return {"ok": True}


def set_cast(cfg, run_id, payload):
    """payload: {"cast": [{"name": "Mira", "voice": "female"}, ...]}: tags who is a woman, man, girl, boy…
    Tagged characters are locked: the voices, the Audio Studio and later episodes all follow them."""
    folder, story, result = load_story(cfg, run_id)
    by_name = {c["name"].lower(): c for c in story.get("cast") or [] if c.get("name")}
    changed = False
    for item in payload.get("cast") or []:
        name, vtype = str(item.get("name", "")).strip()[:60], item.get("voice")
        if vtype in VOICE_TYPES and name and name.lower() not in by_name and name.lower() != "narrator":
            c = {"name": name, "voice": vtype, "confirmed": True}      # someone the Audio Studio found
            story.setdefault("cast", []).append(c)
            by_name[name.lower()] = c
            changed = True
            continue
        if name.lower() in by_name and vtype in VOICE_TYPES:
            c = by_name[name.lower()]
            if c.get("voice") != vtype:
                changed = True
                c["voice"] = vtype
                vc = story.get("voice_choice") or {}
                (vc.get("characters") or {}).pop(c["name"], None)   # a picked voice of the old type no longer fits
            c["confirmed"] = True
    (folder / "story.json").write_text(json.dumps(story, indent=2))
    for f in series_root(cfg).glob("*/series.json"):     # later episodes of the series follow the tags too
        s_ = json.loads(f.read_text())
        if any(Path(e.get("folder", "")).name == run_id for e in s_.get("episodes", [])):
            known = {c.get("name", "").lower() for c in s_.get("cast", [])}
            s_["cast"] = s_.get("cast", []) + [dict(c) for c in story["cast"] if c["name"].lower() not in known]
            apply_locked_cast(s_, story["cast"])
            sv = (s_.get("voices") or {}).get("voices") or {}
            for c in story["cast"]:
                if sv.get(c["name"].lower(), {}).get("type") not in (None, c["voice"]):
                    sv.pop(c["name"].lower(), None)
            f.write_text(json.dumps(s_, indent=2))
    stems = folder / ".stems" / "voices.json"
    if stems.exists():                                   # voices are chosen again for changed types
        vj = json.loads(stems.read_text())
        vv = (vj.get("voices") or {}).get("voices") or {}
        for c in story["cast"]:
            v = vv.get(c["name"].lower())
            if v and v.get("type") != c["voice"]:
                vv.pop(c["name"].lower(), None)
        stems.write_text(json.dumps(vj))
    if changed and result.get("audio_file"):
        result["audio_outdated"] = True
    sub = f"{result['series']} — Episode {result['episode']}" if result.get("episode", 1) > 1 else ""
    result["story"] = story_markdown(story, sub)          # the Characters list at the top of the text
    (folder / "story.md").write_text(result["story"])
    (folder / "story.pdf").unlink(missing_ok=True)
    (folder / "result.json").write_text(json.dumps(result, indent=2))
    return {"ok": True, "cast": story["cast"]}


def set_sounds(cfg, run_id, payload):
    """payload: {"wishes": "rain, thunder", "scenes": {"0": {"ambience": "...", "effects": [{"sound", "at"}]}}}"""
    folder, story, result = load_story(cfg, run_id)
    if isinstance(payload.get("wishes"), str):
        story["sound_wishes"] = payload["wishes"].strip()[:300]
    for k, plan in (payload.get("scenes") or {}).items():
        i = int(k)
        if not (0 <= i < len(story["scenes"])) or not isinstance(plan, dict):
            continue
        fx = [{"sound": str(f_.get("sound", "")).strip()[:60], "at": str(f_.get("at", "")).strip()[:120]}
              for f_ in (plan.get("effects") or []) if isinstance(f_, dict) and str(f_.get("sound", "")).strip()][:6]
        story["scenes"][i]["sound"] = {"ambience": str(plan.get("ambience") or "none").strip()[:80] or "none",
                                       "effects": fx}
    if result.get("level") == "explicit":
        check_adult_only(json.dumps(payload, ensure_ascii=False))
    (folder / "story.json").write_text(json.dumps(story, indent=2))
    if result.get("audio_file"):
        result["audio_outdated"] = True
        (folder / "result.json").write_text(json.dumps(result, indent=2))
    return {"ok": True}


REWRITE_PROMPT = """You are revising one scene of the story "{title}". Genre / tone: {genre}. Reader: {audience}.
Content rules: {rules}
Characters: {characters}
{context}
Here is the scene as it is now:
---
{text}
---
Rewrite this scene following these instructions: {instructions}
{keywords}
Keep it consistent with the rest of the story unless the instructions say otherwise. Aim for about
{words} words unless the instructions ask for a different length. {extra}Write it as finished story
prose with plenty of dialogue; put every spoken line in double quotation marks with a speech tag that
names who is speaking. Write only the scene itself: no title, notes or comments."""


def scenes_for(story, target):
    n = len(story["scenes"])
    kind, _, num = (target or "story").partition(":")
    if kind == "scene":
        i = int(num) - 1
        return [i] if 0 <= i < n else []
    if kind == "chapter":
        k = int(num)
        hits = [i for i, sc in enumerate(story["scenes"]) if sc.get("chapter", 1) == k]
        return hits
    return list(range(n))


def rewrite_story(cfg, run_id, target, instructions, then_remaster=True, keywords=None):
    folder, story, result = load_story(cfg, run_id)
    if keywords is not None:
        story["keywords"] = keyword_list(keywords)
        if result.get("level") == "explicit":
            check_adult_only(" ".join(story["keywords"]))
        (folder / "story.json").write_text(json.dumps(story, indent=2))
    level = result.get("level") or "family"
    if level == "explicit":
        adult_config(cfg)
    idxs = scenes_for(story, target)
    if not idxs:
        raise ValueError("That part of the story doesn't exist.")
    audience, rules, _ = level_info(level)
    rules += style_rules(story.get("prefs"), level)
    rules += style_block(cfg.get("_STYLE"), level)
    adult = level == "explicit"
    extra = ("If the scene involves sex, write it out fully and explicitly, moment by moment, in graphic detail. "
             if adult else "")
    steps = len(idxs) + (2 if then_remaster else 0)
    for n_, i in enumerate(idxs):
        report("writing", n_ / steps, f"Rewriting scene {i + 1} ({n_ + 1} of {len(idxs)})")
        sc = story["scenes"][i]
        before = story["scenes"][i - 1]["narration"][-1200:] if i > 0 else ""
        after = story["scenes"][i + 1]["narration"][:400] if i + 1 < len(story["scenes"]) else ""
        context = ((f"The scene before ends like this:\n…{before}\n" if before else "This is the first scene.\n") +
                   (f"The scene after begins like this:\n{after}…\n" if after else "This is the last scene.\n"))
        if sc.get("chapter_title"):
            context += f"This scene opens chapter {sc['chapter']}, \"{sc['chapter_title']}\".\n"
        words = max(150, len(sc["narration"].split()))
        new = clean_prose(ask_writer(cfg, REWRITE_PROMPT.format(
            title=story["title"], genre=story.get("genre", ""), audience=audience, rules=rules,
            characters=story.get("characters", ""), context=context, text=sc["narration"],
            instructions=instructions.strip() or "Improve it: richer detail and more natural dialogue.",
            words=words, extra=extra, keywords=keywords_block(story.get("keywords"), " ".join(
                s_["narration"] for j_, s_ in enumerate(story["scenes"]) if j_ != i))),
            as_json=False, system=EXPLICIT_SYSTEM if adult else None))
        if len(new.split()) < 40:
            raise ValueError(f"The writer sent back almost nothing for scene {i + 1}. Try again.")
        if adult:
            new = adult_guard(cfg, new, EXPLICIT_SYSTEM)
        sc["narration"] = new
        save_story_text(folder, story, result, [i])     # saved after every scene, so nothing is lost
    log(f"Rewrote {len(idxs)} scene(s).")
    if then_remaster and (result.get("audio_file") or result.get("video_file")):
        return remaster_story(cfg, run_id)
    return {"ok": True, "rewritten": len(idxs), "audio_outdated": bool(result.get("audio_outdated"))}


def remaster_story(cfg, run_id, revoice=False, redesign=False):
    """Make the audio again (and the video's sound) from the story as it is now: new or changed lines
    are recorded, the sound is designed and mixed again with your current sound files."""
    folder, story, result = load_story(cfg, run_id)
    level = result.get("level") or "family"
    if level == "explicit":
        adult_config(cfg)
    mode = result.get("mode") or ("video" if result.get("video_file") else "audio")
    if mode == "story":
        mode = "audio"
    job = jobs_dir(cfg) / f"remake_{hashlib.sha1(run_id.encode()).hexdigest()[:10]}"
    job.mkdir(parents=True, exist_ok=True)
    if not _PROG:
        progress_start(cfg, f"remake_{run_id[:20]}", job, "audio", False)
    _PROG["steps"] = [("writing", 2), ("narration", 80 if mode != "video" else 50)] + \
        ([("video", 35)] if mode == "video" else []) + [("finishing", 8)]
    stems = folder / ".stems"
    saved = None
    if not revoice and (stems / "voices.json").exists():
        saved = json.loads((stems / "voices.json").read_text()).get("voices")
    try:
        final, audio_out, timeline, engine, voices, credits = produce_audio(
            cfg, story, job, folder, level, story.get("genre", ""), None, run_id,
            saved_voices=saved, fresh_voices=revoice, redesign=redesign)
        (folder / "story.json").write_text(json.dumps(story, indent=2))
        result.update(audio_file=str(audio_out), voice=engine, audio_outdated=False,
                      remade=dt.datetime.now().isoformat(timespec="seconds"))
        if credits:
            result["sound_credits"] = credits
        if voices and any(v["voice"] for v in voices.get("voices", {}).values()):
            result["cast_voices"] = {v["name"]: v["voice"] for v in voices["voices"].values()}
        if cfg["SUBTITLES"] == "on":
            result["subtitles_file"] = str(folder / "story.srt")
        if mode == "video" and (folder / "story.mp4").exists():
            report("video", 0.1, "Putting the video together again with the new sound")
            for f_ in folder.glob("clip_*.mp4"):
                shutil.copy(f_, job / f_.name)
            for f_ in folder.glob("scene_*.png"):
                shutil.copy(f_, job / f_.name.replace("scene_", "pic_"))
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=width,height", "-of", "csv=p=0", str(folder / "story.mp4")],
                                   capture_output=True, text=True).stdout.strip().split(",")
            w, h = (int(probe[0]), int(probe[1])) if len(probe) == 2 else (1920, 1080)
            vcfg = dict(cfg, IMAGE_PROVIDER="cards")
            with tempfile.TemporaryDirectory() as tmpd:
                slices = slice_segments(final, timeline, job)
                _, _, seg_lens = make_video(vcfg, story, slices, job, Path(tmpd), w, h, h > w,
                                            Path(tmpd) / "story.mp4", "existing", None)
                shutil.copy(Path(tmpd) / "story.mp4", folder / "story.mp4")
            if cfg["SUBTITLES"] == "on":
                texts, spans, at = [], [], 0.0
                for seg, seg_len in zip(timeline["segments"], seg_lens):
                    if seg["lines"]:
                        texts.append(story["title"] if seg["kind"] == "title" else story["scenes"][seg["index"]]["narration"])
                        spans.append((at + seg["lines"][0]["start"] - seg["start"],
                                      seg["lines"][-1]["end"] - seg["lines"][0]["start"]))
                    at += seg_len
                write_subtitles(texts, spans, folder / "story.srt", folder / "story.vtt")
        report("finishing", 0.5, "Making the audiobook (chapters, cover and title)")
        try:
            make_audiobook(story, audio_out, timeline, folder, result.get("series", "")
                           if result.get("episode", 1) > 1 else "", result.get("episode", 1))
            result["audiobook_file"] = str(folder / "story.m4b")
        except Exception as e:
            log(f"Audiobook export skipped ({e}).")
        (folder / "result.json").write_text(json.dumps(result, indent=2))
    finally:
        shutil.rmtree(job, ignore_errors=True)
    log("Audio remade.")
    return {"ok": True, **result}


def book_images(story, clips, pics, job):
    """One picture per scene: a still from the scene's clip, or its picture."""
    imgs = []
    for i in range(len(story["scenes"])):
        c = clips[i] if clips and i < len(clips) else None
        if c:
            still = job / f"book_{i + 1:02d}.png"
            if not still.exists():
                run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(3.0, duration(c) / 2):.2f}", "-i", str(c),
                     "-frames:v", "1", str(still)])
            imgs.append(still)
        else:
            imgs.append(pics.get(i))
    return imgs


# ───────────────────────────── the job ─────────────────────────────

def paragraphs(text):
    """Book-style paragraphs: a new one whenever someone starts speaking after other text."""
    out = []
    for block in re.split(r"\n\s*\n|\n", (text or "").strip()):
        block = re.sub(r"\s+", " ", block).strip()
        if block:
            out += [b.strip() for b in re.split(r'(?<=[.!?…”"’])\s+(?=[“"])', block) if b.strip()]
    return out


def make_audiobook(story, audio, timeline, out_dir, series_name="", episode=1):
    """story.m4b: an audiobook with the title, a cover and chapter markers (Apple Books, phones, car
    players), and the same tags, cover and chapters added to story.mp3 so it plays well anywhere."""
    starts = [seg["start"] for seg in timeline["segments"]]
    total = duration(audio)
    marks = [(0.0, story["title"])]
    has_chapters = any(sc.get("chapter_title") for sc in story["scenes"])
    for i, sc in enumerate(story["scenes"], 1):
        if i >= len(starts):
            break
        if sc.get("mark"):
            marks.append((starts[i], sc["mark"]))
        elif has_chapters:
            if sc.get("chapter_title"):
                marks.append((starts[i], f"Chapter {sc['chapter']}: {sc['chapter_title']}"))
        else:
            marks.append((starts[i], sc.get("heading") or f"Part {i}"))
    marks = [m_ for k, m_ in enumerate(marks) if k + 1 == len(marks) or marks[k + 1][0] - m_[0] > 0.5]
    esc = lambda s: re.sub(r"([=;#\\\n])", r"\\\1", str(s))
    meta = [";FFMETADATA1", f"title={esc(story['title'])}", "artist=StoryMaker",
            f"album={esc(series_name or story['title'])}", "genre=Audiobook",
            f"comment={esc(story.get('logline', ''))}"]
    if series_name:
        meta.append(f"track={episode}")
    for k, (st, name) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else total
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(st * 1000)}", f"END={int(end * 1000)}", f"title={esc(name)}"]
    work = out_dir / ".audiobook"
    work.mkdir(exist_ok=True)
    try:
        mf = work / "meta.txt"
        mf.write_text("\n".join(meta) + "\n")
        cover = out_dir / "cover.jpg" if (out_dir / "cover.jpg").exists() else out_dir / "thumb.jpg"
        if not cover.exists():
            title_card(story["title"], 1400, 1400, work / "cover.png", 0)
            Image.open(work / "cover.png").convert("RGB").save(work / "cover.jpg", quality=88)
            cover = work / "cover.jpg"
        m4b = out_dir / "story.m4b"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-i", str(mf), "-i", str(cover),
             "-map", "0:a", "-map", "2:v", "-map_metadata", "1", "-map_chapters", "1",
             "-c:a", "aac", "-b:a", "96k", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
             "-f", "mp4", "-movflags", "+faststart", str(m4b)])
        if audio.suffix == ".mp3":
            tagged = work / "tagged.mp3"
            run(["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-i", str(mf), "-i", str(cover),
                 "-map", "0:a", "-map", "2:v", "-map_metadata", "1", "-map_chapters", "1",
                 "-c:a", "copy", "-c:v", "mjpeg", "-disposition:v", "attached_pic", "-id3v2_version", "3",
                 str(tagged)])
            tagged.replace(audio)
        return m4b
    finally:
        shutil.rmtree(work, ignore_errors=True)


def story_markdown(story, subtitle=""):
    parts = [f"# {story['title']}", ""]
    if subtitle:
        parts += [f"**{subtitle}**", ""]
    if story.get("logline"):
        parts += [f"*{story['logline']}*", ""]
    cl = characters_lines(story)
    if cl:
        parts += ["## Characters", ""] + cl + [""]
    for s_ in story["scenes"]:
        if s_.get("chapter_title"):
            parts += [f"## Chapter {s_['chapter']}: {s_['chapter_title']}", ""]
        for para in paragraphs(s_["narration"]):
            parts += [para, ""]
    parts.append("*The End*")
    return "\n".join(parts) + "\n"


def run_job(cfg, args, idea, scenes, vprovider, job_id, job, level, series):
    clip_mode = args.mode == "video" and vprovider in ("omni", "veo", "demo")
    w, h = (1080, 1920) if args.portrait else (1920, 1080)
    job.mkdir(parents=True, exist_ok=True)
    write_status(cfg, job_id, state="running", step="writing")

    # A continuing series reuses its character sheet so the characters look the same as last time.
    if series:
        sref = series_root(cfg) / series["slug"] / "character_ref.png"
        if sref.exists() and not (job / "character_ref.png").exists():
            shutil.copy(sref, job / "character_ref.png")

    progress_start(cfg, job_id, job, args.mode,
                   bool(args.book) or (args.mode == "video" and cfg["BOOK"] == "on"))
    story_file = job / "story.json"
    if story_file.exists():
        story = json.loads(story_file.read_text())
        log(f"Resuming '{story['title']}' from the earlier attempt.")
    else:
        writer = {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI", "groq": "Groq",
                  "ollama": "your own computer (Ollama)", "demo": "the test writer"}.get(cfg["STORY_PROVIDER"], "AI")
        chapters = max(1, min(args.chapters, 20))
        with Ticker("writing", f"Writing the story with {writer}",
                    (120 if cfg["STORY_PROVIDER"] == "ollama" else 25) * (chapters + (chapters > 1))):
            story = write_story(cfg, idea, args.genre, level, scenes, clip_mode, series, chapters, job, args.detail)
        if args.audience:
            story["audience"] = args.audience
        story["keywords"] = cfg.get("_KEYWORDS") or []
        if cfg.get("_PREFS"):
            story["prefs"] = cfg["_PREFS"]
        if args.sounds:
            story["sound_wishes"] = args.sounds.strip()[:300]
        apply_media_style(cfg, story)
        if args.narrator and VOICE_SPEC.match(args.narrator):
            story["voice_choice"] = {"narrator": args.narrator, "characters": {}}
        story_file.write_text(json.dumps(story, indent=2))
    n_sc = len(story["scenes"])
    n_ch = len(story.get("chapters") or [])
    report("writing", 1, f"Story written: {n_ch} chapters, {n_sc} scenes" if n_ch > 1 else
           f"Story written: {n_sc} scene{'s' * (n_sc != 1)}", title=story["title"],
           logline=story.get("logline", ""), opening=story["scenes"][0]["narration"][:320])

    episode = len(done_episodes(series)) + 1 if series else 1
    subtitle = f"{series['name']} — Episode {episode}" if series else ""
    run_id = f"{dt.datetime.now():%Y-%m-%d_%H%M%S}_{slugify(story['title'])}"
    out_dir = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    request = {"idea": idea, "genre": args.genre, "level": level, "mode": args.mode, "scenes": scenes,
               "chapters": args.chapters, "detail": args.detail, "keywords": ", ".join(story.get("keywords") or []),
               "sounds": story.get("sound_wishes", ""), "narrator": (story.get("voice_choice") or {}).get("narrator", ""),
               "portrait": args.portrait, "book": args.book, "series": series["name"] if series else "",
               **(story.get("prefs") or {})}
    result = {"ok": True, "job_id": job_id, "request": request, "title": story["title"], "logline": story.get("logline", ""),
              "level": level, "story": story_markdown(story, subtitle), "folder": str(out_dir)}
    (out_dir / "story.md").write_text(result["story"])
    (out_dir / "story.json").write_text(json.dumps(story, indent=2))
    result["story_file"] = str(out_dir / "story.md")
    complete = True
    voices = series.get("voices") if series else None
    clips, pics = [], {}

    with tempfile.TemporaryDirectory() as tmpd:
        work = Path(tmpd)
        timeline, final = None, None
        if args.mode in ("audio", "video"):
            story["genre"] = story.get("genre") or args.genre
            final, audio_out, timeline, engine, voices, credits = produce_audio(
                cfg, story, job, out_dir, level, args.genre, args.music, job_id, saved_voices=voices)
            (out_dir / "story.json").write_text(json.dumps(story, indent=2))     # keeps the sound plan
            result.update(audio_file=str(audio_out), voice=engine)
            if cfg["SUBTITLES"] == "on":
                result["subtitles_file"] = str(out_dir / "story.srt")
            if voices and any(v["voice"] for v in voices.get("voices", {}).values()):
                result["cast_voices"] = {v["name"]: v["voice"] for v in voices["voices"].values()}
            if credits:
                result["sound_credits"] = credits

        want_book = (args.mode == "video" and cfg["BOOK"] == "on") or args.book
        ref = None
        if args.mode == "video" or want_book:
            if (args.mode == "video" and vprovider in ("omni", "veo", "demo")) or pick_image_provider(cfg) == "gemini" \
                    or (job / "character_ref.png").exists():
                report("characters", 0.1, "Drawing the characters so they look the same in every scene")
                ref = make_character_ref(cfg, story, job)
                if ref:
                    shutil.copy(ref, out_dir / "character_ref.png")
                report("characters", 1, "Characters drawn", image=ref)

        if args.mode == "video":
            report("video", 0, "Filming the scenes" if clip_mode else "Drawing the pictures")
            raw_mp4 = work / "story_raw.mp4"
            slices = slice_segments(final, timeline, job)
            clips, pics, seg_lens = make_video(cfg, story, slices, job, work, w, h, args.portrait, raw_mp4,
                                               vprovider if clip_mode else "slideshow", ref)
            mp4 = out_dir / "story.mp4"
            shutil.copy(raw_mp4, mp4)
            for i, c in enumerate(clips):
                if c:
                    shutil.copy(c, out_dir / f"clip_{i + 1:02d}.mp4")
            for i, p_ in pics.items():
                shutil.copy(p_, out_dir / f"scene_{i + 1:02d}.png")
            made = sum(1 for c in clips if c)
            result.update(video_file=str(mp4), video_engine=vprovider if clip_mode else "slideshow")
            if clip_mode:
                result["clips_made"] = made
                if made < len(clips):
                    complete = False
                    result["note"] = (f"{len(clips) - made} clip(s) failed and show a still picture. "
                                      "Run the same request again to retry only those clips.")

        if want_book:
            report("book", 0.05, "Drawing the picture-book pages" if args.mode != "video" else "Laying out the picture book")
            if args.mode != "video":
                bw, bh = (1536, 1024)
                pics = make_pictures(cfg, story, job, bw, bh, False, range(len(story["scenes"])), ref)
            imgs = book_images(story, clips, pics, job)
            cover = next((im for im in imgs if im), None) or ref
            pdf = out_dir / "story-book.pdf"
            report("book", 0.9, "Laying out the picture book")
            make_book(story, imgs, cover, pdf, subtitle)
            result["book_file"] = str(pdf)
            log("Picture book ready.")

    report("finishing", 0.3, "Finishing up: subtitles, cover and library")
    if args.mode == "video" and cfg["SUBTITLES"] == "on" and timeline:
        # the video adds a short breath after each part, so its subtitles follow the video's timing
        texts, spans, at = [], [], 0.0
        for seg, seg_len in zip(timeline["segments"], seg_lens):
            if seg["lines"]:
                texts.append(story["title"] if seg["kind"] == "title" else story["scenes"][seg["index"]]["narration"])
                a = seg["lines"][0]["start"] - seg["start"]
                spans.append((at + a, seg["lines"][-1]["end"] - seg["lines"][0]["start"]))
            at += seg_len
        write_subtitles(texts, spans, out_dir / "story.srt", out_dir / "story.vtt")
    if args.mode != "video" or not result.get("video_file"):
        report("finishing", 0.4, "Painting the cover")
        make_cover(cfg, story, out_dir, level)
    make_thumbnail(out_dir, [out_dir / "cover.jpg", result.get("video_file"), out_dir / "scene_01.png",
                             out_dir / "character_ref.png"])
    if result.get("audio_file") and timeline:
        report("finishing", 0.7, "Making the audiobook (chapters, cover and title)")
        try:
            book = make_audiobook(story, Path(result["audio_file"]), timeline, out_dir,
                                  series["name"] if series else "", episode)
            result["audiobook_file"] = str(book)
        except Exception as e:
            log(f"Audiobook export skipped ({e}).")

    if cfg["MEDIA_BASE_URL"]:
        base = cfg["MEDIA_BASE_URL"].rstrip("/") + "/" + run_id
        for key in ("story_file", "audio_file", "video_file", "book_file", "subtitles_file"):
            if key in result:
                result[key.replace("_file", "_url")] = f"{base}/{Path(result[key]).name}"
    elif cfg["PUBLIC_DIR"] and cfg["PUBLIC_BASE_URL"]:
        pub = Path(os.path.expanduser(cfg["PUBLIC_DIR"])) / run_id
        pub.mkdir(parents=True, exist_ok=True)
        base = cfg["PUBLIC_BASE_URL"].rstrip("/") + "/" + run_id
        for key in ("story_file", "audio_file", "video_file", "book_file", "subtitles_file"):
            if key in result:
                src = Path(result[key])
                shutil.copy(src, pub / src.name)
                result[key.replace("_file", "_url")] = f"{base}/{src.name}"

    saved = save_episode(cfg, series, story, result, voices, out_dir, level, args.genre, args.series,
                         job_id, complete)
    result.update(series=saved["name"], episode=episode, mode=args.mode,
                  created=dt.datetime.now().isoformat(timespec="seconds"), run_id=run_id)
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))
    if complete:
        shutil.rmtree(job, ignore_errors=True)        # everything is in the output folder now
    else:
        result["retry_available"] = True
    write_status(cfg, job_id, state="done", result=result)
    return result


def job_key(args, idea, cfg, scenes, vprovider, level, series):
    parts = [idea, args.genre, args.audience or "", level, str(scenes), args.mode, cfg["STORY_PROVIDER"],
             vprovider, str(args.portrait), str(args.book),
             series["slug"] if series else "", str(len(done_episodes(series))) if series else "",
             str(args.chapters), args.detail, args.keywords or "", args.sounds or "", args.narrator or "",
             json.dumps(cfg.get("_PREFS") or {}, sort_keys=True)]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def recover_settings(cfg, args):
    """An unfinished story: its plan (outline.json) and the scenes it wrote are in its job folder. Work out how it
    was set up (chapters and scenes from the plan; level and detail from its log) and carry on in that folder,
    so only the missing scenes are written."""
    rid = re.sub(r"[^a-f0-9]", "", args.recover or "")
    folder = jobs_dir(cfg) / rid
    if not rid or not (folder / "outline.json").exists():
        print(json.dumps({"ok": False, "error": "There's nothing saved for that story to carry on from."}))
        sys.exit(2)
    st = jobs_dir(cfg) / f"{rid}.status.json"
    if not args.child and st.exists() and time.time() - st.stat().st_mtime < 15 * 60:
        try:
            if json.loads(st.read_text()).get("state") in ("running", "queued"):
                print(json.dumps({"ok": False, "error": "That story is already being worked on."}))
                sys.exit(2)
        except ValueError:
            pass
    outline = json.loads((folder / "outline.json").read_text())
    chs = outline.get("chapters") or []
    args.chapters = max(1, len(chs))
    args.scenes = max([len(c.get("scenes") or []) for c in chs] + [1])
    logf = jobs_dir(cfg) / f"{rid}.log"
    text = logf.read_text(errors="replace") if logf.exists() else ""
    m = re.search(r"Writing a \d+-chapter story \(\d+ scenes each, about (\d+) words per scene\) for (.+?) with", text)
    if m:
        for name, info in LEVELS.items():
            if info[0] == m.group(2):
                args.level = name
                words = int(m.group(1))
                args.detail = min(DETAIL, key=lambda d: abs(160 * info[2] * DETAIL[d] - words))
                break
    m = re.search(r"--level['\",\s]+([a-z-]+)", text)
    if not args.level and m and m.group(1) in LEVELS:
        args.level = m.group(1)
    if not args.idea:
        args.idea = (outline.get("logline") or outline.get("title") or "Continue the story.")[:2000]
    args._job = rid


def main():
    ap = argparse.ArgumentParser(description="Write a story and turn it into audio and video.")
    ap.add_argument("--idea", help="What the story is about (or pipe it in on stdin)")
    ap.add_argument("--genre", default="heartwarming adventure")
    ap.add_argument("--level", choices=sorted(LEVELS), help="reading level (default: LEVEL setting, or the series')")
    ap.add_argument("--audience", help="free-text note about the reader (optional)")
    ap.add_argument("--scenes", type=int, default=5, help="scenes (per chapter, with --chapters)")
    ap.add_argument("--chapters", type=int, default=1, help="write a longer story in this many chapters (1–20)")
    ap.add_argument("--detail", choices=sorted(DETAIL), default="standard",
                    help="how long and detailed each scene is (not for AI video clips, which need short scenes)")
    ap.add_argument("--mode", choices=["story", "audio", "video"], default="video")
    ap.add_argument("--provider", choices=["claude", "gemini", "openai", "groq", "ollama", "demo"],
                    help="override STORY_PROVIDER")
    ap.add_argument("--video", choices=["omni", "veo", "slideshow", "demo"], help="override VIDEO_PROVIDER")
    ap.add_argument("--music", help="part of a music file name, or 'off'")
    ap.add_argument("--book", action="store_true", help="also make a picture-book PDF in story/audio mode")
    ap.add_argument("--continue", dest="cont", action="store_true", help="next episode of your most recent story")
    ap.add_argument("--series", help="next episode of the series with this name (or start one with it)")
    ap.add_argument("--list-series", action="store_true", help="list your series and stop")
    ap.add_argument("--portrait", action="store_true", help="phone-shaped 9:16 video")
    ap.add_argument("--estimate", action="store_true", help="print the approximate cost and stop")
    ap.add_argument("--yes", action="store_true", help="skip the MAX_COST_USD check")
    ap.add_argument("--fresh", action="store_true", help="ignore any unfinished earlier attempt")
    ap.add_argument("--no-style", action="store_true", help="don't use your saved style for this story")
    ap.add_argument("--recover", metavar="JOB", help="carry on an unfinished story from what it already wrote")
    ap.add_argument("--background", action="store_true", help="return at once; notify when finished")
    ap.add_argument("--status", metavar="JOB_ID", help="print the status of a background job")
    ap.add_argument("--open", action="store_true", help="Mac: open the result when done")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--features", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--export-pdf", metavar="STORY", help="make a reading-copy PDF of a finished story (folder name)")
    ap.add_argument("--delete", metavar="STORY", help="delete a finished story (folder name) and its series entry")
    ap.add_argument("--edit", metavar="STORY", help="change a finished story's text (JSON on stdin)")
    ap.add_argument("--rewrite", metavar="STORY", help="rewrite part of a story with the AI (instructions on stdin)")
    ap.add_argument("--target", default="story", help="with --rewrite: story, chapter:N or scene:N")
    ap.add_argument("--instructions", help=argparse.SUPPRESS)
    ap.add_argument("--no-remake", action="store_true", help="with --rewrite: don't remake the audio afterwards")
    ap.add_argument("--remaster", metavar="STORY", help="make a finished story's audio (and video sound) again")
    ap.add_argument("--revoice", action="store_true", help="with --remaster: choose the character voices again")
    ap.add_argument("--redesign", action="store_true", help="with --remaster: choose the sounds again")
    ap.add_argument("--job-id", help=argparse.SUPPRESS)
    ap.add_argument("--keywords", help="words or phrases the story should include (comma-separated)")
    ap.add_argument("--sounds", help="sounds you want to hear, e.g. 'rain, thunder, crackling fire'")
    ap.add_argument("--narrator", help="narrator voice (Kokoro), e.g. af_heart or bm_fable")
    ap.add_argument("--pov", choices=sorted(POV), help="first or third person")
    ap.add_argument("--heat", type=int, choices=range(1, 6), help="Explicit: 1 sensual … 5 maximum")
    ap.add_argument("--pacing", choices=sorted(PACING), help="Explicit: slow, steady or fast")
    ap.add_argument("--language", choices=sorted(LANGUAGE), help="Explicit: tasteful, frank or crude")
    ap.add_argument("--turn-ons", help="Explicit: things to include (between consenting adults)")
    ap.add_argument("--limits", help="Explicit: things never to include")
    ap.add_argument("--set-voices", metavar="STORY", help="choose a story's voices (JSON on stdin)")
    ap.add_argument("--set-sounds", metavar="STORY", help="change a story's sounds (JSON on stdin)")
    ap.add_argument("--set-cast", metavar="STORY", help="tag who is a woman, man, girl, boy... (JSON on stdin)")
    ap.add_argument("--voices-list", action="store_true", help="list the voices you can pick")
    ap.add_argument("--voice-sample", metavar="VOICE", help=argparse.SUPPRESS)
    args = ap.parse_args()

    cfg = load_settings()
    if args.provider:
        cfg["STORY_PROVIDER"] = args.provider
    if args.video:
        cfg["VIDEO_PROVIDER"] = args.video

    cfg["_STYLE"] = {} if args.no_style else style_from_env()
    if args.recover:                                  # finish a stopped story from its saved plan and scenes
        recover_settings(cfg, args)

    if args.status:
        p_ = jobs_dir(cfg) / f"{args.status}.status.json"
        print(p_.read_text() if p_.exists() else json.dumps({"ok": False, "error": "no such job"}))
        return
    if args.list_series:
        print(json.dumps({"ok": True, "series": list_series(cfg)}))
        return
    if args.voices_list:
        print(json.dumps({"ok": True, "engine": pick_tts(cfg), "kokoro": kokoro_ok(),
                          "voices": [dict(zip(("id", "name", "gender", "accent", "note"), v)) for v in KOKORO_CATALOGUE],
                          "narrator": cfg["KOKORO_VOICE"]}))
        return
    if args.voice_sample:
        try:
            print(json.dumps({"ok": True, "file": str(voice_sample(cfg, args.voice_sample))}))
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
        return
    if args.set_voices or args.set_sounds or args.set_cast:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            out = (set_voices(cfg, args.set_voices, payload) if args.set_voices else
                   set_sounds(cfg, args.set_sounds, payload) if args.set_sounds else set_cast(cfg, args.set_cast, payload))
            print(json.dumps(out))
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
        return
    if args.edit:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            print(json.dumps(edit_story(cfg, args.edit, payload)))
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
        return
    if args.rewrite or args.remaster:
        run_id = args.rewrite or args.remaster
        try:
            story_folder(cfg, run_id)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return
        instructions = args.instructions
        if args.rewrite and instructions is None:
            instructions = "" if sys.stdin.isatty() else sys.stdin.read()
        job_id = args.job_id or hashlib.sha1(f"{run_id}|{time.time()}".encode()).hexdigest()[:12]
        if args.background and not args.child:
            logf = jobs_dir(cfg) / f"{job_id}.log"
            argv = [a for a in sys.argv[1:] if a != "--background"]
            cmd = [sys.executable, str(Path(__file__).resolve()), *argv, "--job-id", job_id, "--child"]
            if args.rewrite:
                cmd += ["--instructions", instructions or ""]
            with open(logf, "a") as lf:
                subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                 start_new_session=True, close_fds=True)
            write_status(cfg, job_id, state="queued", kind="rewrite" if args.rewrite else "remaster", story=run_id)
            print(json.dumps({"ok": True, "queued": True, "job_id": job_id}))
            return
        progress_start(cfg, job_id, jobs_dir(cfg) / job_id, "audio", False)
        _PROG["status"].update(kind="rewrite" if args.rewrite else "remaster", story=run_id)
        try:
            if args.rewrite:
                out = rewrite_story(cfg, run_id, args.target, instructions or "", not args.no_remake, args.keywords)
            else:
                out = remaster_story(cfg, run_id, args.revoice, args.redesign)
        except Exception as e:
            write_status(cfg, job_id, state="failed", error=str(e), kind="rewrite" if args.rewrite else "remaster",
                         story=run_id)
            print(json.dumps({"ok": False, "error": str(e)}))
            raise SystemExit(1)
        write_status(cfg, job_id, state="done", kind="rewrite" if args.rewrite else "remaster", story=run_id,
                     result={"run_id": run_id})
        print(json.dumps(out if isinstance(out, dict) else {"ok": True}))
        return
    if args.export_pdf or args.delete:
        try:
            print(json.dumps(export_pdf(cfg, args.export_pdf) if args.export_pdf else delete_story(cfg, args.delete)))
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
        return
    if args.features:
        print(json.dumps({"ok": True, "adult": cfg["ADULT_CONTENT"].lower() == "on",
                          "local_pictures": bool(cfg.get("LOCAL_IMAGE_URL"))}))
        return

    series = None
    if args.cont or args.series:
        series = find_series(cfg, args.series)
        if args.cont and not args.series and not series:
            sys.exit("There's no earlier story to continue yet. Make one first.")
        if series:
            log(f"Continuing '{series['name']}' — episode {len(done_episodes(series)) + 1}.")
    level = args.level or (series or {}).get("level") or cfg["LEVEL"]
    if level not in LEVELS:
        level = "family"

    idea = (args.idea or "").strip()
    if not idea and not sys.stdin.isatty():
        idea = sys.stdin.read().strip()
    cfg["_KEYWORDS"] = keyword_list(args.keywords)
    prefs = {"pov": args.pov or "", "heat": args.heat or 0, "pacing": args.pacing or "", "language": args.language or "",
             "turn_ons": (args.turn_ons or "").strip()[:400], "limits": (args.limits or "").strip()[:400]}
    cfg["_PREFS"] = {k: v for k, v in prefs.items() if v}

    if level == "explicit":
        # Adults only, and it never leaves this computer: the online AIs don't allow it and could
        # shut off your keys. Local writer, local voice, no AI pictures or video.
        if cfg["ADULT_CONTENT"].lower() != "on":
            sys.exit("The Explicit level is turned off. Set ADULT_CONTENT=on in storymaker.env to use it.")
        adult_config(cfg)
        if args.mode == "video" and cfg["IMAGE_PROVIDER"] != "local":
            sys.exit("Explicit video needs free local pictures: set LOCAL_IMAGE_URL (Draw Things) in storymaker.env. "
                     "Or use --mode audio.")
        if cfg["IMAGE_PROVIDER"] != "local":
            args.book = False
        try:
            check_adult_only(" ".join([idea, args.genre or "", args.audience or "", args.keywords or "", args.sounds or ""]))
            check_prefs(cfg["_PREFS"])
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            sys.exit(2)

    vprovider = pick_video_provider(cfg)
    scenes = max(1, min(args.scenes, 12))
    args.chapters = max(1, min(args.chapters, 20))
    est = estimate(cfg, args.mode, scenes * args.chapters, vprovider, args.book,
                   writes=args.chapters + 1 if args.chapters > 1 else 1,
                   words_per_scene=int((160 if args.chapters > 1 or args.detail in ("rich", "epic") else 85)
                                       * DETAIL[args.detail]))
    est["chapters"] = args.chapters
    if args.estimate:
        extra = {"series": series["name"], "episode": len(done_episodes(series)) + 1} if series else {}
        print(json.dumps({"ok": True, **est, "level": level, **extra}))
        return
    if est["estimate_usd"] > float(cfg["MAX_COST_USD"]) and not args.yes:
        print(json.dumps({"ok": False, "needs_confirm": True, **est,
                          "error": f"{est['summary']} is over MAX_COST_USD (${cfg['MAX_COST_USD']}). "
                                   "Add --yes to go ahead."}))
        sys.exit(2)

    if not idea and not series:
        sys.exit("No story idea given. Use --idea \"...\" or pipe text in.")

    job_id = getattr(args, "_job", None) or job_key(args, idea, cfg, scenes, vprovider, level, series)
    job = jobs_dir(cfg) / job_id
    if args.fresh and job.exists():
        shutil.rmtree(job)

    if args.background and not args.child:
        logf = jobs_dir(cfg) / f"{job_id}.log"
        argv = [a for a in sys.argv[1:] if a != "--background"]
        cmd = [sys.executable, str(Path(__file__).resolve()), *argv, "--idea", idea, "--yes", "--child"]
        with open(logf, "a") as lf:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                             start_new_session=True, close_fds=True)
        write_status(cfg, job_id, state="queued")
        print(json.dumps({"ok": True, "queued": True, "job_id": job_id, **est,
                          "message": "Working on it — you'll get a notification when it's ready."}))
        return

    notify_on = cfg["NOTIFY"] == "always" or (cfg["NOTIFY"] == "background" and args.child)
    try:
        result = run_job(cfg, args, idea, scenes, vprovider, job_id, job, level, series)
    except MinorContent as e:
        shutil.rmtree(job, ignore_errors=True)            # nothing of it is kept: not the plan, not a scene
        for f in jobs_dir(cfg).glob(f"{job_id}*"):
            if f.is_file() and not f.name.endswith(".status.json"):
                f.unlink(missing_ok=True)
        write_status(cfg, job_id, state="failed", error=str(e))
        if notify_on:
            notify(cfg, "Story stopped", str(e)[:200], failed=True)
        raise
    except Exception as e:
        write_status(cfg, job_id, state="failed", error=str(e))
        if notify_on:
            notify(cfg, "Story failed", f"{e}"[:200] + " — run it again to resume.", failed=True)
        raise

    if notify_on:
        link = result.get("video_url") or result.get("audio_url") or result.get("book_url") or result.get("story_url")
        msg = result.get("logline") or "Your story is ready."
        if result.get("note"):
            msg += " " + result["note"]
        notify(cfg, f"Story ready: {result['title']}", msg, click=link)
    if args.open and IS_MAC:
        subprocess.run(["open", result.get("video_file") or result.get("audio_file")
                        or result.get("book_file") or result["story_file"]])
    log(f"Done → {result['folder']}")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        log(f"Error: {e}")
        sys.exit(1)
