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
import signal
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
    "LOCAL_IMAGE_STEPS": "auto",           # auto = what suits the model Draw Things has loaded (or a number)
    "LOCAL_IMAGE_CFG": "auto",             # guidance: auto, or a number
    "LOCAL_IMAGE_MODEL": "",               # blank = whatever model Draw Things has selected
    "PICTURE_STYLE": "storybook",          # storybook | realistic | cinematic (each story can choose its own)
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
    "GROQ_TPM": "8000",                           # Groq's tokens-a-minute allowance (8000 on the free plan)
    "GROQ_REASONING": "auto",                     # how hard gpt-oss thinks: auto (plan hard, scenes medium,
                                                  # fixes briefly), or low / medium / high for everything
    "GROQ_FALLBACK": "ollama",                    # Groq's free allowance used up: carry on with Ollama (off = stop)
    "OLLAMA_URL": "http://localhost:11434",  # in Docker on a Mac: http://host.docker.internal:11434
    "OLLAMA_MODEL": "llama3.1:8b",
    "OLLAMA_FALLBACK_URL": "",                # a backup Ollama when OLLAMA_URL's isn't there (e.g. on the server)
    "OLLAMA_FALLBACK_MODEL": "",              # the backup's model, if it doesn't have the same one

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
                      "bf_isabella^0.95,af_aoede+bf_lily^1.02,af_jessica,af_river+af_nova^0.98,af_alloy^1.05,"
                      "af_bella+bf_isabella,af_heart+bf_emma,af_jessica+af_sarah,af_nova+af_jessica,af_kore+af_river,"
                      "bf_emma+bf_alice,af_aoede+af_heart,af_nicole+af_bella;"
                      "male=am_michael,bm_george,am_fenrir^0.95,am_puck^1.03,am_liam+am_eric,bm_daniel^0.97,"
                      "am_onyx^0.94,am_adam^1.02,bm_daniel+am_michael,am_michael+bm_fable,am_eric+am_adam,"
                      "am_michael+am_fenrir,am_onyx+bm_george,bm_fable+bm_daniel,am_liam+am_puck,am_echo+bm_daniel;"
                      "girl=af_sky^1.08,af_nova^1.06,af_sky+af_nova^1.06;boy=am_echo^1.08,am_puck^1.1,am_puck+am_echo^1.06;"
                      "old_woman=bf_alice^0.95,bf_isabella^0.92,bf_alice+bf_isabella;"
                      "old_man=bm_lewis^0.93,bm_daniel^0.9,bm_lewis+bm_george,am_fenrir+bm_lewis;"
                      "creature=am_onyx^0.85,bm_fable^0.9,am_onyx+am_fenrir^0.92,am_santa+am_onyx"),

    "BOOK": "on",                          # picture-book PDF in video mode (free: uses the video's frames)
    "LEVEL": "family",                     # default reading level (see --level)
    "PROOFREAD": "on",                     # a copy editor's pass over every scene: spelling, grammar, clear dialogue
    "ADULT_CONTENT": "off",                # on = allow the Explicit (18+) level (local Ollama only)
    "OLLAMA_ADULT_MODEL": "dolphin3",      # the local model used for Explicit stories
    "OLLAMA_TEMPERATURE": "0.8",           # livelier writing; lower = safer, plainer
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
    "MUSIC_MODE": "under",                 # under = softly all through; moments = only the start, chapters and end
    "INTIMATE_SOUNDS": "on",               # explicit love scenes: soft breathing and moans at the right moments

    "NOTIFY": "background",                # background | always | off
    "NTFY_SERVER": "https://ntfy.sh",
    "NTFY_TOPIC": "",
    "NTFY_TOKEN": "",

    "MAX_COST_USD": "10",
    # Approximate prices used for --estimate (US dollars). Edit to match your bills.
    "PRICE_OMNI_PER_SEC": "0.10",
    "PRICE_VEO_PER_SEC": "0.40",
    "BIG_MOMENTS": "0",                      # a picture video's most dramatic scenes animated with Veo (0 = none)
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
    "LIBRARY_CHECK_FILE": "",              # a file that's only there when the NAS is connected (the server sets it)
    "JOBS_DIR": "",                        # stories being made (blank = OUTPUT_DIR/.jobs); a local disk when OUTPUT_DIR is a NAS
    "PUBLIC_DIR": "",
    "PUBLIC_BASE_URL": "",
    "MEDIA_BASE_URL": "",                  # web page: links to files where the web page serves OUTPUT_DIR
    "SUBTITLES": "on",
    "CAPTIONS": "on",                      # subtitles burned into the video, line by line as spoken (off = only the
                                           # switchable subtitle track)
    "MOTION": "on",
    "STUDIO_HELPER_URL": "http://host.docker.internal:8096",   # Audio Studio, which opens Draw Things/Ollama for us
    "MORE_PICTURES": "on",                 # video with free local pictures: long scenes get a new picture every
    "SECONDS_PER_PICTURE": "40",           # this many seconds, of a later moment in the scene
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
Write this scene now as finished story prose of about {words} words. Keep it moving: every paragraph shows
something new happening. Use telling details of the place and the senses, what the characters think and feel,
and plenty of dialogue, but never pad: no restating what the reader already knows, no summing up feelings you've
already shown, no long lists of description. {pov}
Don't repeat or retell anything from earlier scenes, chapters or earlier parts of the series: pick up where the
story so far ends and move it on. Every sentence is a complete, clear thought.
{extra}Put every spoken line in double quotation marks with a speech tag that names who is speaking,
like: “Wait for me,” Mira said. Start a new paragraph whenever the speaker changes.
Write only the scene itself: no title, no heading, no notes, nothing before or after the story text.
Stop when this scene's events are done; don't end the chapter or the story early."""

CONTINUE_PROMPT = """Here is a scene from the story "{title}" ({genre}, for {audience}). Content rules: {rules}

{text}

Continue this same scene from exactly where it stops, moving its events forward. Add about {more} more words of
prose and dialogue (spoken lines in double quotation marks with a speech tag), in the same point of view and
tense. {extra}Write only the continuation, with no heading or notes, and don't repeat or restate anything that's
already written."""

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
Write a NEW adventure that follows on from the last episode (don't retell or repeat earlier plots). Respect
everything that's true now (injuries, secrets, who has what, how people feel about each other), and you may pick
up one or two of the open threads.
You may add new characters; keep the recurring ones in "characters" and "cast" unchanged.
"""

RECAP_PROMPT = """You keep the continuity notes for a story series. Read this episode and reply with JSON only:
{{"recap": "5 to 8 sentences: what happened in this episode, in order, naming who did what, and how it ended",
 "where_things_stand": ["short facts that are true at the end and will matter later: injuries, who knows what,
   relationships and feelings, where people are, important objects and who has them"],
 "open_threads": ["unresolved questions, promises, dangers or mysteries a next episode could pick up"]}}
Up to 8 items in each list. Earlier facts that are still true (keep them unless this episode changed them):
{state}

Episode "{title}":
{text}"""


def bible_of(series):
    """A series' bible: its characters (name, type, age, look, outfit, voice), look, picture style and narrator."""
    return (series or {}).get("bible") or {}


def bible_characters(series):
    """The bible's characters as the writer and the picture app read them: "Mara (12, red curls, wearing …)"."""
    out = []
    for c in bible_of(series).get("characters") or []:
        if not c.get("name"):
            continue
        bits = [x for x in (c.get("age"), c.get("look"), f"wearing {c['outfit']}" if c.get("outfit") else "") if x]
        out.append(f"{c['name']} ({', '.join(bits)})" if bits else c["name"])
    return "; ".join(out)


def bible_cast(series):
    return [{"name": c["name"], "voice": c["type"], "confirmed": True}
            for c in bible_of(series).get("characters") or [] if c.get("name") and c.get("type") in VOICE_TYPES]


def bible_from_story(story):
    """A first bible from what the writer wrote: "Mara (12, red curls, freckles)" → name, age, look; the type from
    the cast."""
    types = {str(c.get("name", "")).lower(): c.get("voice") for c in story.get("cast") or [] if isinstance(c, dict)}
    out = []
    for part in re.split(r";\s*", story.get("characters", "") or ""):
        part = part.strip()
        if not part:
            continue
        # the name ends at the first "(", ":", "," or spaced dash: "Mira, a curious girl…", "Tom (60s, …)"
        cut = re.search(r"\s*(?:\(|:|,|\s[—–-]\s)\s*", part)
        name = (part[:cut.start()] if cut else part).strip()[:60]
        desc = (part[cut.end():] if cut else "").strip().rstrip(")").strip()
        if not name:
            continue
        age = ""
        a = re.match(r"(\d{1,3}s?|teen|adult|elderly)\s*,\s*", desc, re.I)
        if a:
            age, desc = a.group(1), desc[a.end():]
        outfit = ""
        w = re.search(r"\bwearing\s+(.+)$", desc, re.I)
        if w:
            outfit, desc = w.group(1).strip(" ,."), desc[:w.start()].strip(" ,")
        out.append({"name": name, "type": types.get(name.lower(), ""), "age": age[:20], "look": desc[:300],
                    "outfit": outfit[:200], "voice": ""})
    return out[:24]


def portrait_prompt(series, c):
    ps = PICTURE_STYLES[picture_style({"picture_style": bible_of(series).get("picture_style")})]
    look = (bible_of(series).get("visual_style") or series.get("visual_style") or "") if ps["keep_writer_look"] \
        else ps["look"]
    bits = ", ".join(x for x in (c.get("age"), c.get("look"), f"wearing {c['outfit']}" if c.get("outfit") else "") if x)
    return (f"Character portrait of {c['name']}{': ' + bits if bits else ''}. Head and shoulders, facing the viewer, "
            f"a calm natural expression, plain soft background. {look}.")


def draw_portraits(cfg, slug, report_=lambda f, t: None):
    """A portrait for every character in a series' bible, from exactly the bible's words: the reference for how
    each one looks. Each character keeps its own seed, so redrawing gives the same face unless the words change."""
    f = series_root(cfg) / slug / "series.json"
    if not f.exists():
        raise ValueError("Unknown series.")
    series = json.loads(f.read_text())
    if not local_image_ready(cfg):
        raise RuntimeError("Portraits need Draw Things: it isn't answering. " + DRAW_THINGS_FIX)
    chars = [c for c in bible_of(series).get("characters") or [] if c.get("name")]
    pdir = f.parent / "portraits"
    pdir.mkdir(exist_ok=True)
    level = series.get("level") or "family"
    size = picture_size(cfg, square=True)
    for i, c in enumerate(chars):
        report_(i / max(1, len(chars)), f"Drawing {c['name']} ({i + 1} of {len(chars)})")
        prompt = portrait_prompt(series, c)
        if level in ("mature", "explicit"):
            prompt = picture_safe(prompt, level)
        seed = int(hashlib.sha1(f"{slug}|{c['name'].lower()}".encode()).hexdigest()[:8], 16)
        name = f"{slugify(c['name']) or 'character'}.png"
        local_image(cfg, prompt, size[0], size[1], pdir / name, level, seed,
                    style=picture_style({"picture_style": bible_of(series).get("picture_style")}))
        c["portrait"] = name
        series = {**json.loads(f.read_text()), "bible": {**bible_of(series), "characters": chars}}
        f.write_text(json.dumps(series, indent=2))           # saved after each, so a stop keeps what's drawn
    report_(1, "Portraits drawn")
    return {"drawn": len(chars)}


def last_episode_style(series):
    """The picture style the last episode was drawn in, so a series keeps its look even without a bible choice."""
    for e in reversed(done_episodes(series)):
        try:
            return json.loads((Path(e["folder"]) / "story.json").read_text()).get("picture_style")
        except (OSError, ValueError, KeyError):
            continue
    return None


def series_memory(series):
    """What the writer is told about earlier episodes: the last three in detail, older ones in a line, then what's
    true now and the open threads."""
    eps = done_episodes(series)
    lines = []
    for i, e in enumerate(eps):
        told = e.get("recap") if i >= len(eps) - 3 and e.get("recap") else e.get("logline", "")
        lines.append(f"- Episode {i + 1}: {e.get('title', '')} — {told}")
    out = "\n".join(lines) or "(no earlier episodes)"
    if series.get("state"):
        out += "\nWhere things stand now:\n" + "\n".join(f"- {x}" for x in series["state"])
    if series.get("threads"):
        out += "\nOpen threads (pick up one or two, not all):\n" + "\n".join(f"- {x}" for x in series["threads"])
    return out


def episode_recap(cfg, story, series=None):
    """The continuity notes for a finished episode (None if the writer couldn't make them: that's not fatal)."""
    if cfg.get("STORY_PROVIDER", "").lower() == "demo":     # the test writer only writes stories
        return None
    text = "\n\n".join(str(sc.get("narration", "")) for sc in story.get("scenes", []))
    if len(text) > 14000:                      # the start and (especially) the end matter most
        text = text[:4000] + "\n…\n" + text[-10000:]
    try:
        r = ask_writer(cfg, RECAP_PROMPT.format(title=story.get("title", ""), text=text,
                                                state="\n".join(f"- {x}" for x in (series or {}).get("state") or [])
                                                or "(none yet)"), think="fix")
    except Exception as e:
        log(f"Couldn't write this episode's continuity notes ({str(e)[:120]}); the next episode gets its summary.")
        return None
    clean = lambda xs: [str(x).strip()[:240] for x in (xs or []) if str(x).strip()][:8]
    return {"recap": str(r.get("recap") or "").strip()[:1500], "state": clean(r.get("where_things_stand")),
            "threads": clean(r.get("open_threads"))}


def extract_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("The AI reply did not contain JSON:\n" + text[:500])
    return json.loads(text[start:end + 1])


class WriterLimit(RuntimeError):
    """An online writer's allowance is used up for longer than it's worth waiting."""


_PATIENCE = 15 * 60                    # wait up to this long for a writer's allowance to free up


def _duration(text):
    """'19.215s', '1m2.5s', '7m12s', '1h3m' → seconds."""
    m = re.fullmatch(r"\s*(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?\s*", text or "")
    if not m or not any(m.groups()):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)


def _wait_hint(r):
    """How long the service says to wait before asking again."""
    ra = r.headers.get("retry-after")
    if ra:
        w = _duration(ra)
        if w is not None:
            return w
    m = re.search(r"(?:try again|retry)\s+in\s+((?:\d+h)?(?:\d+m(?!s))?(?:[\d.]+s)?)", r.text or "", re.I)
    if m:
        return _duration(m.group(1))
    m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', r.text or "")       # Gemini
    return float(m.group(1)) if m else None


def _say_wait(seconds):
    s_ = int(round(seconds))
    if s_ < 90:
        return f"{s_} seconds"
    if s_ < 5400:
        return f"{round(s_ / 60)} minutes"
    return f"{s_ / 3600:.1f} hours"


_GROQ = {"remaining": None, "reset_at": 0.0}


def _tokens(msgs):
    """A cautious guess at how many tokens these messages are (Groq counts about 3–4 characters a token)."""
    return int(sum(len(m["content"]) for m in msgs) / 3.2) + 60


def groq_budget(cfg, msgs):
    """How long a reply to ask Groq for. Groq counts the question AND the longest reply asked for against its
    per-minute allowance (8,000 tokens on the free plan), so asking for 8,000 tokens of reply can never fit."""
    allowance = int(cfg.get("GROQ_TPM") or 8000)
    return max(1024, min(8000, allowance - 300 - _tokens(msgs)))


def _groq_pace(msgs, reply=0):
    """Groq's free plan allows a set number of tokens a minute and says, with every reply, how many are left.
    Wait just long enough when the next scene wouldn't fit, instead of being turned away."""
    need_ = _tokens(msgs) + (reply or 600)
    left, reset_at = _GROQ["remaining"], _GROQ["reset_at"]
    if left is not None and left < need_ and reset_at > time.time():
        wait = min(65.0, reset_at - time.time()) + 0.5
        log(f"Pacing for Groq's free plan: waiting {_say_wait(wait)} for its per-minute allowance…")
        time.sleep(wait)


def _groq_note(r):
    try:
        _GROQ["remaining"] = int(r.headers.get("x-ratelimit-remaining-tokens"))
        _GROQ["reset_at"] = time.time() + (_duration(r.headers.get("x-ratelimit-reset-tokens", "")) or 0)
    except (TypeError, ValueError):
        pass


def post_patiently(who, url, headers, body, timeout, msgs=None):
    """POST to an online writer. "Too many requests" and "busy" replies are waited out (as long as the service
    says, up to 15 minutes) and tried again, instead of stopping the whole story."""
    waited = 0.0
    for attempt in range(12):
        if who == "Groq" and msgs:
            _groq_pace(msgs, body.get("max_tokens", 0))
        r = requests.post(url, headers=headers, json=body, timeout=timeout)
        if who == "Groq":
            _groq_note(r)
        if r.status_code == 413 and "max_tokens" in body:
            # "Request too large": the question plus the longest reply allowed is more than the per-minute
            # allowance. Ask for a shorter reply (a scene needs far less than the most it's allowed).
            m = re.search(r"Limit\s+(\d+),\s*Requested\s+(\d+)", r.text or "")
            over = int(m.group(2)) - int(m.group(1)) if m else 1500
            shorter = body["max_tokens"] - over - 300
            if shorter >= 1024:
                log(f"{who}: that request was a little too big for the free plan; asking for a shorter reply…")
                body = {**body, "max_tokens": shorter}
                continue
            raise WriterLimit(f"This part of the story is too big for {who}'s free plan in one go (it asked for "
                              f"{m.group(2) if m else 'more than'} tokens; the plan allows "
                              f"{m.group(1) if m else 'fewer'} a minute). ({r.text[:200]})")
        if r.status_code not in (429, 500, 502, 503, 504, 529):
            return r
        hint = _wait_hint(r)
        wait = hint if hint is not None else min(60.0, 5.0 * 2 ** attempt)
        daily = bool(re.search(r"per day|\(TPD\)|\(RPD\)|daily|quota", r.text or "", re.I))
        if wait > _PATIENCE or waited + wait > 2 * _PATIENCE or (daily and wait > 120):
            raise WriterLimit(f"{who}'s free allowance is used up for now; it frees up in about {_say_wait(wait)}. "
                              "What's written so far is kept: press Recover in Unfinished stories later, or pick "
                              f"another writer. ({who} said: {r.text[:200]})")
        log(f"{who} says it's had too many requests ({r.status_code}); waiting {_say_wait(wait)} and trying again…")
        time.sleep(wait + 1)
        waited += wait
    return r


def _switch_to_ollama(cfg):
    """Groq's allowance for today is used up: carry on with Ollama on the Mac, if it's there (GROQ_FALLBACK=ollama).
    The rest of this story is written by Ollama, so it isn't left half-done."""
    if str(cfg.get("GROQ_FALLBACK", "ollama")).lower() != "ollama":
        return False
    which, _ = pick_ollama(cfg, wake=True)
    if which is None:
        return False
    where = "on the Mac" if which == "main" else "on the server (the Mac's isn't answering)"
    log(f"Groq's free allowance is used up for now, so the rest of this story is written by Ollama "
        f"({cfg['OLLAMA_MODEL']}) {where}.")
    cfg["STORY_PROVIDER"] = "ollama"
    return True


# How hard Groq's gpt-oss thinks before it answers (GROQ_REASONING=auto): hard when planning the story (few requests,
# and where a story gets well thought out), a fair amount for each scene, briefly for small fixes.
THINKING = {"plan": "high", "write": "medium", "fix": "low"}
LESS_THINKING = {"high": "medium", "medium": "low"}


def thinking_for(cfg, think):
    chosen = str(cfg.get("GROQ_REASONING") or "auto").lower()
    return chosen if chosen in ("low", "medium", "high") else THINKING.get(think or "write", "medium")


def ask_writer(cfg, prompt, as_json=True, system=None, think=None):
    """Send one prompt to the chosen writer. Returns its JSON reply (one retry if malformed), or with
    as_json=False its plain text. `system` is a standing instruction for the writer. `think` says what kind of
    request it is — "plan" (the plot), "write" (a scene) or "fix" (spelling, sentences, sound cues) — so a writer
    that thinks first (Groq's gpt-oss) can think as hard as the job needs."""
    provider = cfg["STORY_PROVIDER"].lower()
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    for attempt in range(2):
        if provider == "claude":
            r = post_patiently(
                "Claude", "https://api.anthropic.com/v1/messages",
                {"x-api-key": need(cfg, "ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
                {"model": cfg["CLAUDE_MODEL"], "max_tokens": 12000, **({"system": system} if system else {}),
                 "messages": [{"role": "user", "content": prompt}]},
                300)
            if r.status_code != 200:
                raise RuntimeError(f"Claude API error {r.status_code}: {r.text[:500]}")
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
        elif provider == "gemini":
            r = post_patiently(
                "Gemini", f"{GEMINI_BASE}/models/{cfg['GEMINI_TEXT_MODEL']}:generateContent",
                {"x-goog-api-key": need(cfg, "GEMINI_API_KEY"), "content-type": "application/json"},
                {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                 **({"systemInstruction": {"parts": [{"text": system}]}} if system else {}),
                 "generationConfig": {"responseMimeType": "application/json"} if as_json else {}},
                300)
            if r.status_code != 200:
                raise RuntimeError(f"Gemini API error {r.status_code}: {r.text[:500]}")
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
        elif provider == "openai":
            r = post_patiently(
                "OpenAI", "https://api.openai.com/v1/chat/completions",
                {"Authorization": f"Bearer {need(cfg, 'OPENAI_API_KEY')}", "content-type": "application/json"},
                {"model": cfg["OPENAI_TEXT_MODEL"], "messages": msgs,
                 **({"response_format": {"type": "json_object"}} if as_json else {})},
                300)
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI API error {r.status_code}: {r.text[:500]}")
            text = r.json()["choices"][0]["message"]["content"]
        elif provider == "groq":
            effort = thinking_for(cfg, think) if "gpt-oss" in cfg["GROQ_MODEL"] else None
            while True:
                try:
                    r = post_patiently(
                        "Groq", "https://api.groq.com/openai/v1/chat/completions",
                        {"Authorization": f"Bearer {need(cfg, 'GROQ_API_KEY')}", "content-type": "application/json"},
                        {"model": cfg["GROQ_MODEL"], "max_tokens": groq_budget(cfg, msgs), "messages": msgs,
                         # gpt-oss thinks before it writes, and the thinking counts against the allowance too
                         **({"reasoning_effort": effort} if effort else {}),
                         **({"response_format": {"type": "json_object"}} if as_json else {})},
                        300, msgs)
                except WriterLimit:
                    if not _switch_to_ollama(cfg):
                        raise
                    return ask_writer(cfg, prompt, as_json, system, think)
                if r.status_code != 200:
                    raise RuntimeError(f"Groq API error {r.status_code}: {r.text[:500]}")
                choice = r.json()["choices"][0]
                text = (choice.get("message") or {}).get("content") or ""
                if choice.get("finish_reason") == "length" and effort in LESS_THINKING:
                    # it thought so long the answer was cut off: ask again, thinking a little less
                    log(f"Groq thought so long its answer got cut off; asking again with {LESS_THINKING[effort]} thinking…")
                    effort = LESS_THINKING[effort]
                    continue
                break
        elif provider == "ollama":
            if cfg.get("OLLAMA_FALLBACK_URL"):
                pick_ollama(cfg)
            try:
                r = requests.post(f"{cfg['OLLAMA_URL'].rstrip('/')}/api/chat",
                                  json={"model": cfg["OLLAMA_MODEL"], "stream": False,
                                        **({"format": "json"} if as_json else {}),
                                        "keep_alive": cfg.get("OLLAMA_KEEP_ALIVE", "30s"),
                                        "options": {"num_ctx": 12288, "num_predict": 8000,
                                                    **({} if as_json else {
                                                        # a strong penalty on repeating words makes small models drop
                                                        # "the", "to", "was"… and write word salad: keep it light
                                                        "temperature": min(0.85, float(cfg.get("OLLAMA_TEMPERATURE") or 0.8)),
                                                        "top_p": 0.92, "min_p": 0.05,
                                                        "repeat_penalty": 1.05, "repeat_last_n": 64})},
                                        "messages": msgs},
                                  timeout=1800)
            except requests.ConnectionError:
                # the Mac went to sleep partway through: carry on with the backup, if there is one
                if cfg.get("OLLAMA_FALLBACK_URL") and not cfg.get("_OLLAMA_RETRIED"):
                    pick_ollama(cfg, wake=True, fresh=True)
                    return ask_writer({**cfg, "_OLLAMA_RETRIED": "1"}, prompt, as_json, system, think)
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


# ── names: a fresh set for every story, so the same few names don't keep coming back ──
FEMALE_NAMES = """Ada Adaeze Adelaide Aisha Alana Alba Alma Amara Amelie Anais Annika Aria Ashlyn Astrid Aurelia Ava Beatrix
Bianca Bree Briar Callie Camila Carmen Cassia Celeste Clara Coralie Dahlia Daphne Delia Eden Eliza Elodie Emeka Esme
Estelle Etta Evangeline Farah Felicity Fiona Freya Gemma Georgia Greta Hadley Hana Harper Hazel Helena Imani Ines Ingrid
Iris Isadora Ivy Jada Jasmine Joelle Josephine Juniper Kaia Kalani Keira Kiara Lainey Lana Leah Leila Lena Liv Lorelei
Lucia Luna Lydia Mabel Maeve Magnolia Maren Marisol Matilda Maya Meadow Mei Mila Mirela Nadia Naomi Nell Nia Nina Noemi
Octavia Odette Olive Ophelia Paloma Pearl Penelope Phoebe Piper Poppy Priya Quinn Rae Ramona Raven Reese Renata Rhea
Rosalind Rowan Ruby Sabine Sadie Saoirse Selah Selena Serena Sienna Simone Sloane Sofia Stella Talia Tamsin Tess Thea
Valentina Vera Violet Willa Wren Ximena Yara Yasmin Zadie Zara Zoe Zuri""".split()
MALE_NAMES = """Aarav Abel Ace Adrian Alaric Alden Alejandro Amir Anders Andre Ansel Archer Arlo Asher Atticus August Axel Beau
Bennett Bodhi Brooks Caleb Callum Calvin Cassius Cedric Cillian Colt Conrad Cormac Cyrus Dalton Damon Dante Darius Declan
Desmond Dorian Eamon Elias Elliot Emeric Emil Emmett Enzo Ezra Felix Finn Fletcher Gideon Graham Grady Gunnar Hale Harlan
Hayes Hendrix Hugo Idris Ignatius Ike Isaac Jasper Jonah Jude Julian Kai Kaleb Keanu Kellan Kian Knox Lachlan Landon Leif
Leo Levi Lorenzo Luca Lucian Magnus Malik Marcus Mateo Micah Miles Nash Nico Nikolai Oisin Omar Orion Oscar Otis Pax
Pierce Quentin Rafael Reid Remy Rhys Roman Rowan Rufus Ryker Santiago Sawyer Silas Soren Tate Teo Theo Thiago Tobias Tristan
Vance Vaughn Victor Wade Wells Wesley Wilder Xavier Yusuf Zane Zeke""".split()
SURNAMES = """Abara Achterberg Adeyemi Alcott Alvarez Ashford Bader Ballard Barlow Beaumont Bishop Blackwood Bramble Brennan
Calloway Carrow Castellano Chandra Cole Corbin Crane Dalca Delacroix Devereux Donovan Draper Ellery Ellison Esposito Everly
Fairfax Farrow Fennimore Ferreira Fitzgerald Fontaine Garrison Greer Hale Halloran Harrow Hawthorne Holloway Ibarra Iyer
Jansen Kaur Kensington Kowalski Lancaster Larkin Lowell Lucero Mackenzie Madsen Marchetti Mercer Monroe Moreau Nakamura
Navarro Nightingale Novak Okafor Oduya Pemberton Petrov Quill Ramsey Reyes Rhodes Rios Rourke Salazar Sato Sinclair Solberg
Sterling Sullivan Tanaka Thorne Torres Vance Vasquez Vega Whitlock Winslow Wolfe Yates Zamora""".split()
OVERUSED = {"mira", "jake", "elara", "lily", "sam", "luna", "kai", "lyra", "aria", "ethan", "emma", "alex", "jack",
            "sarah", "max", "zara", "elena", "liam", "mia", "eli", "evelyn", "seraphina", "orion", "caleb", "marcus"}


def recent_names(cfg):
    f = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / ".recent-names.json"
    try:
        return f, json.loads(f.read_text())
    except (OSError, ValueError):
        return f, []


def name_ideas(cfg, seed=None):
    """A random handful of first names and surnames for this story, skipping overused ones and names from your
    recent stories."""
    _, used = recent_names(cfg)
    skip = OVERUSED | {n.lower() for n in used}
    rnd = random.Random(seed)
    pick = lambda pool, n: rnd.sample([x for x in pool if x.lower() not in skip] or pool, n)
    return pick(FEMALE_NAMES, 6), pick(MALE_NAMES, 6), pick(SURNAMES, 6)


def names_rule(cfg, seed=None):
    f, m, last = name_ideas(cfg, seed)
    return (f"\nNames: give new characters fresh, fitting names, not the usual ones (never Mira, Jake, Elara, Lily, Sam, "
            f"Luna, Kai or Lyra unless the reader asked). Choose from these where they fit: women {', '.join(f)}; "
            f"men {', '.join(m)}; surnames {', '.join(last)}. Names the reader gave keep exactly as written.\n")


def remember_names(cfg, story):
    """Names just used go on the recent list, so the next stories pick others."""
    f, used = recent_names(cfg)
    new = [c["name"].split()[0] for c in story.get("cast") or [] if c.get("name")]
    used = [n for n in used if n not in new] + new
    try:
        f.write_text(json.dumps(used[-40:]))
    except OSError:
        pass


WRITING_CRAFT = ("\nCraft: write clean, natural English. Every sentence is complete and grammatical, with correct spelling "
                 "and no run-ons, fragments by accident, repeated words or jumbled word order.\n"
                 "Sentence structure: one clear idea per sentence. Most sentences 8 to 25 words, none over 35. Vary "
                 "the length and the opening: never start three sentences in a row with the same word. Keep the subject "
                 "and its verb close together. No comma splices: two complete thoughts get a full stop, a semicolon, "
                 "or a joining word (and, but, so). Don't chain more than two 'and's in one sentence. Stay in the past "
                 "tense and in one point of view. Every pronoun clearly points to one person.\n"
                 "Dialogue: it's always clear who is speaking. Give a speaker's first line in each exchange a short "
                 "speech tag with their name (\"Mira said\"); start a new paragraph whenever the speaker changes; "
                 "every spoken line is inside “double quotes”. Each character keeps their own consistent way of "
                 "talking.\n")

PROOF_PROMPT = """You are a careful copy editor. Fix this passage of a story so it reads cleanly:
- spelling, grammar and punctuation;
- broken, run-on, jumbled or repeated sentences and words;
- dialogue: make sure it's always clear who is speaking (add a short speech tag like "Mira said" where it isn't),
  each speaker keeps their own way of talking, and every line of dialogue is in “double quotes”.
Change nothing else: keep the events, the details, the length, the style, the characters' names and how explicit
it is. Don't add or remove scenes, don't soften or summarize, don't comment. Reply with only the corrected passage.
{extra}
---
{text}
---"""


def proofread(cfg, text, system=None, extra=""):
    """A copy editor's pass over one scene. If the result looks wrong (much shorter or longer), the original is kept."""
    if cfg.get("PROOFREAD", "on") != "on" or cfg["STORY_PROVIDER"].lower() == "demo" or len(text.split()) < 40:
        return text
    try:
        new = clean_prose(ask_writer(cfg, PROOF_PROMPT.format(text=text, extra=extra), as_json=False, system=system, think="fix"))
    except Exception as e:
        log(f"Proofreading skipped ({str(e)[:100]})")
        return text
    a, b = len(text.split()), len(new.split())
    return new if 0.85 * a <= b <= 1.25 * a else text


# ── sentence checker: finds badly built sentences by rule (no AI), then the writer fixes just those ──
_ABBR = r"(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|Prof|Capt|Lt|Sgt|Col|Gen|Mt|vs|etc|e\.g|i\.e|a\.m|p\.m|U\.S|No)"
_SENT_END = re.compile(r"([.!?…]+[\"”’)]*)\s+(?=[\"“‘(]?[A-Z0-9])")
_REPEAT_OK = {"had", "that", "very", "no", "so", "bye", "ha", "knock", "tick", "tock", "really", "please", "go",
              "come", "yes", "now", "faster", "more", "again", "slowly", "boom", "run", "help", "oh", "wait", "deeper",
              "harder", "never", "away", "out", "back", "over", "on", "can", "do", "is"}
_SPELL = None


FIRST_PERSON = re.compile(r"\b(I|I'm|I'd|I'll|I've|me|my|mine|myself)\b")


def narration_only(text):
    return re.sub(r"“[^”]*”|\"[^\"]*\"", " ", text or "")


def detect_pov(texts):
    """"first" when the narration (not the dialogue) says "I", otherwise "third"."""
    bare = narration_only(" ".join(texts))
    words = max(1, len(bare.split()))
    return "first" if len(FIRST_PERSON.findall(bare)) * 100 / words > 1.2 else "third"


def pov_rule(pov):
    return ("Tell it in the first person (\"I\"), from the main character's point of view, in the past tense, all the "
            "way through." if pov == "first" else
            "Tell it in the third person (\"she\", \"he\", \"they\"), in the past tense, all the way through: the "
            "narration never says \"I\" (only characters do, inside quotes).")


def split_sentences(text):
    """The sentences of a passage, in order (keeps the dialogue tags with their quotes)."""
    out = []
    for para in re.split(r"\n\s*\n|\n", text or ""):
        para = para.strip()
        if not para:
            continue
        parts = _SENT_END.sub(lambda m: m.group(1) + "\n", para).split("\n")
        merged = []
        for p in parts:                       # don't split after an abbreviation ("Mr. Hale")
            if merged and re.search(r"\b" + _ABBR + r"\.$", merged[-1]):
                merged[-1] += " " + p
            else:
                merged.append(p)
        out += [m.strip() for m in merged if m.strip()]
    return out


def _speller():
    global _SPELL
    if _SPELL is None:
        try:
            from spellchecker import SpellChecker
            _SPELL = SpellChecker(distance=1)
        except Exception:
            _SPELL = False
    return _SPELL


def tidy_sentences(text):
    """Mechanical fixes that never change the meaning: doubled words, stray spaces, doubled punctuation,
    missing capitals after a full stop, a missing space after a full stop."""
    t = text or ""
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" +([,.;:!?])", r"\1", t)
    t = re.sub(r",{2,}", ",", t)
    t = re.sub(r"(?<!\.)\.\.(?!\.)", ".", t)
    t = re.sub(r"([!?])\1{2,}", r"\1", t)
    t = re.sub(r"\.,|,\.", ".", t)

    t = re.sub(r"\b([A-Za-z]+)(?:\s+\1\b)+", lambda m: m.group(0) if m.group(1).lower() in _REPEAT_OK
               else m.group(1), t, flags=re.I)                           # "the the" → "the"
    t = re.sub(r"([a-z]{2})([.!?])([A-Z][a-z])", r"\1\2 \3", t)            # "door.She" → "door. She"
    t = re.sub(r"\b([a-z]{3,})([.!?])([a-z]{2,})\b(?<!com)(?<!org)(?<!net)", r"\1\2 \3", t)   # "door.she"

    def cap(m):                                                         # "door. she" → "door. She"
        if re.search(r"\b" + _ABBR + r"\.$", t[max(0, m.start() - 6):m.start() + 1]):
            return m.group(0)
        return m.group(1) + m.group(2).upper()
    t = re.sub(r"(?<![\"”’'.])([.!?]\s+)([a-z])", cap, t)
    lines = t.split("\n")
    for k, ln in enumerate(lines):                                      # a spoken line left open at the paragraph end
        s_ = ln.rstrip()
        if s_.count("“") == s_.count("”") + 1 and s_.rfind("“") > s_.rfind("”") and s_.endswith((".", "!", "?", "…")):
            lines[k] = s_ + "”"
    return "\n".join(lines)


GLUE = {"the", "a", "an", "of", "to", "in", "on", "at", "for", "with", "from", "by", "into", "onto", "her", "his",
        "their", "my", "your", "our", "its", "was", "were", "is", "had", "has", "be", "been", "that", "this"}
COMMON_LONG = {"before", "around", "something", "through", "thought", "looked", "another", "little", "turned",
               "nothing", "against", "between", "without", "everything", "himself", "herself", "toward", "towards",
               "because", "behind", "inside", "outside", "moment", "couldn", "wouldn", "didn", "wasn", "really"}


def garbled(sentence):
    """A sentence that has lost its little words ("she wanted love be happy stir trouble") or trails off in pieces."""
    bare = narration_only(sentence)
    words = re.findall(r"[A-Za-z']+", bare)
    if len(words) >= 9 and not ({w.lower() for w in words} & GLUE):
        return True
    return len(re.findall(r"\.\.\.|…", bare)) >= 2 and len(words) >= 6


def sentence_issues(text, names=(), pov=""):
    """Badly built sentences, by rule. Returns a list of (sentence, problem). Fiction is allowed its style, so
    only clear problems are listed: very long or run-on sentences, comma splices, the same word twice in a row,
    unbalanced quotes, the same sentence twice, three openers in a row, a lowercase start, likely misspellings."""
    issues = []
    sents = split_sentences(text)
    names_l = {str(n).lower() for n in names if n}
    seen = {}
    run_word, run_len = None, 0
    for s in sents:
        words = re.findall(r"[A-Za-z’']+", s)
        n = len(words)
        bare = re.sub(r"“[^”]*”|\"[^\"]*\"", "", s)                       # narration only, without the quotes
        if n > 42:
            issues.append((s, f"too long ({n} words): split it into two or three clear sentences"))
        elif len(re.findall(r"\band\b", bare, re.I)) >= 4:
            issues.append((s, "strings too many clauses together with 'and': split it or restructure it"))
        elif re.search(r"[a-z], (?:he|she|they|I|we|it|you|then)\s+(?:was|were|is|had|did|could|would|felt|"
                       r"looked|walked|said|turned|took|went|came|saw|knew|thought|smiled|pulled|pushed)\b", bare) \
                and not re.search(r"\b(?:and|but|or|so|yet|when|while|as|because|if|until|before|after|though|"
                                  r"although|which|who|where|since|once|as soon as)\b[^,]*,\s*(?:he|she|they|I|we|it|you|then)\s", bare, re.I):
            issues.append((s, "comma splice: two complete sentences joined by only a comma"))
        m = re.search(r"\b([A-Za-z]+)\s+\1\b", s, re.I)
        if m and m.group(1).lower() not in _REPEAT_OK:
            issues.append((s, f"the word '{m.group(1)}' twice in a row"))
        if s.count("“") != s.count("”") and not re.search(r"^“[^”]*$|^[^“]*”", s):
            issues.append((s, "the quotation marks don't match: a spoken line is opened or closed wrongly"))
        if s.count('"') % 2:
            issues.append((s, "an odd number of quotation marks"))
        if re.match(r"^[a-z]", s) and not re.match(r"^(?:e\.g|i\.e)", s):
            issues.append((s, "starts with a lowercase letter"))
        if re.match(r"^(?:Because|Although|Whereas|Though)\b", s) and "," not in s and n < 14 and not s.startswith("“"):
            issues.append((s, "a fragment: it starts like the first half of a sentence and stops"))
        if garbled(s):
            issues.append((s, "garbled: words are missing and it doesn't read as a complete idea; rewrite it as a "
                              "full, clear sentence that says what it means"))
        if pov == "third" and FIRST_PERSON.search(narration_only(s)):
            issues.append((s, "the narration slips into the first person ('I'): this story is told in the third "
                              "person; keep 'I' only inside quotes"))
        key = re.sub(r"\W+", " ", s.lower()).strip()
        if n >= 6 and key in seen:
            issues.append((s, "the same sentence appears twice in the scene; remove or change one"))
        seen[key] = 1
        first = (words[0].lower() if words else "")
        if first and first == run_word and first not in ("“",):
            run_len += 1
            if run_len == 3 and first not in ("and", "but"):
                issues.append((s, f"the third sentence in a row that starts with '{words[0]}': vary the openings"))
        else:
            run_word, run_len = first, 1
    counts = {}
    for w in re.findall(r"[a-z]{6,}", narration_only(text).lower()):
        counts[w] = counts.get(w, 0) + 1
    for w, c in sorted(counts.items(), key=lambda kv: -kv[1])[:3]:
        if c >= 5 and w not in names_l and w not in COMMON_LONG:
            first = next((x for x in sents if w in x.lower()), w)
            issues.append((first, f"the word '{w}' is used {c} times in this scene: vary it or cut some"))
    for para in re.split(r"\n\s*\n", text or ""):
        if para.count("“") != para.count("”"):
            issues.append((para.strip()[:220], "this paragraph opens and closes quotes a different number of times"))
    sp = _speller()
    if sp:
        cand = {w for w in re.findall(r"(?<![’'\w])[a-z]{4,}(?![’'\w])", text or "")} - names_l
        bad = []
        for w in sorted(sp.unknown(list(cand)))[:40]:
            fix = sp.correction(w)
            if fix and fix != w and len(fix) >= 4:
                bad.append((w, fix))
        for w, fix in bad[:8]:
            s = next((x for x in sents if re.search(r"\b" + re.escape(w) + r"\b", x)), w)
            issues.append((s, f"possible misspelling: '{w}' (maybe '{fix}'); fix it only if it's really wrong"))
    out, dup = [], set()
    for s, p in issues:
        if (s, p) not in dup:
            dup.add((s, p))
            out.append((s, p))
    return out


SENTENCE_FIX_PROMPT = """You are a line editor. The passage below has these sentence problems:
{issues}

Rewrite the passage fixing ONLY those sentences (split long or run-on sentences, fix splices, quotes, repeated words
and spelling). Keep every other sentence exactly as it is. Keep the events, details, dialogue meaning, names,
style, tone, length and how explicit it is. Don't summarize, soften, add scenes or comment. Every spoken line stays
inside “double quotes”. Reply with only the corrected passage.
{extra}
---
{text}
---"""


def fix_sentences(cfg, text, system=None, names=(), rounds=2, extra="", pov=""):
    """Rule check, then targeted fixes by the writer, up to `rounds` times. Never makes it worse: a fix is kept only
    when it has fewer problems and about the same length."""
    text = tidy_sentences(text)
    if cfg.get("PROOFREAD", "on") != "on" or cfg["STORY_PROVIDER"].lower() == "demo" or len(text.split()) < 40:
        return text
    issues = sentence_issues(text, names, pov)
    for _ in range(rounds):
        if not issues:
            break
        listed = "\n".join(f"{k + 1}. \"{s[:300]}\": {p}" for k, (s, p) in enumerate(issues[:14]))
        try:
            new = clean_prose(ask_writer(cfg, SENTENCE_FIX_PROMPT.format(issues=listed, text=text, extra=extra),
                                         as_json=False, system=system, think="fix"))
        except Exception as e:
            log(f"Sentence fixing skipped ({str(e)[:100]})")
            break
        new = tidy_sentences(new)
        a, b = len(text.split()), len(new.split())
        left = sentence_issues(new, names, pov)
        if not (0.85 * a <= b <= 1.2 * a) or len(left) >= len(issues):
            break
        text, issues = new, left
    if issues:
        log(f"Sentence check: {len(issues)} small thing(s) left in a scene")
    return text


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


# Five kinds of style, from gentlest to strongest. A style used for a gentler story than it was made for is
# softened to fit that story (Explicit → Mature keeps the language but nothing graphic; anything → Safe for work,
# Family or Kids: no swearing, nothing crude, sexual or suggestive).
PROFILES = ["kids", "family", "sfw", "mature", "explicit"]
PROFILE_LEVELS = {"young-kids": "kids", "kids": "kids", "middle-grade": "kids", "family": "family",
                  "teen": "sfw", "adult": "sfw", "mature": "mature", "explicit": "explicit"}
OLD_PROFILES = {"kids": ["kids"], "family": ["kids"], "sfw": ["everyday"], "mature": ["grown"], "explicit": ["grown"]}
GRAPHIC = re.compile(
    r"\b(fuck(?:ed|ing|s)?\b(?= (?:her|him|me|you|them|each))|cunt\w*|dick\w*|cock\w*|pussy|pussies|tits?|titties|"
    r"cum\w*|orgasm\w*|clit\w*|genital\w*|penis\w*|vagina\w*|erect\w*|nipple\w*|thrust\w*|naked|nude\w*|"
    r"blowjob\w*|hard-on|wet (?:for|between)|sex scene\w*|graphic|explicit|smut\w*|raunch\w*|bdsm|"
    r"spank\w*|lick\w*|suck\w*|straddl\w*|undress\w*|panties|lingerie|moan\w*)\b", re.I)
SWEARING = re.compile(r"\b(fuck\w*|shit\w*|damn\w*|goddamn\w*|hell|bitch\w*|bastard\w*|asshole\w*|ass|arse\w*|"
                      r"crap\w*|piss\w*|filth\w*|dirty|crude|cuss\w*|swear\w*)\b", re.I)
SUGGESTIVE = re.compile(r"\b(sex\w*|sensual\w*|erotic\w*|seduc\w*|lust\w*|horny|arous\w*|steamy|sultry|naughty|"
                        r"teas\w*|kiss\w*|kinky|kink|foreplay|grind\w*|strip\w*|breasts?|boob\w*|bra|"
                        r"bed(?:room)? scene|intimate|intimacy|desire\w*|flirt\w*)\b", re.I)
CRUDE = re.compile("|".join(x.pattern for x in (GRAPHIC, SWEARING, SUGGESTIVE)), re.I)
SOFTEN_NOTE = {
    "mature": "keep it to a Mature story: strong language and heat are fine, but nothing graphic or explicit",
    "sfw": "keep it safe for work: no swearing, nothing crude, nothing sexual or suggestive",
    "family": "keep it right for the whole family: no swearing, nothing crude, nothing sexual or suggestive",
    "kids": "keep it right for children: no swearing, nothing crude, nothing romantic, sexual or suggestive",
}


def profile_for(level):
    return PROFILE_LEVELS.get(level, "family")


def get_profile(profiles, name):
    if profiles.get(name):
        return profiles[name]
    for old in OLD_PROFILES.get(name, []):                     # styles saved before the five kinds existed
        if profiles.get(old):
            return profiles[old]
    return {}


def soften(text, target):
    """Keep the words, phrasing and tone, without what doesn't belong in the target kind of story."""
    pat = GRAPHIC if target == "mature" else CRUDE
    text = str(text or "")
    if not pat.search(text):
        return text
    parts = re.split(r"(?<=[.!?…;])\s+|\n+|,\s*", text)
    return ", ".join(p.strip() for p in parts if p.strip() and not pat.search(p))


def style_block(style, level):
    """The reader's own writing style, for every writing prompt: their notes, what they said about earlier stories,
    and passages to match. Each style is made for one kind of story: Kids, Family, Safe for work (teens and
    adults), Mature or Explicit. A style can be shared with the other kinds; used for a gentler story than it was
    made for, it's softened to fit (Kids and Family stories never get passages from Mature or Explicit styles)."""
    profiles = ((style or {}).get("writing") or {})
    own = profile_for(level)
    rank = PROFILES.index
    adult_only = level == "explicit"
    ok = lambda t: not (adult_only and (young_age(t) or MINOR_WORDS.search(t)))    # nothing young, ever
    lines, refs, softened, seen = [], [], False, set()
    order = [own] + [k for k in reversed(PROFILES) if k != own]
    for name in order:
        w = get_profile(profiles, name)
        if not w or id(w) in seen:
            continue
        seen.add(id(w))
        if name != own and not w.get("share", True):
            continue
        gentler = rank(name) > rank(own)                    # made for stronger stories than this one
        fix = (lambda t: soften(t, own)) if gentler else (lambda t: t)
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
        no_passages = gentler and own in ("kids", "family") and name in ("mature", "explicit")
        if not no_passages:
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
        out += ("\n- Some of this style was made for stronger stories. Use its voice, rhythm, phrasing and words, but "
                f"{SOFTEN_NOTE.get(own, SOFTEN_NOTE['sfw'])}; where the style would go further, keep it clean instead.")
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


BEDTIME_RULES = ("\nBedtime story: this is for winding down before sleep. Keep it calm, warm and safe from beginning "
                 "to end: no frightening moments, no villains who threaten, no loud chases or fights, no cliffhanger. "
                 "Gentle wonder and small, easy problems that are solved kindly. Let the pace slow as it goes: shorter, "
                 "softer sentences near the end, with quiet sounds (rain, crickets, a fire, breathing, the sea). Close "
                 "with the characters safe, cosy and settling down to rest, and a soft goodnight feeling.\n")


def bedtime_config(cfg):
    """Bedtime: quieter sounds and music, and the narrator reads a little slower."""
    def scale(key, f):
        try:
            cfg[key] = f"{float(cfg[key]) * f:.3f}"
        except (KeyError, ValueError):
            pass
    scale("MUSIC_VOLUME", 0.6)
    scale("AMBIENCE_VOLUME", 0.75)
    scale("EFFECTS_VOLUME", 0.45)
    scale("CLIP_SOUND_VOLUME", 0.5)
    try:
        cfg["KOKORO_SPEED"] = f"{min(float(cfg['KOKORO_SPEED']), 0.95) * 0.92:.3f}"
    except (KeyError, ValueError):
        pass
    cfg["_BEDTIME"] = True


def write_story(cfg, idea, genre, level, scenes, clip_mode, series=None, chapters=1, job=None, detail="standard"):
    provider = cfg["STORY_PROVIDER"].lower()
    audience, rules, length = level_info(level)
    rules += style_rules(cfg.get("_PREFS"), level)
    rules += style_block(cfg.get("_STYLE"), level)
    rules += WRITING_CRAFT
    if cfg.get("_BEDTIME"):
        rules += BEDTIME_RULES
    if not series:
        rules += names_rule(cfg, seed=str(job) if job else None)
    length *= DETAIL.get(detail, 1.0)
    lo, hi = (20, 28) if clip_mode else (120, 200) if chapters > 1 else (60, 110)   # clips need short narration
    if not clip_mode:
        lo, hi = int(lo * length), int(hi * length)
    series_text = ""
    if series:
        bible = bible_of(series)
        locked = bible_cast(series) or series.get("cast") or []
        fixed = ", ".join(f"{c['name']} ({TYPE_WORDS.get(c.get('voice'), c.get('voice', ''))})"
                          for c in locked if c.get("name")) or "as before"
        series_text = SERIES_BLOCK.format(n=len(done_episodes(series)) + 1, name=series["name"],
                                          style=bible.get("visual_style") or series["visual_style"],
                                          characters=bible_characters(series) or series["characters"],
                                          previously=series_memory(series), cast=fixed)
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
        story["visual_style"] = bible_of(series).get("visual_style") or series["visual_style"]
        recurring = bible_characters(series) or series["characters"]
        if recurring:                           # the bible's exact descriptions first, then anyone new
            known = {re.split(r"[(:,]", c)[0].strip().lower() for c in re.split(r";\s*", recurring) if c.strip()}
            new_ones = [c for c in re.split(r";\s*", story.get("characters", "") or "")
                        if c.strip() and re.split(r"[(:,]", c)[0].strip().lower() not in known]
            story["characters"] = "; ".join([recurring] + new_ones)
    quote_dialogue(story)
    declared = {str(c.get("name", "")).lower() for c in story.get("cast") or [] if isinstance(c, dict)
                and c.get("voice") in VOICE_TYPES}
    if series:
        apply_locked_cast(story, bible_cast(series) or series.get("cast"))
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
    if cfg.get("PROOFREAD", "on") == "on" and provider != "demo":
        system = EXPLICIT_SYSTEM if level == "explicit" else None
        names = [c.get("name") for c in story.get("cast") or [] if isinstance(c, dict)]
        for i, sc in enumerate(story.get("scenes", [])):
            if sc.get("proofread"):
                continue
            report("writing", 0.97, f"Proofreading scene {i + 1} of {len(story['scenes'])}")
            text = proofread(cfg, sc.get("narration", ""), system)
            report("writing", 0.98, f"Checking sentence structure: scene {i + 1} of {len(story['scenes'])}")
            text = fix_sentences(cfg, text, system, names, pov=story.get("pov") or "")
            if level == "explicit":
                try:
                    text = adult_guard(cfg, text, system)
                except SceneRedo:
                    text = sc["narration"]                     # keep the checked version from before
            if text != sc.get("narration"):
                sc["narration"] = text
                sc["lines"] = lines_from_prose(text, story.get("cast") or [])
            sc["proofread"] = True
    remember_names(cfg, story)
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

# In a grown-up book, "young girl" / "young boy" is everyday wording for an adult ("a young girl fresh out of
# college"). Books brought into Audio Studio get "young woman" / "young man" instead, so nothing in an explicit
# book reads as a child. Every other word about youth still stops an explicit book (check_adult_only).
YOUNG_WORDING = re.compile(r"\b(young)(\s+)(girl|boy)(s?)\b", re.I)


def adult_wording(text):
    """("…young woman…", how many were changed)."""
    count = 0

    def swap(m):
        nonlocal count
        count += 1
        word = {"girl": "woman", "boy": "man"}[m.group(3).lower()]
        if m.group(4):
            word = word[:-3] + "men"                       # woman → women, man → men
        if m.group(3).isupper():
            word = word.upper()
        return m.group(1) + m.group(2) + word

    return YOUNG_WORDING.sub(swap, text or ""), count


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
        new = clean_prose(ask_writer(cfg, CLEAN_PROMPT.format(text=text), as_json=False, system=system, think="fix"))
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


def _where_in(text, start, end, room=90):
    """The words around a match, so a long book shows which sentence it's in."""
    if len(text) <= 2 * room + (end - start):
        return ""
    a, b = max(0, start - room), min(len(text), end + room)
    snip = re.sub(r"\s+", " ", text[a:b]).strip()
    return f" (“{'…' if a else ''}{snip}{'…' if b < len(text) else ''}”)"


def check_adult_only(text, where="what you typed"):
    """Explicit stories are for adult characters only. Stop before anything is written if the text gives an age
    under 20 or suggests a minor."""
    text = text or ""
    age = young_age(text)
    if age:
        i = text.find(age)
        raise MinorContent(f"Explicit stories are for adults only (20 and over), and {where} mentions “{age}”"
                           f"{_where_in(text, i, i + len(age)) if i >= 0 else ''}. Nothing was made. Make every "
                           "character 20 or older, or use another level.")
    m = MINOR_WORDS.search(text)
    if m:
        raise ValueError(f"Explicit stories are for adult characters only, and {where} mentions “{m.group(0)}”"
                         f"{_where_in(text, m.start(), m.end())}. Nothing was made. Reword that part (for example "
                         "“college” for “high school”, “young woman” for “young girl”), or use another level.")


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


def shingles(text, n=5):
    w = re.findall(r"[a-z']+", (text or "").lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def scene_problems(text, earlier, pov):
    """What's wrong enough with a freshly written scene to write it again: it repeats an earlier scene, it's word
    salad, or it's told from the wrong point of view. Returns a note for the writer ("" if it's fine)."""
    notes = []
    sh = shingles(text)
    for k_, old in enumerate(earlier[-12:]):
        o = shingles(old)
        if sh and o and len(sh & o) / max(1, min(len(sh), len(o))) > 0.2:
            notes.append("The last attempt repeated a scene that already happened. Write only what happens NEXT, "
                         "with new events and new words.")
            break
    sents = split_sentences(text)
    bad = sum(1 for x in sents if garbled(x))
    if sents and bad / len(sents) > 0.08:
        notes.append("The last attempt had broken sentences with missing words. Write in plain, complete, "
                     "grammatical sentences.")
    if pov:
        other = detect_pov([text])
        if other != pov:
            notes.append("The last attempt switched point of view. " + pov_rule(pov))
    return " ".join(notes)


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
            keywords=keywords_block(cfg.get("_KEYWORDS"), whole=True)), system=system, think="plan")
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
    pov = outline.get("pov") or (cfg.get("_PREFS") or {}).get("pov") or ""
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
                redo_note = ""
                for fresh in range(3):                    # a scene that goes wrong is written again
                    wcfg = dict(cfg, OLLAMA_TEMPERATURE="0.6") if redo_note else cfg     # calmer the second time
                    text = clean_prose(ask_writer(wcfg, SCENE_PROMPT.format(
                        **fmt, characters=outline.get("characters", ""), plan=plan, n=k, total=chapters,
                        chapter_title=ch.get("title", ""), summary=ch.get("summary", ""), k=j, scenes=scenes,
                        beat=beat, previous=prev, words=target, pov=pov_rule(pov) if pov else "",
                        extra=extra + (ADULT_REDO if fresh and adult else "") + (redo_note + " " if redo_note else ""),
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
                    if not pov:                               # the first scene sets the point of view for the rest
                        pov = detect_pov([text])
                        outline["pov"] = pov
                        if plan_file:
                            plan_file.write_text(json.dumps(outline, indent=2))
                    redo_note = scene_problems(text, [x["narration"] for x in all_scenes], pov) if fresh < 2 else ""
                    if redo_note:
                        log(f"Chapter {k}, scene {j} written again: {redo_note[:120]}")
                        report("writing", 0.03 + 0.97 * done / total, f"Writing chapter {k}, scene {j} again (better)")
                        continue
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
                keywords="", words=len(sc["narration"].split()), extra=extra,
                fresh="Change as little as you need to.", pov=pov_rule(pov) if pov else ""),
                as_json=False, system=system))
            if len(new.split()) > 0.6 * len(sc["narration"].split()):
                if adult:
                    try:
                        new = adult_guard(cfg, new, system)
                    except SceneRedo:
                        continue                            # keep the scene as it was
                sc["narration"], sc["lines"] = new, lines_from_prose(new, cast)
    story = {k_: outline.get(k_, "") for k_ in ("title", "logline", "visual_style", "characters")}
    story.update(cast=cast, scenes=all_scenes, pov=pov or detect_pov([x["narration"] for x in all_scenes]))
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
            system=EXPLICIT_SYSTEM if cfg.get("_ADULT_ONLY") else None, think="plan")
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
    """How long a sound or video is, in seconds. Never fails on a file that doesn't say (ffprobe answers "N/A" for
    an empty recording, a picture or a stream): it's then measured by reading it through, and 0 if there's nothing."""
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)]).stdout.strip()
    try:
        return max(0.0, float(out))
    except ValueError:
        pass
    res = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    times = re.findall(r"time=(\d+):(\d+):([\d.]+)", res.stderr)
    if not times:
        return 0.0
    h_, m_, s_ = times[-1]
    return int(h_) * 3600 + int(m_) * 60 + float(s_)


def has_audio(path):
    return bool(run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                     "-of", "csv=p=0", str(path)]).stdout.strip())


def valid_media(path):
    try:
        return Path(path).exists() and Path(path).stat().st_size > 0 and duration(path) > 0.02
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


PICTURE_UNSAFE = re.compile(
    GRAPHIC.pattern[:-3] + r"|sex\w*|erotic\w*|horny|arous\w*|strip\w*|breasts?|boob\w*|bras?|foreplay|grind\w*|"
    r"kink\w*|naughty|seduc\w*|lust\w*|topless|shirtless|bare[- ]chested|bare skin|unclothed|undress\w*|"
    r"nightgown|negligee|towel|bath|bathtub|bathing|shower\w*|(?:bed|silk|satin) ?sheets|between the sheets|"
    r"under the covers|in bed|on the bed|bedroom|cleavage|thighs?|hips|curves?|curvy|busty|writh\w*|entwined|"
    r"tangled (?:together|in each other|limbs)|on top of (?:him|her|me|each other)|skin to skin|"
    r"(?:his|her|their) bod(?:y|ies) (?:against|pressed|entwined)|touch\w*|caress\w*|stroke\w*|grop\w*|bite|biting|"
    r"young|youthful|petite|tiny|little|girl\w*|boy\w*|teen\w*|school\w*|child\w*|kid\w*)\b", re.I)


# A fight, an argument, a chase: pictures should show the drama (fists, shouting, pointing, struggling).
CONFLICT = re.compile(r"\b(fight\w*|fought|punch\w*|kick\w*|slam\w*|shov\w*|wrestl\w*|struggl\w*|brawl\w*|"
                      r"shout\w*|yell\w*|scream\w*|roar\w*|argu\w*|confront\w*|furious|rage|raging|glar\w*|"
                      r"jab\w*|point\w* (?:at|a finger)|hurl\w*|throw\w*|threw|swing\w*|swung|charg\w*|lung\w*|"
                      r"tackl\w*|grabb?\w*|chas\w*|duel\w*|sword\w*|gun\w*|aim\w*|attack\w*|clash\w*|battle\w*)\b",
                      re.I)


def picture_safe(prompt, level):
    """A picture prompt for a grown-up story, with every part that could lead to an unsuitable picture taken out.
    Whatever is left is about the place, the mood and the people, fully clothed."""
    text = str(prompt or "")
    if level not in ("mature", "explicit") or not PICTURE_UNSAFE.search(text):
        return text
    bits = re.split(r"((?<=[.!?;])\s+|,\s*|\s+(?:and|while)\s+)", text)
    out = ""
    for k in range(0, len(bits), 2):                       # each part with the separator after it
        part, sep = bits[k], (bits[k + 1] if k + 1 < len(bits) else "")
        if part.strip() and not PICTURE_UNSAFE.search(part):
            out += part + sep
    return re.sub(r"(?:,\s*|\s+(?:and|while)\s*)$", "", out.strip()).strip()


def story_seed(story, extra=0):
    """The same seed for every picture of a story, so faces, clothes and the look stay the same."""
    return (int(hashlib.sha1(str(story.get("title", "")).encode()).hexdigest()[:7], 16) + extra) % 2_000_000_000


ILLUSTRATE = ("illustrated scene with the characters in action: full figures, expressive faces and body language, "
              "a clear moment of action, dynamic composition, storybook illustration")
DEFAULT_LOOK = "vivid storybook illustration, expressive characters, ink and watercolour, rich colour, warm light"

# How the pictures look. Storybook keeps the writer's own look for the story; Realistic and Cinematic replace it,
# so a "Pixar-style" or "watercolour" look from the writer can't turn a realistic picture back into a cartoon.
PICTURE_STYLES = {
    "storybook": {"name": "Storybook", "shot": ILLUSTRATE, "look": DEFAULT_LOOK, "neg": "", "keep_writer_look": True},
    "realistic": {
        "name": "Realistic",
        "shot": ("a candid photograph of the characters in the middle of the action: real people, full figures, natural "
                 "expressions and body language, a real moment caught on camera"),
        "look": ("photorealistic, true-to-life detail, natural skin texture, realistic proportions, natural light, "
                 "35mm photograph, sharp focus, depth of field"),
        "neg": ("cartoon, illustration, drawing, painting, sketch, anime, manga, 3d render, cgi, plastic skin, doll, "
                "toy, comic, watercolor, oversaturated"),
        "keep_writer_look": False},
    "3d": {
        "name": "3D animated",
        "shot": ("a frame from a 3D animated feature film: the characters in the middle of the action, full figures, "
                 "exaggerated expressive faces and body language, a dynamic moment"),
        "look": ("3D animated film still, stylized CGI characters with appealing proportions, soft global illumination, "
                 "subsurface scattering, rich colour, detailed textures, high-end 3D render, depth of field"),
        "neg": ("photograph, photorealistic, real people, flat 2d, sketch, line art, watercolor, painting, anime, "
                "low poly, plastic toy, uncanny"),
        "keep_writer_look": False},
    "epic": {
        "name": "Epic action",
        "shot": ("the decisive instant of the action, frozen in dramatic slow motion: full figures mid-strike, mid-leap "
                 "or mid-clash, raw effort and emotion on their faces, a heroic low camera angle"),
        "look": ("epic action film still, high contrast, intense rim lighting, dust, sparks and debris in the air, "
                 "motion blur on the fastest movement, bold colour grade, dramatic sky, highly detailed"),
        "neg": ("cartoon, flat 2d, sketch, line art, watercolor, painting, anime, calm pose, standing still, "
                "plastic skin, doll, toy, gore, dismemberment"),
        "keep_writer_look": False},
    "cinematic": {
        "name": "Cinematic",
        "shot": ("a film still of the characters in the middle of the action: real people, full figures, expressive "
                 "faces, a dramatic moment, cinematic composition"),
        "look": ("cinematic film still, photorealistic, dramatic lighting, shallow depth of field, anamorphic lens, "
                 "colour graded, atmospheric, highly detailed"),
        "neg": ("cartoon, illustration, drawing, painting, sketch, anime, manga, 3d render, cgi, plastic skin, doll, "
                "toy, comic, watercolor, flat lighting"),
        "keep_writer_look": False},
}


def picture_style(story_or_cfg):
    key = str((story_or_cfg or {}).get("picture_style") or (story_or_cfg or {}).get("PICTURE_STYLE") or "storybook")
    return key.lower() if key.lower() in PICTURE_STYLES else "storybook"


def characters_in(story, text, most=3):
    """Only the characters who are in this moment (a picture told about all twelve draws a crowd of look-alikes).
    Falls back to the first two, the leads."""
    chars = [c.strip() for c in re.split(r";\s*|\n+", story.get("characters", "") or "") if c.strip()]
    if len(chars) <= most:
        return "; ".join(chars)
    text_l = (text or "").lower()
    named = [c for c in chars if (n := re.split(r"[(:,–-]", c)[0].strip().lower()) and len(n) > 1
             and re.search(r"\b" + re.escape(n.split()[0]) + r"\b", text_l)]
    return "; ".join((named or chars[:2])[:most])

SHOT_PLAN_PROMPT = """You are the illustrator of the story "{title}". Look of every picture: {style}.
The characters always look like this: {characters}
For each numbered scene below, describe ONE illustration of its most dramatic or telling moment: which characters
are in it (by name), exactly what each is doing (a clear action, mid-movement: running, reaching, laughing,
arguing, shouting, pointing in anger, throwing a punch, grappling, embracing), their expressions, where they are,
and the light. In a fight or an argument, show the clash itself, not the calm before or after. One sentence of 25 to 45 words. Only
things a picture can show; no words or signs in the picture. {extra}
Reply with ONLY JSON: {{"pictures": ["...", "..."]}} with exactly {n} entries, in order.

{scenes}"""


def plan_shots(cfg, story):
    """Before drawing: an illustration idea for every scene, written from the finished scene itself (who is there,
    doing what), so the pictures show the characters acting out the story, not an empty place or the plot summary.
    Asked once per scene (kept in the story); a scene the writer misses keeps its first idea."""
    if cfg["STORY_PROVIDER"].lower() == "demo":
        return
    scenes = story.get("scenes") or []
    todo = [i for i, sc in enumerate(scenes) if not sc.get("shot_planned")]
    level = story.get("level", "family")
    extra = ("Grown-up story: everyone is an adult and fully clothed; show the mood (a look, a touch of hands, a "
             "kiss at most), never anything sexual." if level in ("mature", "explicit") else
             "Keep it gentle and friendly for children." if level in ("young-kids", "kids") else "")
    system = EXPLICIT_SYSTEM if level == "explicit" else None
    for n0 in range(0, len(todo), 6):
        batch = todo[n0:n0 + 6]
        report("video", 0.0, f"Planning the illustrations: {n0 + len(batch)} of {len(todo)} scenes")
        listed = "\n\n".join(f"{k + 1}. {scenes[i].get('narration', '')[:1100]}" for k, i in enumerate(batch))
        try:
            got = ask_writer(cfg, SHOT_PLAN_PROMPT.format(
                title=story.get("title", ""),
                style=(story.get("visual_style") or DEFAULT_LOOK) if picture_style(story) == "storybook"
                else PICTURE_STYLES[picture_style(story)]["look"],
                characters=story.get("characters", ""), extra=extra, n=len(batch), scenes=listed), system=system,
                think="fix")
            got = got.get("pictures") if isinstance(got, dict) else []
        except Exception as e:
            log(f"Illustration ideas skipped for a few scenes ({str(e)[:100]})")
            got = []
        for k, i in enumerate(batch):
            idea = str(got[k]).strip() if isinstance(got, list) and k < len(got) else ""
            if len(idea.split()) >= 8:
                scenes[i]["image_prompt"] = idea[:500]
            scenes[i]["shot_planned"] = True


def shot_prompt(story, scene, kind, base=None, local=False):
    base = base or scene.get("video_prompt" if kind == "video" else "image_prompt") or scene.get("image_prompt", "")
    level = story.get("level", "family")
    tail = (" Characters do not speak. No on-screen text, captions, subtitles or watermarks."
            if kind == "video" else " No text, words, letters, captions or watermarks anywhere in the image.")
    if level in ("young-kids", "kids"):
        tail += " Gentle, friendly and child-appropriate; nothing scary or violent."
    us = story.get("user_style") or {}
    ps = PICTURE_STYLES[picture_style(story)]
    look = ". ".join(x for x in (us.get("pictures"), story.get("visual_style", "") if ps["keep_writer_look"] else "",
                                 "" if ps["keep_writer_look"] else ps["look"]) if x)
    if kind == "video" and us.get("video"):
        tail = f" Camera and motion: {us['video']}." + tail
    chars = characters_in(story, f"{base} {scene.get('narration', '')[:600]}") if local else story.get("characters", "")
    if level in ("mature", "explicit"):
        base, chars = picture_safe(base, level), picture_safe(chars, level)
        if len(base.split()) < 4:                             # nothing safe left: a calm moment from the scene itself
            calm = [s_ for s_ in split_sentences(scene.get("narration", "")) if "“" not in s_ and '"' not in s_
                    and len(s_.split()) >= 6 and not PICTURE_UNSAFE.search(s_)]
            base = calm[0] if calm else ""
        if len(base.split()) < 4:                             # or the place and the mood
            base = picture_safe(f"{scene.get('heading', '')}, {story.get('logline', '')}", level) or "a quiet moment"
        mood = "Tense, dramatic, cinematic" if CONFLICT.search(base) else "Atmospheric, cinematic"
        base = "Fully clothed adults, " + base.rstrip(" .") + ". " + mood
    if local:          # picture apps read the start of a prompt most closely: the shot first, then who and the look
        look = look or ps["look"]
        if CONFLICT.search(base) and story.get("level") not in ("young-kids", "kids"):
            tail = (" Intense, dramatic action: bodies in motion, raw emotion on their faces, a dynamic camera angle."
                    + tail)
        return f"{base.rstrip(' .')}. {ps['shot']}. Characters: {chars}. {look}.{tail}"
    return f"Style: {look}. Characters: {chars}. Shot: {base}.{tail}"


# ───────────────────────────── pictures ─────────────────────────────

TASTEFUL = (" Tasteful: fully clothed or covered, no nudity, nothing sexual shown. "
            "Every person is clearly an adult.")
TASTEFUL_NEG = ("nude, nudity, naked, nsfw, explicit, sexual, nipples, genitals, underwear, lingerie, topless, "
                "bare chest, cleavage, see-through, bikini, swimsuit, bed, sex, erotic, child, children, teen, "
                "young-looking, minor, schoolgirl, loli, gore, dismemberment, mutilation")


# Settings that suit each kind of picture model. A fast "Turbo" model pushed with 24 steps and strong guidance comes
# out over-cooked (garish colour, too much going on); a full model with 6 steps comes out muddy.
MODEL_KINDS = [  # (words in the model's file name, kind, steps, guidance, (landscape width, height))
    (("schnell",), "FLUX schnell", 4, 1.0, (1344, 768)),
    (("turbo", "lightning", "hyper", "lcm", "dmd", "flash"), "fast (Turbo)", 6, 1.5, (1024, 576)),
    (("flux",), "FLUX", 20, 3.5, (1344, 768)),
    (("sd3", "sd_3", "stable_diffusion_3"), "SD 3", 28, 4.5, (1344, 768)),
    (("xl", "juggernaut", "realvis", "pony", "illustrious", "noob", "dreamshaper", "playground"), "SDXL", 30, 5.5,
     (1344, 768)),
]
_MODEL = {"name": None, "checked": 0.0}


def local_model(cfg):
    """The model the picture app has loaded (asked once every 10 minutes): Draw Things answers GET / with its
    settings; Automatic1111/Forge answer /sdapi/v1/options."""
    if cfg.get("LOCAL_IMAGE_MODEL"):
        return cfg["LOCAL_IMAGE_MODEL"]
    if _MODEL["name"] is not None and time.time() - _MODEL["checked"] < 600:
        return _MODEL["name"]
    base_url = (cfg.get("LOCAL_IMAGE_URL") or "").rstrip("/")
    name = ""
    for path, key in (("/", "model"), ("/sdapi/v1/options", "sd_model_checkpoint")):
        try:
            r = requests.get(base_url + path, timeout=5)
            v = (r.json() or {}).get(key) if r.ok else None
            if v:
                name = str(v)
                break
        except (requests.RequestException, ValueError, AttributeError):
            continue
    _MODEL.update(name=name, checked=time.time())
    return name


def model_kind(name):
    n = (name or "").lower()
    for words, kind, steps, guidance, size in MODEL_KINDS:
        if any(w in n for w in words):
            return kind, steps, guidance, size
    return ("SD 1.5", 28, 7.0, (912, 512)) if n else ("unknown", 24, 6.0, (1024, 576))


def picture_settings(cfg):
    """(steps, guidance) for the loaded model, unless storymaker.env sets them (LOCAL_IMAGE_STEPS / _CFG)."""
    _, steps, guidance, _ = model_kind(local_model(cfg))
    st, gd = str(cfg.get("LOCAL_IMAGE_STEPS") or "auto"), str(cfg.get("LOCAL_IMAGE_CFG") or "auto")
    if (st, gd) in (("24", "6"), ("4", "1.5")):  # old defaults written into storymaker.env (one size for every
        st = gd = "auto"                         # model; the second pair was for SDXL Turbo only)
    return (int(st) if st.isdigit() else steps), (float(gd) if re.fullmatch(r"[\d.]+", gd) else guidance)


def picture_size(cfg, portrait=False, square=False):
    w, h = model_kind(local_model(cfg))[3]
    if square:
        side = 1024 if w >= 1024 else 768
        return side, side
    return (h, w) if portrait else (w, h)


def local_image(cfg, prompt, w, h, out_path, level="family", seed=-1, style="storybook"):
    """A picture from the image app on your own computer (Draw Things, or Automatic1111/Forge):
    its txt2img API. Free and private. Grown-up stories only ever get tasteful pictures."""
    neg = ("text, letters, words, caption, title, speech bubble, watermark, signature, logo, blurry, deformed, extra "
           "fingers, extra limbs, bad anatomy, empty scene, crowd of identical people, duplicate figures")
    if PICTURE_STYLES.get(style, {}).get("neg"):
        neg += ", " + PICTURE_STYLES[style]["neg"]
    avoid = (((cfg.get("_STYLE") or {}).get("pictures") or {}).get("avoid") or "").strip()
    if avoid:
        neg += ", " + _clip(avoid, 200)
    if level in ("mature", "explicit"):
        prompt = picture_safe(prompt, level) + TASTEFUL       # never anything sexual, whatever the prompt said
        neg = TASTEFUL_NEG + ", " + neg
    elif level in ("young-kids", "kids", "middle-grade", "family"):
        neg += ", scary, gore, violence"
    try:
        steps, guidance = picture_settings(cfg)
        body = {"prompt": prompt[:1500], "negative_prompt": neg, "width": w, "height": h,
                "steps": steps, "cfg_scale": guidance, "seed": seed, "batch_size": 1}
        if cfg.get("LOCAL_IMAGE_MODEL"):           # a model of your choosing for StoryMaker's pictures
            body["model"] = cfg["LOCAL_IMAGE_MODEL"]                          # Draw Things
            body["override_settings"] = {"sd_model_checkpoint": cfg["LOCAL_IMAGE_MODEL"]}   # Automatic1111/Forge
        r = requests.post(cfg["LOCAL_IMAGE_URL"].rstrip("/") + "/sdapi/v1/txt2img", timeout=900, json=body)
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
    ps = PICTURE_STYLES[picture_style(story)]
    look = ". ".join(x for x in ((story.get("user_style") or {}).get("pictures"),
                                 story.get("visual_style", "") if ps["keep_writer_look"] else ps["look"]) if x)
    logline = story.get("logline", "")
    chars = characters_in(story, logline, most=2)                 # the leads, not the whole cast in a crowd
    if level in ("mature", "explicit"):
        logline, chars = picture_safe(logline, level), picture_safe(chars, level)
    prompt = (f"Book cover: {chars}, {logline.rstrip(' .')}. A single striking moment, the main characters large in "
              f"the frame. {look}. Beautiful, evocative. No text, no title, no lettering.")
    try:
        size = picture_size(cfg, square=True)
        local_image(cfg, prompt, size[0], size[1], raw, level, story_seed(story), style=picture_style(story))
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
    card = out.with_suffix(".card")                 # a title card stood in last time: try drawing it again
    if out.exists() and not (card.exists() and provider != "cards"):
        return out
    sc = story["scenes"][i]
    raw = job / f"raw_{i + 1:02d}.png"
    try:
        if provider == "openai":
            openai_image(cfg, shot_prompt(story, sc, "image"), portrait, raw)
        elif provider == "gemini":
            gemini_image(cfg, shot_prompt(story, sc, "image"), portrait, raw, ref)
        elif provider == "local":
            local_picture(cfg, story, shot_prompt(story, sc, "image", local=True), portrait, raw, story_seed(story))
        else:
            title_card(sc.get("heading", ""), w, h, raw, i)
        card.unlink(missing_ok=True)
    except Exception as e:
        log(f"Picture {i + 1} failed ({e}); using a title card.")
        title_card(sc.get("heading", ""), w, h, raw, i)
        card.write_text("1")
    fit_cover(Image.open(raw), w, h).save(out)
    raw.unlink(missing_ok=True)
    return out


def local_picture(cfg, story, prompt, portrait, raw, seed):
    """A picture from the picture app on this computer, with a second and third try (it may be busy or just
    opening). Raises if it still can't."""
    lw, lh = picture_size(cfg, portrait)
    for attempt in range(3):
        try:
            return local_image(cfg, prompt, lw, lh, raw, story.get("level", "family"), seed, style=picture_style(story))
        except Exception as e:
            if attempt == 2:
                raise
            log(f"Picture app: {str(e)[:120]}. Trying again in 15 seconds.")
            time.sleep(15)


def extra_picture(cfg, story, i, k, sentence, w, h, portrait, job):
    """Another picture for a long scene, of a later moment in it, in the same look (local pictures only)."""
    out = job / f"pic_{i + 1:02d}_{k}.png"
    if out.exists():
        return out
    sc = story["scenes"][i]
    raw = job / f"raw_{i + 1:02d}_{k}.png"
    base = f"{sentence.rstrip(' .!?')}. Same place as before: {sc.get('image_prompt', '')[:240]}"
    try:
        local_picture(cfg, story, shot_prompt(story, sc, "image", base=base, local=True), portrait, raw,
                      story_seed(story, k))
        fit_cover(Image.open(raw), w, h).save(out)
        return out
    except Exception as e:
        log(f"Extra picture for scene {i + 1} skipped ({str(e)[:100]}).")
        return None
    finally:
        raw.unlink(missing_ok=True)


def scene_moments(story, i, n):
    """n - 1 later moments in a scene to draw, as sentences from the text: narration, not dialogue, and (for
    grown-up stories) nothing that a safe picture can't show."""
    sc = story["scenes"][i]
    level = story.get("level", "family")
    sents = [s_ for s_ in split_sentences(sc.get("narration", "")) if "“" not in s_ and '"' not in s_
             and len(s_.split()) >= 6]
    if level in ("mature", "explicit"):
        sents = [s_ for s_ in sents if not PICTURE_UNSAFE.search(s_)]
    names = [c.get("name") for c in story.get("cast") or [] if isinstance(c, dict) and c.get("name")]
    acting = lambda x: (any(nm in x for nm in names) or re.search(r"^(?:She|He|They)\b", x)) and \
        re.search(r"\b\w+ed\b|\b(?:ran|took|held|threw|caught|fell|stood|sat|turned|grabbed|pulled|pushed|"
                  r"kissed|hugged|laughed|cried|reached|opened|climbed|jumped|danced|fought)\b", x)
    out = []
    for k in range(1, n):        # in each part of the scene, a sentence where someone does something, if there is one
        part = [s_ for j, s_ in enumerate(sents) if k / n <= (j + 0.5) / max(1, len(sents)) < (k + 1) / n]
        out.append(next((x for x in part if acting(x)), part[0] if part else ""))
    return out


def local_image_ready(cfg, timeout=8):
    try:
        requests.get(cfg["LOCAL_IMAGE_URL"].rstrip("/") + "/", timeout=timeout)
        return True
    except requests.RequestException:
        return False


def ollama_ready(cfg, timeout=6):
    try:
        r = requests.get(cfg["OLLAMA_URL"].rstrip("/") + "/api/tags", timeout=timeout)
        return [m.get("name", "") for m in (r.json() or {}).get("models") or []]
    except (requests.RequestException, ValueError):
        return None


# Mac apps StoryMaker can ask to be opened: the picture app and the local writer
MAC_APPS = {"draw-things": "Draw Things", "ollama": "Ollama"}

_OLLAMA_CHOICE = {}          # (main address, model) → (address, model, which, when): asked again after 5 minutes


def _has_model(names, want):
    return bool(names) and bool(want) and any(n == want or n.split(":")[0] == want.split(":")[0] for n in names)


def pick_ollama(cfg, wake=True, fresh=False):
    """Which Ollama writes: the Mac's (faster and better) when it's there, otherwise the backup on the server
    (OLLAMA_FALLBACK_URL, with OLLAMA_FALLBACK_MODEL if it doesn't have the same model). Sets cfg's OLLAMA_URL and
    OLLAMA_MODEL to the one picked and returns ("main" | "backup" | None, a few words for the checks)."""
    main_url, want = cfg["OLLAMA_URL"], cfg["OLLAMA_MODEL"]
    key = (cfg.get("_OLLAMA_MAIN") or main_url, want)
    cfg.setdefault("_OLLAMA_MAIN", main_url)
    hit = _OLLAMA_CHOICE.get(key)
    if hit and not fresh and time.time() - hit[3] < 300:
        cfg["OLLAMA_URL"], cfg["OLLAMA_MODEL"] = hit[0], hit[1]
        return hit[2], hit[4]
    main = {**cfg, "OLLAMA_URL": key[0]}
    names = ollama_ready(main, timeout=4)
    if names is None and wake:
        open_mac_app(cfg, "ollama")                       # the Mac's Ollama may just be closed: ask the studio
        names = ollama_ready(main)
    choice = None
    if _has_model(names, want):
        choice = (key[0], want, "main", time.time(), f"Ollama is ready ({want})")
    elif cfg.get("OLLAMA_FALLBACK_URL"):
        back = {**cfg, "OLLAMA_URL": cfg["OLLAMA_FALLBACK_URL"]}
        bnames = ollama_ready(back, timeout=4)
        bwant = next((m for m in (want, cfg.get("OLLAMA_FALLBACK_MODEL")) if _has_model(bnames, m)), None)
        if bwant:
            why = "isn't answering" if names is None else f"doesn't have {want}"
            choice = (cfg["OLLAMA_FALLBACK_URL"], bwant, "backup", time.time(),
                      f"The Mac's Ollama {why}, so the backup Ollama on the server writes ({bwant}; slower)")
            log(choice[4] + ".")
    if not choice:
        return None, ("Ollama isn't running" if names is None else f"Ollama doesn't have the model {want}")
    _OLLAMA_CHOICE[key] = choice
    cfg["OLLAMA_URL"], cfg["OLLAMA_MODEL"] = choice[0], choice[1]
    return choice[2], choice[4]


def open_mac_app(cfg, app):
    """Open Draw Things (or Ollama) when it's needed and closed. StoryMaker runs inside Docker and can't open Mac
    apps itself, so it asks Audio Studio (which runs on the Mac) to; run straight on a Mac, it opens it directly.
    Waits until the app answers. Returns (ok, what happened)."""
    ready = (lambda: local_image_ready(cfg, 4)) if app == "draw-things" else (lambda: ollama_ready(cfg, 4) is not None)
    if ready():
        return True, "already open"
    name = MAC_APPS[app]
    if sys.platform == "darwin":
        if subprocess.run(["open", "-g", "-a", name], capture_output=True).returncode != 0:
            return False, f"{name} isn't installed on this Mac"
    else:
        tok = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / ".helper-token"
        url = (cfg.get("STUDIO_HELPER_URL") or "http://host.docker.internal:8096").rstrip("/") + "/api/helper/open"
        if not tok.exists():
            return False, f"{name} is closed, and Audio Studio isn't running to open it"
        try:
            r = requests.post(url, json={"app": app}, headers={"X-Helper-Token": tok.read_text().strip()}, timeout=150)
            d = r.json()
            return bool(d.get("ok")), d.get("detail") or d.get("error") or ""
        except (requests.RequestException, ValueError):
            return False, f"{name} is closed, and Audio Studio isn't answering to open it"
    for _ in range(45):                                    # it takes a moment to start its server
        time.sleep(2)
        if ready():
            return True, f"opened {name}"
    return False, f"{name} opened but isn't answering: its API Server switch is off"


DRAW_THINGS_FIX = ("Draw Things forgets its API Server switch every time it closes. Audio Studio turns it back on by "
                   "itself once it's allowed to: System Settings → Privacy & Security → Accessibility → turn on "
                   "\"Python\". Or turn it on by hand: in Draw Things, Settings → All → API Server (HTTP, port 7860).")


def preflight(cfg, args, level, vprovider, auto=True):
    """Everything a story needs, checked before it starts: the writer, the voices, the picture app (opened if it's
    closed), the sounds and the disk. Returns [{"item", "ok", "detail", "fix", "blocking"}]."""
    out = []
    add = lambda item, ok, detail, fix="", blocking=True: out.append(
        {"item": item, "ok": ok, "detail": detail, "fix": fix, "blocking": blocking and not ok})
    if cfg.get("LIBRARY_CHECK_FILE") and not Path(cfg["LIBRARY_CHECK_FILE"]).exists():
        add("Library", False, "The NAS isn't connected, so there's nowhere to save the story",
            "Check the NAS is on. On the server: sudo mount -a   (then try again)")
    prov = cfg["STORY_PROVIDER"].lower()
    if prov == "demo":
        add("Writer", True, "demo writer")
    elif prov == "groq":
        if not cfg.get("GROQ_API_KEY"):
            add("Writer", False, "No Groq key", "Add GROQ_API_KEY to ~/StoryMaker/storymaker.env.")
        else:
            try:
                r = requests.get("https://api.groq.com/openai/v1/models", timeout=8,
                                 headers={"Authorization": f"Bearer {cfg['GROQ_API_KEY']}"})
                add("Writer", r.status_code == 200, "Groq is ready" if r.status_code == 200 else
                    f"Groq said no ({r.status_code})", "Check GROQ_API_KEY in storymaker.env (make a new key at "
                    "console.groq.com if needed)." if r.status_code in (401, 403) else "Try again in a minute.")
            except requests.RequestException:
                add("Writer", False, "Can't reach Groq", "Check the internet connection (and turn off the work VPN).")
    elif prov == "ollama":
        which, what = pick_ollama(cfg, wake=auto, fresh=True)
        want = cfg["OLLAMA_MODEL"]
        if which is None and what.startswith("Ollama isn't"):
            add("Writer", False, what, "Open the Ollama app on the Mac" + (
                " (and check the server's backup Ollama is running)." if cfg.get("OLLAMA_FALLBACK_URL") else "."))
        elif which is None:
            add("Writer", False, what, f"In Terminal on the Mac: ollama pull {want}")
        else:
            add("Writer", True, what)
    else:
        key = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(prov)
        add("Writer", bool(not key or cfg.get(key)), f"{prov} is set up" if not key or cfg.get(key) else f"No {key}",
            f"Add {key} to storymaker.env." if key else "")
    if args.mode != "story":
        tts = cfg["TTS_PROVIDER"].lower()
        if tts == "kokoro" or (tts == "auto" and local_tts() == "kokoro"):
            d = Path(os.path.expanduser(cfg["KOKORO_DIR"]))
            have = all((d / n).exists() for n in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"))
            add("Voices", True, "Kokoro voices are ready" if have else "Kokoro voices will download first (one time)",
                blocking=False)
        else:
            add("Voices", True, f"{tts} voices")
    need_pics = (args.mode == "video" and vprovider in ("slideshow", "demo")) or args.book
    if need_pics:
        ip = pick_image_provider(cfg)
        if ip == "local":
            ok = local_image_ready(cfg)
            what = "Draw Things is ready" if ok else "it's closed"
            if not ok and auto:
                ok, what = open_mac_app(cfg, "draw-things")
                what = ("Draw Things wasn't open, so it was opened" if ok else what)
            add("Pictures", ok, what if ok else f"Draw Things isn't answering ({what})", DRAW_THINGS_FIX)
            if ok:
                model = local_model(cfg)
                kind = model_kind(model)[0]
                style = picture_style({"picture_style": getattr(args, "picture_style", None) or cfg.get("PICTURE_STYLE")})
                fast = kind in ("fast (Turbo)", "FLUX schnell")
                add("Picture model", True,
                    f"{model or 'unknown model'} ({kind}) for {PICTURE_STYLES[style]['name'].lower().replace('3d', '3D')} pictures",
                    ("This is a fast sketch model, so pictures won't look very real. For realistic pictures, download a "
                     "realistic model in Draw Things (Juggernaut XL or RealVisXL from its model list) and select it."
                     if fast and style != "storybook" else
                     "StoryMaker couldn't tell which model Draw Things has loaded, so it uses middle-of-the-road "
                     "settings. Put the model's file name in LOCAL_IMAGE_MODEL= in storymaker.env for the best ones."
                     if not model else ""), blocking=False)
        elif ip == "cards":
            add("Pictures", False, "No picture app is set up, so the video would only have title cards",
                "Set up Draw Things (free, on the Mac): " + DRAW_THINGS_FIX + " Then set LOCAL_IMAGE_URL in "
                "storymaker.env. Or make it as audio.")
        else:
            key = "OPENAI_API_KEY" if ip == "openai" else "GEMINI_API_KEY"
            add("Pictures", bool(cfg.get(key)), f"{ip} pictures", f"Add {key} to storymaker.env.")
    if args.mode == "video" and vprovider in ("omni", "veo"):
        add("Video clips", bool(cfg.get("GEMINI_API_KEY")), "Gemini clips", "Add GEMINI_API_KEY to storymaker.env.")
    if args.mode != "story" and cfg["SOUNDSCAPES"] == "on":
        has_fs = bool(cfg.get("FREESOUND_API_KEY"))
        add("Sounds", True, "Freesound and your sounds folder" if has_fs else
            "Only your own sounds folder (no Freesound key)", "" if has_fs else
            "For more sounds, add FREESOUND_API_KEY to storymaker.env.", blocking=False)
    try:
        free = shutil.disk_usage(os.path.expanduser(cfg["OUTPUT_DIR"])).free / 1e9
        add("Disk space", free >= 3, f"{free:.0f} GB free", "Free up some space on the Mac (at least 3 GB).")
    except OSError:
        pass
    return out


def preflight_message(checks):
    bad = [c for c in checks if c["blocking"]]
    return " ".join(f"{c['item']}: {c['detail']}. {c['fix']}" for c in bad)


def make_pictures(cfg, story, job, w, h, portrait, which, ref):
    idxs = list(which)
    if not idxs:
        return {}
    provider = pick_image_provider(cfg)
    step = (_PROG.get("status") or {}).get("step", "video")
    lo = 0.0
    if provider == "local" and any(not (job / f"pic_{i + 1:02d}.png").exists() or
                                   (job / f"pic_{i + 1:02d}.card").exists() for i in idxs):
        if not local_image_ready(cfg):
            report(step, lo, "Opening Draw Things…")
            ok, what = open_mac_app(cfg, "draw-things")
            if not ok:
                raise RuntimeError(f"Draw Things isn't answering ({what}), so the pictures can't be drawn. "
                                   f"{DRAW_THINGS_FIX} Then press Make it again: everything already made is kept.")
        plan_shots(cfg, story)
    elif provider in ("openai", "gemini"):
        plan_shots(cfg, story)
    log(f"Making {len(idxs)} picture(s) with {provider}…")
    step = (_PROG.get("status") or {}).get("step", "video")
    lo, hi = ((0.70, 0.80) if _PROG.get("clips") else (0.0, 0.80)) if step == "video" else (0.05, 0.85)
    count = [0]

    def one(i):
        pic = picture_for(cfg, story, i, w, h, portrait, job, provider, ref)
        count[0] += 1
        report(step, lo + (hi - lo) * count[0] / len(idxs), f"Drawing pictures: {count[0]} of {len(idxs)}", image=pic)
        return i, pic
    workers = 1 if provider == "local" else 3              # the Mac's picture app draws one at a time anyway
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
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


def veo_clip(cfg, prompt, portrait, out_path, ref=None, first_frame=None):
    """Veo 3.1 (long-running operation). Tries with the character sheet, then without. With first_frame (a big
    moment), the clip starts from that scene's own picture, so it moves on from what you've just seen."""
    key = need(cfg, "GEMINI_API_KEY")
    hdr = {"x-goog-api-key": key, "content-type": "application/json"}
    params = {"aspectRatio": "9:16" if portrait else "16:9", "resolution": cfg["VIDEO_RESOLUTION"],
              "durationSeconds": 8}
    attempts = []
    if first_frame is not None and Path(first_frame).exists():
        mime = "image/jpeg" if Path(first_frame).suffix.lower() in (".jpg", ".jpeg") else "image/png"
        attempts.append({"instances": [{"prompt": prompt, "image": {"bytesBase64Encoded": b64file(first_frame),
                                                                    "mimeType": mime}}],
                         "parameters": dict(params, personGeneration="allow_adult")})
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
            log("Veo didn't accept the picture for this clip; trying another way.")
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
    ("am_santa", "Nick", "male", "American", "jolly, deep"),
    # character voices: two voices blended into a new one, each with its own colour
    ("af_bella+bf_isabella", "Scarlett", "female", "Mid-Atlantic", "smoky, sultry"),
    ("af_nicole+af_bella", "Delilah", "female", "American", "breathy, intimate"),
    ("af_heart+bf_emma", "Grace", "female", "Mid-Atlantic", "gentle storyteller"),
    ("af_sky+af_nova", "Pippa", "female", "American", "bubbly, quick"),
    ("bf_alice+bf_isabella", "Margaret", "female", "British", "dignified, low"),
    ("af_kore+af_river", "Vera", "female", "American", "cool, composed"),
    ("af_jessica+af_sarah", "Kayla", "female", "American", "chatty, warm"),
    ("bf_lily+af_sky", "Rosie", "female", "Mid-Atlantic", "playful, light"),
    ("af_aoede+af_heart", "Hazel", "female", "American", "dreamy, soft"),
    ("bf_emma+bf_alice", "Eleanor", "female", "British", "elegant, poised"),
    ("af_alloy+af_kore", "Morgan", "female", "American", "firm, no-nonsense"),
    ("af_nova+af_jessica", "Jade", "female", "American", "sassy, energetic"),
    ("af_river+bf_lily", "Willow", "female", "Mid-Atlantic", "wistful, quiet"),
    ("am_onyx+bm_george", "Victor", "male", "Mid-Atlantic", "commanding, dark"),
    ("am_fenrir+bm_lewis", "Silas", "male", "Mid-Atlantic", "gravelly, weathered"),
    ("am_michael+bm_fable", "Theo", "male", "Mid-Atlantic", "warm narrator"),
    ("am_puck+am_echo", "Finn", "male", "American", "cheeky, quick"),
    ("bm_daniel+am_michael", "Julian", "male", "Mid-Atlantic", "smooth, romantic"),
    ("am_onyx+am_fenrir", "Rook", "male", "American", "menacing, deep"),
    ("am_eric+am_adam", "Cole", "male", "American", "everyman, steady"),
    ("bm_lewis+bm_george", "Edmund", "male", "British", "grandfatherly, wise"),
    ("am_liam+am_puck", "Jesse", "male", "American", "boyish charm"),
    ("bm_fable+bm_daniel", "Oliver", "male", "British", "gentle, thoughtful"),
    ("am_santa+am_onyx", "Bear", "male", "American", "booming, big"),
    ("am_michael+am_fenrir", "Wade", "male", "American", "rugged, warm"),
    ("am_echo+bm_daniel", "Ezra", "male", "Mid-Atlantic", "soft-spoken"),
    # other accents: these voices speak English with their own accent
    ("ff_siwis", "Siwis", "female", "French accent", "soft, elegant"),
    ("ef_dora", "Dora", "female", "Spanish accent", "warm"), ("em_alex", "Alejandro", "male", "Spanish accent", "friendly"),
    ("em_santa", "Santiago", "male", "Spanish accent", "deep"),
    ("if_sara", "Sara", "female", "Italian accent", "bright"), ("im_nicola", "Nicola", "male", "Italian accent", "smooth"),
    ("pf_dora", "Lúcia", "female", "Brazilian accent", "gentle"), ("pm_alex", "Paulo", "male", "Brazilian accent", "easygoing"),
    ("pm_santa", "Rafael", "male", "Brazilian accent", "deep"),
    ("hf_alpha", "Priya", "female", "Indian accent", "clear"), ("hf_beta", "Ananya", "female", "Indian accent", "soft"),
    ("hm_omega", "Arjun", "male", "Indian accent", "calm"), ("hm_psi", "Rohan", "male", "Indian accent", "bright"),
    ("jf_alpha", "Yuki", "female", "Japanese accent (strong)", "light"), ("jm_kumo", "Kenji", "male", "Japanese accent (strong)", "steady"),
    ("zf_xiaobei", "Mei", "female", "Chinese accent (strong)", "bright"), ("zm_yunxi", "Wei", "male", "Chinese accent (strong)", "calm"),
    # lighter accents: an English voice blended with an accented one
    ("af_bella+ff_siwis", "Camille", "female", "light French accent", "warm"),
    ("bf_emma+if_sara", "Giulia", "female", "light Italian accent", "bright"),
    ("af_sarah+pf_dora", "Luiza", "female", "light Brazilian accent", "friendly"),
    ("af_nicole+hf_alpha", "Maya", "female", "light Indian accent", "soft"),
    ("am_michael+em_alex", "Mateo", "male", "light Spanish accent", "warm"),
    ("bm_george+im_nicola", "Marco", "male", "light Italian accent", "classic"),
    ("am_eric+hm_omega", "Dev", "male", "light Indian accent", "confident"),
    ("am_liam+pm_alex", "Lucas", "male", "light Brazilian accent", "young"),
]
def voice_group(v):
    """standard (one of Kokoro's own voices), character (a blend with its own colour) or accent."""
    return "accent" if "accent" in v[3] else "character" if "+" in v[0] else "standard"


VOICE_SPEC = re.compile(r"^[abefhijpz][fm]_[a-z]+(\+[abefhijpz][fm]_[a-z]+)?(\^(0\.[89]\d?|1\.[01]\d?))?$")
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


# ───────────────────────────── sounds people make, said and not spelled ─────────────────────────────
# Stories are full of "Mmmmm…", "HMMM", "Shhh!", "Grrr", "Nooooo" and "W-w-what?". A voice reads those letter by
# letter ("em em em", "S H H") or stumbles. Before anything is read aloud they become the usual written form a voice
# knows how to say; for the ones even that doesn't fix in Kokoro (it would still spell "Mmm" and "Shh"), the exact
# sound is handed over directly. What you read on the page (story text, subtitles, read-along) keeps the original.

# the letters of the word with repeats squeezed ("hmmmm" → "hm", "shhh" → "sh") → (what's read, Kokoro's sound)
SOUND_WORDS = {
    "hm": ("Hmm", "hˈəm"), "hum": None, "m": ("Mmm", "mːː"), "mhm": ("Mm-hmm", "mhˈmː"), "m-hm": ("Mm-hmm", "mhˈmː"),
    "um": ("Um", None), "uhm": ("Um", None), "erm": ("Erm", None), "uh": ("Uh", None), "er": ("Er", None),
    "ah": ("Ah", None), "oh": ("Oh", None), "aw": ("Aww", None), "ew": ("Ew", "ˈiːjuː"), "eh": ("Eh", None),
    "sh": ("Shh", "ʃːː"), "z": ("Zzz", "zːː"), "gr": ("Grr", "ɡɹːː"), "br": ("Brr", "bɹːː"),
    "hmph": ("Hmph", "hˈʌmf"), "humph": ("Hmph", "hˈʌmf"), "hmf": ("Hmph", "hˈʌmf"),
    "pft": ("Pfft", "pfːt"), "pf": ("Pfft", "pfːt"), "psst": ("Psst", None), "pst": ("Psst", None),
    "mwah": ("Mwah", "mwˈɑː"), "muah": ("Mwah", "mwˈɑː"), "ugh": ("Ugh", None), "argh": ("Argh", None),
    "agh": ("Argh", None), "arg": ("Argh", None), "huh": ("Huh", None), "whoa": ("Whoa", None), "woah": ("Whoa", None),
    "phew": ("Phew", None), "yay": ("Yay", None), "yum": ("Yum", None), "wow": ("Wow", None), "ow": ("Ow", None),
    "ouch": ("Ouch", None), "whe": ("Whee", None), "ek": ("Eek", None), "tsk": ("Tsk", None),
}
SOUND_WORDS = {k: v for k, v in SOUND_WORDS.items() if v}
# written forms whose sound Kokoro gets wrong from the letters: the sound to use instead
KOKORO_SOUNDS = {w: ipa for w, ipa in SOUND_WORDS.values() if ipa}
_KOKORO_SOUND_RE = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, KOKORO_SOUNDS), key=len, reverse=True))
                              + r")(?![\w-])")
# short words that are often SHOUTED and would otherwise be spelled out ("NO!" → "No!")
_SHOUTED_SHORT = {"no", "yes", "why", "hey", "go", "run", "now", "who", "me", "you", "not", "out", "get", "off",
                  "but", "how", "yet", "and", "the", "him", "her", "she", "his", "our", "boo", "ow", "oi", "aye"}
_LAUGH = re.compile(r"^(?:mw?u?a)?((?:ha){2,}|(?:he){2,}|(?:hi){2,}|(?:ho){2,})h?$", re.I)


def _match_case(word, like):
    if like.isupper() and len(like) > 1:
        return word[:1].upper() + word[1:].lower()       # SHOUTED: read normally (the ! carries it)
    if like[:1].isupper():
        return word[:1].upper() + word[1:]
    return word.lower() if word not in KOKORO_SOUNDS else word


def _squeeze(w, keep=1):
    return re.sub(r"([a-z])\1+", lambda m: m.group(1) * min(keep, len(m.group(0))), w)


def _say_word(m):
    w = m.group(0)
    low = w.lower()
    if re.fullmatch(r"[ivxlcdm]+", low) and w.isupper():          # Roman numerals ("Henry III") stay
        return w
    laugh = _LAUGH.match(low)
    if laugh:                                                     # "hahaha" → "Ha ha ha", "muahaha" → "Mwah ha ha"
        unit = laugh.group(1)[:2]
        n = min(3, len(laugh.group(1)) // 2)
        out = " ".join([unit] * n)
        if low.startswith(("mwa", "mua")):
            out = "Mwah " + out
        return _match_case(out, w) if not out.startswith("Mwah") else out
    sq = _squeeze(low)
    hit = SOUND_WORDS.get(sq) if low != "err" else None             # (to err is human)
    if hit and (sq != "m" or len(low) >= 2) and (sq != "z" or len(low) >= 3) and not (sq in ("oh", "ah", "aw")
                                                                                        and low.startswith("oo")):
        word = hit[0]
        if sq == "oh" and low.startswith("oo"):
            word = "Ooh"
        return word if word in KOKORO_SOUNDS else _match_case(word, w)
    if sq in ("oh", "ah") and low.startswith("oo"):
        return _match_case("Ooh", w)
    if re.search(r"([a-z])\1\1", low):                            # "Nooooo", "sooo", "pleeease", "heyyy"
        one, two = re.sub(r"([a-z])\1{2,}", r"\1", low), re.sub(r"([a-z])\1{2,}", r"\1\1", low)
        sp = _speller()
        if sp:                                                    # the likelier word: "gooood" → good, "sooo" → so
            f1, f2 = sp.word_frequency[one] if sp.known([one]) else 0, sp.word_frequency[two] if sp.known([two]) else 0
            if f2 > f1:
                return _match_case(two, w)
        return _match_case(one, w)
    if w.isupper() and 2 <= len(w) <= 3 and low in _SHOUTED_SHORT:
        return _match_case(low, w)
    return w


def speakable(text):
    """Sounds and stretched words in a form a voice says naturally (see SOUND_WORDS above)."""
    t = str(text or "")
    # stutters: "W-w-what?" → "… what?", "th-th-that" → "… that" (a voice reads the fragments as letters)
    t = re.sub(r"\b([A-Za-z]{1,2})-(?:\1-)*(\1[a-z]+)\b", lambda m: "… " + m.group(2), t, flags=re.I)
    t = re.sub(r"(?<![\w'])[A-Za-z]+(?:-[A-Za-z]+)?(?![\w'])", _say_word, t)
    return t


def kokoro_text(k, text, lang):
    """For Kokoro: the text, or its sounds with the hard ones (Mmm, Shh, Grr…) put in directly."""
    if not _KOKORO_SOUND_RE.search(text):
        return text, False
    out, pos = "", 0
    for m in _KOKORO_SOUND_RE.finditer(text):
        piece = text[pos:m.start()]
        if piece.strip():
            out += (" " if out and piece[:1].isspace() else "") + k.tokenizer.phonemize(piece, lang)
        out += (" " if out and (piece == "" or piece[-1:].isspace()) and not out.endswith(" ") else "") \
            + KOKORO_SOUNDS[m.group(1)]
        pos = m.end()
    rest = text[pos:]
    if rest.strip():
        out += ("" if rest[:1] in ",.!?;:…" else " ") + k.tokenizer.phonemize(rest, lang)
    return re.sub(r"\s{2,}", " ", out).strip(), True


def tts_clean(text):
    """What a voice reads: no stray symbols or formatting, natural pauses, nothing it would stumble over."""
    t = str(text or "")
    t = re.sub(r"[*_#`~|<>\[\]{}]+", "", t)                    # markdown and stray symbols
    t = re.sub(r"\s*[—–]\s*", ", ", t)                          # dashes read as a pause
    t = re.sub(r"\.{4,}|…", "...", t)
    t = re.sub(r"([!?])\1+", r"\1", t)                          # "!!!" → "!"
    t = speakable(t)                                             # "Mmmm", "HMMM", "Shhh", "Nooo" said, not spelled
    t = re.sub(r"\b([A-Z]{4,})\b", lambda m: m.group(1).capitalize(), t)   # SHOUTED words read normally
    t = re.sub(r"\b(\w+)( \1\b)+", lambda m: m.group(0) if m.group(1).lower() in (
        "had", "that", "is", "do", "very", "ha", "he", "hi", "ho", "tsk", "knock", "bye", "choo", "tick", "la", "na",
        "no", "yes", "go", "run", "ding", "boo", "ho", "come", "now", "please", "help", "wait", "stop", "hurry")
               else m.group(1), t, flags=re.I)                   # "the the" (but "had had", "ha ha" are fine)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,.!?;:])(?=[A-Za-z])", r"\1 ", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", t)    # emoji
    return re.sub(r"\s{2,}", " ", t).strip(" ,") or str(text or "")


def narrate(cfg, text, out_wav, engine, voice=None, style=None):
    """Speak `text` into out_wav. `voice` overrides the engine's narrator voice; `style` is an
    acting note used by engines that accept one (OpenAI, Gemini)."""
    text = tts_clean(text)
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
        lang = "en-gb" if names[0].startswith("b") else "en-us"
        said, as_sounds = kokoro_text(k, text, lang)
        samples, sr = k.create(said, voice=style, speed=speed, lang=lang, is_phonemes=as_sounds)
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



# how a line is said, from its speech tag ("she whispered", "he snapped", "she said softly") or its punctuation
LINE_EMOTIONS = [
    ("whisper", r"whisper\w*|murmur\w*|breathed|under (?:her|his|their) breath|hush\w*"),
    ("shout", r"shout\w*|yell\w*|scream\w*|roar\w*|bellow\w*|hollered|called out|cried out"),
    ("angry", r"snap\w*|snarl\w*|growl\w*|hiss\w*|spat|barked|seethed|angrily|furious\w*"),
    ("sad", r"sob\w*|wept|weeping|choked|sniffl\w*|sadly|quietly, (?:her|his) voice break\w*|voice cracked"),
    ("laugh", r"laugh\w*|giggl\w*|chuckl\w*|grinn\w*|teas\w*"),
    ("fearful", r"stammer\w*|stutter\w*|trembl\w*|shaking|nervously|panick\w*"),
    ("tender", r"softly|gently|tender\w*|quietly|soothing\w*|reassur\w*"),
    ("seductive", r"purr\w*|moan\w*|husk\w*|sultr\w*|breathless\w*|breathily"),
    ("excited", r"exclaim\w*|excited\w*|gush\w*|squeal\w*|beam\w*"),
]
_LINE_EMO = [(e, re.compile(r"\b(?:" + p_ + r")\b", re.I)) for e, p_ in LINE_EMOTIONS]
# Kokoro: (speed, pitch, loudness change in dB, extra sound shaping)
EMOTION_KOKORO = {
    "whisper": (0.9, 0.98, -7, "highpass=f=200,treble=g=3"), "shout": (1.08, 1.04, 3, None),
    "angry": (1.07, 0.98, 2, None), "sad": (0.88, 0.97, -3, None), "laugh": (1.05, 1.03, 0, None),
    "fearful": (1.06, 1.03, -1, None), "tender": (0.92, 0.99, -3, None), "seductive": (0.88, 0.97, -4, None),
    "excited": (1.08, 1.04, 1, None),
}


def line_emotion(narration, spoken):
    """How a line of dialogue is said, from the words around it in the story."""
    t = (spoken or "").strip()
    i = (narration or "").find(t[:60]) if t else -1
    around = ""
    if i >= 0:
        after = narration[i + len(t):i + len(t) + 110]
        before = narration[max(0, i - 80):i]
        before = re.split(r"[”\"]|(?<=[.!?])\s", before)[-1]           # only a tag that leads into this line
        around = re.split(r"(?<=[.!?])\s|“", after, maxsplit=1)[0] + " " + before.rstrip("“ ")
    for emo, rx in _LINE_EMO:
        if rx.search(around):
            return emo
    if t.endswith("!") and (t.count("!") > 1 or re.search(r"\b[A-Z]{3,}\b", t)):
        return "shout"
    if t.endswith("!"):
        return "excited"
    return ""


def speak_line(cfg, text, wav, engine, vinfo, emotion=""):
    """Speak one line, falling back to the narrator voice and then to silence."""
    tries = [(vinfo["voice"], VOICE_STYLE.get(vinfo["type"])) if vinfo else (None, None), (None, None)]
    if engine == "kokoro" and vinfo and vinfo.get("voice") and "@" not in vinfo["voice"]:
        # acting for the free voices: the way the story says it's said (whispered, shouted, sobbed…), otherwise
        # excited lines a touch quicker and hesitant or trailing ones slower
        t = text.strip()
        pace = 1.07 if t.endswith("!") else 0.93 if t.endswith(("...", "…")) or t.count(",") >= 4 else \
            1.03 if t.endswith("?") else 1.0
        spec = vinfo["voice"]
        if emotion in EMOTION_KOKORO:
            sp_, pi_, _g, _f = EMOTION_KOKORO[emotion]
            pace = sp_
            m = re.search(r"\^([\d.]+)", spec)
            pitch = round(max(0.8, min(1.19, (float(m.group(1)) if m else 1.0) * pi_)), 3)
            spec = re.sub(r"\^[\d.]+", "", spec) + (f"^{pitch}" if abs(pitch - 1) > 0.005 else "")
        if pace != 1.0 or spec != vinfo["voice"]:
            tries.insert(0, (f"{spec}@{pace}" if pace != 1.0 else spec, VOICE_STYLE.get(vinfo["type"])))
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
    f = lambda v: -90.0 if not v or v[-1] in ("-inf", "nan", "N/A") else float(v[-1])
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

    def part(name, text, vinfo, emotion=""):
        who = (vinfo or {}).get("voice") or "narrator:" + str(cfg.get(DEFAULT_VOICE_KEY.get(engine, ""), ""))
        acted = emotion if engine == "kokoro" and vinfo and emotion in EMOTION_KOKORO else ""
        spec = f"{engine}|{who}|{cfg.get('KOKORO_SPEED')}|{text}" + (f"|{acted}" if acted else "")
        key = hashlib.sha1(spec.encode()).hexdigest()[:16]
        lv = parts_dir / f"{key}.flac"
        used.add(lv.name)
        if not valid_media(lv):
            raw, wav = job / f"v_{key}.wav", job / f"lv_{key}.wav"
            speak_line(cfg, text, raw, engine, vinfo, acted)
            level_match(raw, wav)
            shape = []
            if acted:                                   # a whisper is quieter, a shout louder, after levelling
                _s, _p, gain, extra = EMOTION_KOKORO[acted]
                shape = ([extra] if extra else []) + ([f"volume={gain}dB"] if gain else []) + \
                    (["alimiter=limit=0.89:level=false"] if gain > 0 else [])
            try:
                run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), *(["-af", ",".join(shape)] if shape else []),
                     "-c:a", "flac", str(lv)])
            except RuntimeError:
                lv.unlink(missing_ok=True)
            if not valid_media(lv):                    # nothing came out (a line of only "…", a voice hiccup)
                log(f"No sound came out for the line \"{text[:60]}\"; a short pause is used instead.")
                silence(lv, 0.3)
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
                emo = line_emotion(sc["narration"], str(ln["text"]).strip()) if who != "Narrator" else ""
                parts.append((part(f"{i:03d}_{k:03d}", str(ln["text"]).strip(), voices["voices"].get(who.lower()), emo),
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
- "ambience": a background sound ONLY when the text itself names a place with a sound you'd clearly notice,
  written as a short search phrase for a sound-effects library, for example: "heavy rain on window", "ocean
  waves on beach", "busy city street traffic", "crickets at night countryside", "crackling fireplace", "busy
  cafe chatter", "forest birds morning", "inside moving car". Most scenes should be "none": indoors, quiet
  rooms, bedrooms, offices, conversations and thoughts are "none". Never use room tone, hum, drone, white
  noise or "quiet" anything.
- "effects": up to {max_fx} short sound effects, ONLY for things that actually happen in the scene and make a
  sound (a door is knocked on, a glass breaks, thunder cracks, a phone rings). Each has "sound" (a short search
  phrase like "door knock", "glass shattering", "thunder clap", "phone ringing", "footsteps on wood") and "at"
  (5 to 10 words copied EXACTLY from the sentence where it happens). A word alone is not a reason: "she held
  her breath" is not breathing, "a knock-out dress" is not a knock. No effect is better than a wrong one.
{rules}{wishes}
Scenes:
{scenes}

Reply with ONLY a JSON object, no markdown fences:
{{"scenes": [{{"scene": 1, "ambience": "...", "effects": [{{"sound": "...", "at": "..."}}]}}]}}"""

SOUND_RULES = {
    "explicit": ("This is an explicit adult story. Intimate sounds (\"kissing\", \"heavy breathing\", \"female "
                 "moaning\", \"male moaning\", \"bed creaking\") belong ONLY at the moments the characters are "
                 "actually kissing or having sex; never when the text only mentions breath, a sigh or a bed. "
                 "Intimate scenes get their breathing and moaning added automatically, so leave those out and "
                 "use effects only for other things that happen."),
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
}

# Backgrounds that are only noise (they make every story hiss): never used.
NOISE_AMBIENCE = re.compile(r"\b(room ?tone|hum|humming|drone|white noise|pink noise|static|hiss|silence|"
                            r"quiet|ambient|ambience only|air conditioning|ac unit|fridge|refrigerator|"
                            r"electrical|fluorescent|ventilation|hvac)\b", re.I)
# Sounds of bodies: they need an intimate moment (or real exertion) in the text around them, not just the word.
BODY_SOUND = re.compile(r"\b(breath\w*|pant\w*|gasp\w*|moan\w*|groan\w*|sigh\w*|kiss\w*|bed creak\w*|"
                        r"bed squeak\w*|creaking bed|sex\w*|orgasm\w*|slap\w*|whimper\w*|lovemaking|sheets)\b", re.I)
INTIMATE = re.compile(GRAPHIC.pattern[:-3] + r"|kiss\w*|groan\w*|lips|tongue\w*|mouth on|her mouth|his mouth|moan\w*|"
                      r"gasp\w*|pant\w*|arch\w*|hips|thigh\w*|undress\w*|unbutton\w*|skin against|inside (?:her|him)|"
                      r"on top of|beneath (?:him|her)|climax\w*|shudder\w*|tremb\w*|bodies)\b", re.I)
EXERTION = re.compile(r"\b(ran|run|running|sprint\w*|chas\w*|climb\w*|out of breath|breathless|breathing hard|"
                      r"pant\w*|gasp\w*|exhaust\w*|fight\w*|struggl\w*|swim\w*)\b", re.I)
SEX_MOMENT = re.compile(r"\b(moan\w*|groan\w*|cried out|cry out|climax\w*|orgasm\w*|thrust\w*|"
                        r"gasp\w*|whimper\w*|shudder\w*|her name|his name)\b", re.I)


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
                               system=system, think="fix")
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
            fx, used = [], set()
            for f_ in item.get("effects") or []:
                if not isinstance(f_, dict) or not str(f_.get("sound") or "").strip():
                    continue
                snd_name = str(f_["sound"]).strip()[:60]
                if level == "explicit" and cfg.get("INTIMATE_SOUNDS", "on") == "on" and BODY_SOUND.search(snd_name) \
                        and not re.search(r"\bkiss", snd_name, re.I):
                    continue                                  # love scenes get their breathing and moans by themselves
                at = fx_anchor(sc["narration"], snd_name, str(f_.get("at", "")), level)
                if at is None or (snd_name.lower(), at) in used:          # nowhere it fits, or the same twice
                    log(f"Sound '{snd_name}' left out of scene {i + 1}: no matching moment in the text")
                    continue
                used.add((snd_name.lower(), at))
                fx.append({"sound": snd_name, "at": at})
            amb = str(item.get("ambience") or "none").strip()[:80]
            amb = "none" if NOISE_AMBIENCE.search(amb) else amb      # a hint: where it plays follows the text
            sc["sound"] = {"ambience": amb, "effects": fx[:max_fx]}


def ambience_fits(ambience, narration):
    """The background only when the scene's own words put us somewhere with that sound; "none" otherwise.
    Hums, room tone and "quiet" backgrounds are never used: they only add noise."""
    a = (ambience or "").strip()
    if not a or a.lower() == "none" or NOISE_AMBIENCE.search(a):
        return "none"
    text = (narration or "").lower()
    for name_, (pat_, query_) in AMBIENCE_FALLBACK.items():  # a known kind of place: the text must name it
        if re.search(r"\b(?:" + pat_ + r")\b", a.lower()):
            return a if re.search(r"\b(?:" + pat_ + r")\b", text) else "none"
    stems = {w[:5] for w in _words(a) if len(w) > 3}
    hits = {w[:5] for w in re.findall(r"[a-z]+", text) if len(w) > 3} & stems
    return a if hits else "none"


def _norm_words(s):
    return re.sub(r"[^a-z0-9' ]", " ", (s or "").lower().replace("’", "'")).split()


SOUND_HINTS = {   # words in the text that show where a sound belongs, for common effects
    "door": "door|doors|doorway|knock\\w*", "knock": "knock\\w*|door", "footsteps": "step\\w*|walk\\w*|footstep\\w*|stair\\w*",
    "phone": "phone|ring\\w*|buzz\\w*|vibrat\\w*|text\\w*|call\\w*", "glass": "glass\\w*|toast\\w*|wine|drink\\w*",
    "thunder": "thunder\\w*|storm\\w*|lightning", "car": "car|engine|drove|drive\\w*|tyres?|tires?",
    "kiss": "kiss\\w*", "breathing": "pant\\w*|gasp\\w*|out of breath|breathless", "moan": "moan\\w*|groan\\w*",
    "gunshot": "shot|shoot\\w*|gun\\w*|fired", "dog": "dog|bark\\w*", "laugh": "laugh\\w*|giggl\\w*",
}


def sound_fits(sentences, k, sound, level):
    """Does the sound make sense at sentence k? Body sounds need what they stand for: an intimate moment (kissing,
    sex) in or around the sentence, or real exertion for breathing; the word "breath" alone is not enough."""
    if not BODY_SOUND.search(sound or ""):
        return True
    near = " ".join(sentences[max(0, k - 1):k + 2])
    if re.search(r"\bkiss", sound, re.I):
        return bool(re.search(r"\bkiss\w*", near, re.I))
    if re.search(r"\b(breath|pant|gasp)", sound, re.I) and not re.search(r"\b(moan|sex|orgasm|bed)", sound, re.I):
        if EXERTION.search(near):
            return True
    if level != "explicit":
        return False
    return len(INTIMATE.findall(near)) >= 2


def _sentence_index(sentences, words):
    w = " " + " ".join(_norm_words(words)) + " "
    for k, s_ in enumerate(sentences):
        if w.strip() and w in " " + " ".join(_norm_words(s_)) + " ":
            return k
    return None


def fx_anchor(narration, sound, at, level="family"):
    """Where in the scene a sound effect belongs, as words copied from the text: the writer's own words when they're
    really in the scene and the sound makes sense there; otherwise the sentence that best matches the sound;
    None if it fits nowhere (then it's left out: no effect is better than a wrong one)."""
    found = _fx_anchor(narration, sound, at)
    sents = split_sentences(narration)
    if found is not None:
        k = _sentence_index(sents, found)
        if k is None or sound_fits(sents, k, sound, level):
            return found
    if BODY_SOUND.search(sound or ""):                     # the right moment for it, if there is one
        for k, s_ in enumerate(sents):
            if sound_fits(sents, k, sound, level) and (INTIMATE.search(s_) or EXERTION.search(s_)):
                return " ".join(_norm_words(s_)[:7])
        return None
    return found


def _fx_anchor(narration, sound, at):
    text_w = _norm_words(narration)
    joined = " " + " ".join(text_w) + " "
    aw = _norm_words(at)
    for n in (len(aw), 6, 4, 3):                          # the writer's words, exactly or a run of them
        if n < 3 or n > len(aw):
            continue
        for k in range(0, len(aw) - n + 1):
            probe = " ".join(aw[k:k + n])
            if " " + probe + " " in joined:
                return probe
    sents = split_sentences(narration)
    keys = set(_words(sound)) | set(w for w in aw if len(w) > 3 and w not in GENERIC_WORDS)
    hint = "|".join(v for k, v in SOUND_HINTS.items() if k in (sound or "").lower())
    best, best_score = None, 0
    for s_ in sents:
        sw = set(_norm_words(s_))
        score = len(sw & keys) * 2 + (2 * len(re.findall(r"\b(?:" + hint + r")\b", s_.lower())) if hint else 0)
        score += sum(1 for k in keys for w in sw if len(k) > 4 and w.startswith(k[:5]) and w != k)
        if score > best_score:
            best, best_score = s_, score
    if best and best_score >= 2:
        return " ".join(_norm_words(best)[:7])
    return None


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
            if score > best_score and valid_media(f):          # a broken or empty file is skipped
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
    dur = {"ambience": "duration:[25 TO 1200]", "music": "duration:[60 TO 900] tag:music"}.get(kind, "duration:[0.3 TO 12]")
    tries = []
    for t_ in (" ".join(words), " ".join(words[:2]), " ".join(words[-2:]), words[-1] if words else ""):
        if t_ and t_ not in tries:
            tries.append(t_)
    for attempt in tries:
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


def _anchor_info(seg, phrase):
    """When, in this scene, the words `phrase` are spoken, and when the line holding them ends:
    (time, line_end). Nothing found: early in the scene."""
    lines = seg["lines"]
    ph = _norm_words(phrase)
    if ph:
        for n_words in (min(6, len(ph)), 4, 3, 2):            # the longest run of the words that's in one line
            for k in range(0, max(1, len(ph) - n_words + 1)):
                probe = " " + " ".join(ph[k:k + n_words]) + " "
                for ln in lines:
                    text = " " + " ".join(_norm_words(ln["text"])) + " "
                    pos = text.find(probe)
                    if pos >= 0:
                        frac = pos / max(1, len(text))
                        return ln["start"] + frac * (ln["end"] - ln["start"]), ln["end"]
        best, best_n = None, 1                                  # most words in common
        for ln in lines:
            n_ = len(set(ph) & set(_norm_words(ln["text"])))
            if n_ > best_n:
                best, best_n = ln, n_
        if best:
            return best["start"] + 0.3, best["end"]
    if lines:
        return lines[0]["start"] + 0.8, lines[0]["end"]
    return seg["start"] + 0.8, seg["start"] + 3.0


def _anchor_time(seg, phrase):
    return _anchor_info(seg, phrase)[0]


def _every_time(seg, phrase):
    """Every moment in the scene where these words are spoken: [(time, line_end)] (for "every time" effects)."""
    ph = _norm_words(phrase)
    if not ph:
        return []
    probe = " " + " ".join(ph) + " "
    out = []
    for ln in seg["lines"]:
        text = " " + " ".join(_norm_words(ln["text"])) + " "
        pos = text.find(probe)
        while pos >= 0:
            out.append((ln["start"] + pos / max(1, len(text)) * (ln["end"] - ln["start"]), ln["end"]))
            pos = text.find(probe, pos + 1)
    return out


# ── background sound that follows the story: where the scene is, for as long as it stays there ──
AMBIENT_SETTINGS = [   # (name, kind, words in the narration that put us there, what to search for)
    ("storm", "weather", r"thunder\w*|lightning|thunderstorms?|storm|storms|stormy", "thunderstorm rain"),
    ("rain", "weather", r"rain|rains|raining|rained|rainy|drizzl\w*|downpour|raindrops?", "rain ambience"),
    ("wind", "weather", r"the wind|a wind|cold wind|wind (?:howled|blew|whipped|picked up|rose|moaned|tugged)|windy|"
                        r"gusts?|gusting|blizzard|snowstorm|gale", "strong wind ambience"),
    ("ocean", "place", r"sea|ocean|waves|beach|shore|shoreline|surf|tide|seagulls?|harbou?r|pier|docks?|coast",
     "ocean waves beach"),
    ("river", "place", r"river|stream|creek|brook|waterfall", "river stream water"),
    ("forest", "place", r"forest|woods|woodland|meadow|trail|clearing|grove", "forest birds ambience"),
    ("night", "place", r"crickets?|porch at night|campsite|under the stars", "night crickets ambience"),
    ("city", "place", r"city|street|streets|traffic|downtown|sidewalks?|avenue|crosswalk|taxi|cab|honk\w*",
     "city street traffic ambience"),
    ("park", "place", r"park|playground|garden|gardens", "park birds ambience"),
    ("market", "place", r"market|bazaar|stalls?|fairground|carnival", "outdoor market crowd"),
    ("farm", "place", r"farm|barn|pasture|cornfield|ranch", "farm countryside ambience"),
    ("cafe", "indoor", r"caf[eé]|coffee shop|diner|bakery", "cafe ambience chatter"),
    ("restaurant", "indoor", r"restaurant|bistro|dining room", "restaurant ambience"),
    ("bar", "indoor", r"bar|pub|tavern|saloon|nightclub", "bar pub crowd ambience"),
    ("party", "indoor", r"party|ballroom|banquet|gala", "party crowd chatter"),
    ("office", "indoor", r"office|cubicles?|newsroom", "office ambience"),
    ("hospital", "indoor", r"hospital|ward|clinic|nurses'? station|emergency room", "hospital ambience"),
    ("car", "indoor", r"drove|driving|steering wheel|dashboard|passenger seat|back ?seat|highway|freeway",
     "car interior driving"),
    ("train", "indoor", r"train|subway|carriage|railway|tram", "train interior ambience"),
    ("plane", "indoor", r"plane|airplane|aeroplane|flight|cockpit|airport", "airplane cabin ambience"),
    ("fire", "indoor", r"fireplace|hearth|campfire|bonfire|embers", "crackling fireplace"),
    ("kitchen", "indoor", r"stove|sizzl\w*|frying pan|simmer\w*|pots and pans", "kitchen cooking ambience"),
    ("crowd", "indoor", r"crowd|stadium|arena|audience|concert", "crowd ambience"),
    ("school", "indoor", r"classroom|school|cafeteria|lecture hall", "school hallway ambience"),
    ("church", "indoor", r"church|cathedral|chapel", "church interior ambience"),
]
# a word used as a picture of something else ("waves of relief", "a storm of questions", "a train of thought")
_SETTING_RE = {n: re.compile(r"\b(?:" + pat + r")\b(?!\s+(?:of|in her|in his)\b)", re.I)
               for n, _k, pat, _q in AMBIENT_SETTINGS}
_SETTING = {n: (k, q) for n, k, _p, q in AMBIENT_SETTINGS}
QUIET_PLACE = re.compile(r"\b(?:(?:went|walked|stepped|came|headed|got|ran|hurried|slipped|stumbled|moved)\s+"
                         r"(?:back\s+)?(?:inside|indoors|in(?:to)?\s+(?:the|her|his|their|my)\s+(?:house|home|"
                         r"apartment|flat|bedroom|room|cabin|building|hallway))|closed the door behind|back home|"
                         r"bedroom|living room|kitchen|bathroom|apartment|study)\b", re.I)
# a busy place described as quiet ("the street was quiet", "the empty bar") has no crowd or traffic to hear
QUIET_WORDS = re.compile(r"\b(quiet|silent|silence|still|empty|deserted|hushed|closed)\b", re.I)
BUSY = {"city", "crowd", "party", "market", "bar", "cafe", "restaurant", "school", "office", "hospital"}
WEATHER_END = re.compile(r"\b(?:(?:rain|storm|wind|thunder)\s+(?:had\s+)?(?:stopped|eased|passed|cleared|let up|"
                         r"died down|faded)|stopped raining|sun came out|clear skies?)\b", re.I)


def _setting_of(sentence):
    """(place, weather, gone indoors, weather over) for one sentence, from the narration only (not what's said)."""
    bare = re.sub(r"“[^”]*”|\"[^\"]*\"", " ", sentence)
    place = weather = None
    for name, kind, _p, _q in AMBIENT_SETTINGS:
        if _SETTING_RE[name].search(bare):
            if kind == "weather":
                weather = weather or name
            else:
                place = place or name
    if place in BUSY and QUIET_WORDS.search(bare):
        return None, weather, True, bool(WEATHER_END.search(bare))
    return place, weather, bool(QUIET_PLACE.search(bare)), bool(WEATHER_END.search(bare))


def ambience_plan(story, segs):
    """Where each scene is, sentence by sentence, as background spans: a background starts where the text puts us
    there and lasts as long as we stay (into the next scene too, without a break, when the story is still there),
    and stops when we go somewhere else. Rain and wind sit on top, softer once we're indoors.
    Returns {segment number: [{"name", "query", "k0", "k1", "cin", "cout", "gain"}]} (k = sentence numbers)."""
    plans, state, order = {}, {"place": None, "weather": None}, []
    for n, seg in enumerate(segs):
        if seg["kind"] != "scene":
            continue
        sc = story["scenes"][seg["index"]]
        snd = sc.get("sound") or {}
        sents = split_sentences(sc.get("narration", ""))
        amb = str(snd.get("ambience") or "").strip()
        if snd.get("chosen"):                                  # you chose it: the whole scene, or nothing
            spans = [] if amb.lower() in ("", "none", "auto") else \
                [{"name": "chosen:" + amb.lower(), "query": amb, "k0": 0, "k1": max(1, len(sents)), "cin": False,
                  "cout": False, "gain": 1.0}]
            if spans and order and plans[order[-1]] and plans[order[-1]][-1]["name"] == spans[0]["name"]:
                spans[0]["cin"] = True
            state = {"place": spans[0]["name"] if spans else None, "weather": None}
            plans[n] = spans
            order.append(n)
            continue
        info = [_setting_of(x) for x in sents]
        spans = []
        indoors = [False] * len(sents)
        for kind in ("place", "weather"):
            got = lambda i: i[0] or ("quiet" if i[2] else None) if kind == "place" else i[1]
            mentioned = {got(i) for i in info} - {None}
            first = next((got(i) for i in info if got(i)), None)
            cur = state[kind] if state[kind] in mentioned and (kind == "weather" or first == state[kind]) else None
            start, cin = 0, cur is not None
            for k, i in enumerate(info):
                if kind == "weather" and i[3] and cur:
                    spans.append({"name": cur, "k0": start, "k1": k, "cin": cin})
                    cur = None
                    continue
                new = got(i)
                if new and new != cur:
                    if cur:
                        spans.append({"name": cur, "k0": start, "k1": k, "cin": cin})
                    cur, start, cin = new, k, False
            if cur:
                spans.append({"name": cur, "k0": start, "k1": len(sents), "cin": cin})
            state[kind] = cur
            if kind == "place":
                for sp in spans:
                    if sp["name"] == "quiet" or _SETTING.get(sp["name"], ("",))[0] == "indoor":
                        for k in range(sp["k0"], sp["k1"]):
                            indoors[k] = True
        out = []
        for sp in spans:
            if sp["name"] == "quiet":
                continue                                        # indoors and quiet: no background
            kind, query = _SETTING[sp["name"]]
            hint = amb if amb and amb.lower() not in ("none", "auto") and (
                _SETTING_RE[sp["name"]].search(amb) or set(_words(amb)) & set(_words(query))) else ""
            sp.update(query=hint or query, cout=False)
            if kind == "weather":                               # rain heard from inside is softer
                inside = sum(indoors[sp["k0"]:sp["k1"]]) > (sp["k1"] - sp["k0"]) / 2
                sp["gain"] = 0.45 if inside else 1.0
            else:
                sp["gain"] = 1.0
            out.append(sp)
        plans[n] = out
        order.append(n)
    for a_, b_ in zip(order, order[1:]):                       # carries on into the next scene: no fade between
        for sp in plans[a_]:
            nxt = next((x for x in plans[b_] if x["name"] == sp["name"] and x["cin"]), None)
            if nxt and sp["k1"] >= len(split_sentences(story["scenes"][segs[a_]["index"]].get("narration", ""))):
                sp["cout"] = True
        for x in plans[b_]:
            prev = next((sp for sp in plans[a_] if sp["name"] == x["name"] and sp["cout"]), None)
            if x["cin"] and not prev:
                x["cin"] = False
            elif prev:
                x["query"] = prev["query"]                     # the same recording carries on
                if abs(prev["gain"] - x["gain"]) > 0.01:        # louder or softer now: a gentle cross-fade
                    x["cin"] = prev["cout"] = False
    return plans


CONTINUOUS_FX = re.compile(r"\b(ring\w*|engine|motor|siren|alarm|crowd|applause|clapping|footsteps|typing|rain|"
                           r"wind|music|humming|buzz\w*|traffic|crackl\w*|breath\w*|moan\w*|static|running water|"
                           r"shower|purr\w*|snor\w*|ticking|clock)\b", re.I)


def build_bed(cfg, story, timeline, job, level, music=None, with_sounds=True):
    """The background track for the whole story: each scene's ambience (looped, faded) plus its effects
    at the moments they belong, and optional music. Returns (bed_wav or None, credits)."""
    amb_gain = 10 ** ((-33 + 20 * math.log10(max(0.05, float(cfg["AMBIENCE_VOLUME"])))) / 20)
    credits, pieces, any_sound = [], [], False
    cache = {}

    def leveled(path, target):
        key = (str(path), target)
        if key not in cache:
            out = job / f"snd_{hashlib.sha1(str(key).encode()).hexdigest()[:10]}.wav"
            if not valid_media(out):
                try:
                    level_match(path, out, target_rms=target, ceiling=-3, stereo=True)
                except RuntimeError as e:
                    log(f"A sound couldn't be used ({str(e)[:80]}).")
            cache[key] = out if valid_media(out) else None
        return cache[key]

    segs = timeline["segments"]
    amb_plans = ambience_plan(story, segs) if with_sounds else {}
    amb_pos = {}                                   # where each looping background is, so it carries on seamlessly
    for n_, seg in enumerate(segs):
        dur = seg["end"] - seg["start"]
        out = job / f"bed_{n_:03d}.wav"
        if seg["kind"] != "scene":
            silence(out, dur, 2)
            pieces.append(out)
            continue
        sc = story["scenes"][seg["index"]]
        plan = dict(sc.get("sound") or {}) if with_sounds else {}
        if plan:                                   # checked again here, so older stories get it right too
            sents_ = split_sentences(sc.get("narration", ""))
            keep = []
            for fx in plan.get("effects") or []:
                k_ = _sentence_index(sents_, fx.get("at", ""))
                if k_ is None and BODY_SOUND.search(fx.get("sound", "")):
                    continue
                if k_ is None or sound_fits(sents_, k_, fx.get("sound", ""), level):
                    keep.append(fx)
            plan["effects"] = keep
        inputs, chains, labels = ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"], [], ["[0:a]"]
        k = 1
        sents_all = split_sentences(sc.get("narration", ""))

        def sentence_time(k_):
            if k_ <= 0:
                return seg["start"]
            if k_ >= len(sents_all):
                return seg["end"]
            return _anchor_time(seg, " ".join(_norm_words(sents_all[k_])[:6]))
        amb_names = []
        for sp in (amb_plans.get(n_) or []) if with_sounds else []:
            amb, credit = find_sound(cfg, sp["query"], "ambience", level)
            if not amb and sp["query"] != _SETTING.get(sp["name"], ("", sp["query"]))[1]:
                amb, credit = find_sound(cfg, _SETTING[sp["name"]][1], "ambience", level)   # a simpler search
            if not amb:
                continue
            a = max(0.0, sentence_time(sp["k0"]) - seg["start"] - (0 if sp["cin"] else 0.5))
            b = min(dur, sentence_time(sp["k1"]) - seg["start"] + (0 if sp["cout"] else 1.5))
            if b - a < 6 and not (sp["cin"] or sp["cout"]):
                continue                                         # too short to be worth a background
            src = leveled(amb, -30)
            if not src:
                continue
            credits.append(credit)
            amb_names.append(f"{sp['query']} {a:.0f}–{b:.0f}s")
            fi = 0.05 if sp["cin"] else min(2.0, (b - a) / 4)
            fo = 0.05 if sp["cout"] else min(2.5, (b - a) / 4)
            off = amb_pos.get(sp["name"], 0.0) % max(1.0, duration(src) - 1) if sp["cin"] else 0.0
            amb_pos[sp["name"]] = off + (b - a)
            inputs += ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-i", str(src)]
            chains.append(f"[{k}:a]atrim=0:{b - a:.3f},asetpts=N/SR/TB,"
                          f"volume={sp['gain'] * amb_gain / 10 ** (-30 / 20):.4f},"
                          f"afade=t=in:d={fi:.2f},afade=t=out:st={max(0, b - a - fo):.2f}:d={fo:.2f},"
                          f"adelay={int(a * 1000)}|{int(a * 1000)}[a{k}]")
            labels.append(f"[a{k}]")
            k += 1
        layers, auto_fx = [], []
        if with_sounds and level == "explicit" and cfg.get("INTIMATE_SOUNDS", "on") == "on":
            layers, auto_fx = intimate_sounds(seg, sc.get("narration", ""))
        for t0, t1, query in layers:               # soft breathing under the love scene, and only there
            f_, credit = find_sound(cfg, query, "effect", level)
            f_ = leveled(f_, -30) if f_ else None
            a, b = max(0.0, t0 - seg["start"]), min(dur, t1 - seg["start"])
            if not f_ or b - a < 4:
                continue
            credits.append(credit)
            fd = min(3.0, (b - a) / 4)
            inputs += ["-stream_loop", "-1", "-i", str(f_)]
            chains.append(f"[{k}:a]atrim=0:{b - a:.2f},asetpts=N/SR/TB,volume={0.45 * float(cfg['EFFECTS_VOLUME']):.3f},"
                          f"afade=t=in:d={fd:.2f},afade=t=out:st={b - a - fd:.2f}:d={fd:.2f},"
                          f"adelay={int(a * 1000)}|{int(a * 1000)}[a{k}]")
            labels.append(f"[a{k}]")
            k += 1
        planned = []
        for fx in plan.get("effects") or []:        # once, where it belongs, unless you asked for every time
            times = _every_time(seg, fx.get("at", "")) if fx.get("every") else []
            planned += [(t, dict(fx, _end=e)) for t, e in (times or [_anchor_info(seg, fx.get("at", ""))])]
        planned = [(t, fx) for t, fx in planned if not (BODY_SOUND.search(fx.get("sound", "")) and         # no doubles
                   not re.search(r"\bkiss", fx.get("sound", ""), re.I) and any(a <= t <= b for a, b, _ in layers))]
        timed = sorted(planned + auto_fx, key=lambda p_: p_[0])
        log(f"Sound, scene {seg['index'] + 1}: background {', '.join(amb_names) or 'none'}; "
            + (", ".join(f"{fx.get('sound')} at {t - seg['start']:.0f}s" for t, fx in timed) or "no effects")
            + "".join(f"; {q} {a - seg['start']:.0f}–{b - seg['start']:.0f}s" for a, b, q in layers))
        last_at = -99.0
        for at, fx in timed:
            if at is None:
                continue
            at = max(at, last_at + (0.8 if fx.get("every") else 1.5))     # effects never pile on top of each other
            if at > seg["end"] - 0.5:
                continue
            f_, credit = find_sound(cfg, fx.get("sound"), "effect", level)
            f_ = leveled(f_, -26) if f_ else None
            if not f_:
                continue
            last_at = at
            credits.append(credit)
            delay = max(0, int((at - seg["start"]) * 1000))
            body = BODY_SOUND.search(fx.get("sound", ""))
            # women's moaning recordings are breathier and softer than men's: they get more lift to sound as present
            fx_gain = float(cfg["EFFECTS_VOLUME"]) * ((1.15 if re.search(r"female|woman", fx.get("sound", ""), re.I)
                                                      else 0.7) if body else 1.0)
            # it belongs to its moment: a knock is a knock; a ringing phone lasts to the end of that line, no further
            line_end = fx.get("_end") or (at + 3)
            cap = 6.0 if CONTINUOUS_FX.search(fx.get("sound", "")) else 2.5
            room = max(1.0, min(cap, max(line_end + 0.4 - at, 1.0), seg["end"] - at))
            inputs += ["-i", str(f_)]
            chains.append(f"[{k}:a]atrim=0:{room:.2f},afade=t=out:st={max(0.0, room - 0.6):.2f}:d=0.6,"
                          f"volume={fx_gain:.3f},adelay={delay}|{delay}[a{k}]")
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
    music_src = leveled(music, -26 if cfg.get("MUSIC_MODE", "under") == "moments" else -32) if music else None
    if music and not music_src:
        log(f"The music track {music.name} couldn't be played, so this story has none.")
        music = None
    if music and cfg.get("MUSIC_MODE", "under") == "moments":
        mixed = job / "bed_music.wav"
        mix_music_moments(cfg, bed, music, timeline, mixed, music_src)
        bed = mixed
        credits.append(MUSIC_CREDITS.get(str(music), f"Music: {music.name}"))
    elif music:
        total = timeline["total"]
        mixed = job / "bed_music.wav"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(bed), "-stream_loop", "-1", "-i", str(music_src),
             "-filter_complex", f"[1:a]atrim=0:{total:.3f},asetpts=N/SR/TB,volume={float(cfg['MUSIC_VOLUME']) / 0.18:.3f},"
                                f"afade=t=in:d=3,afade=t=out:st={max(0, total - 4):.2f}:d=4[m];"
                                f"[0:a][m]amix=inputs=2:duration=first:normalize=0[o]",
             "-map", "[o]", "-ar", "44100", "-ac", "2", str(mixed)])
        bed = mixed
        credits.append(MUSIC_CREDITS.get(str(music), f"Music: {music.name}"))
    seen = []
    return bed, [c for c in credits if c and not (c in seen or seen.append(c))]


def intimate_sounds(seg, narration):
    """For an explicit story's love scenes: where they are (a run of at least three intimate sentences, not one
    kiss or a mention of breath), a soft breathing layer under each one, and moans at the moments the text
    describes (moaning, gasping, crying out, the climax), a woman's or a man's by who the sentence is about.
    Returns ([(start, end, sound)], [(time, effect)])."""
    sents = split_sentences(narration)
    hot = [k for k, s_ in enumerate(sents) if INTIMATE.search(s_)
           and len(INTIMATE.findall(" ".join(sents[max(0, k - 1):k + 2]))) >= 3]
    runs, cur = [], []
    for k in hot:
        if cur and k - cur[-1] > 2:
            runs.append(cur)
            cur = []
        cur.append(k)
    if cur:
        runs.append(cur)
    layers, fx = [], []
    at = lambda k: _anchor_time(seg, " ".join(_norm_words(sents[k])[:6]))
    for r in runs:
        if len(r) < 3:
            continue
        t0 = at(r[0])
        t1 = at(r[-1] + 1) if r[-1] + 1 < len(sents) else seg["end"]
        layers.append((t0, max(t1, at(r[-1]) + 4), "heavy breathing"))
        moments = [k for k in range(r[0], r[-1] + 1) if SEX_MOMENT.search(sents[k])] or [r[-1]]
        last = -99.0
        for k in moments[:6]:
            t = at(k)
            if t - last < 5:
                continue
            last = t
            m_ = re.search(r"\b(she|her|he|him|his)\b", sents[k].lower())     # who the sentence is about first
            who = "male moaning" if m_ and m_.group(1) in ("he", "him", "his") else "female moaning"
            fx.append((t, {"sound": who, "at": " ".join(_norm_words(sents[k])[:6])}))
    return layers, fx


MUSIC_MOODS = [   # words in the genre or level → words in music file names that fit
    (r"romanc|love|explicit|mature|sensual|steamy", ["romantic", "love", "sensual", "tender", "piano", "soft"]),
    (r"horror|thrill|suspense|dark|crime|noir|ghost", ["dark", "tense", "suspense", "eerie", "noir", "mystery"]),
    (r"myster|detective", ["mystery", "noir", "suspense", "jazz"]),
    (r"kid|child|young|bedtime|fairy|whims", ["playful", "whimsical", "happy", "lullaby", "gentle", "kids"]),
    (r"adventur|fantasy|epic|quest|action|hero", ["adventure", "epic", "heroic", "fantasy", "cinematic"]),
    (r"comed|funny|humou?r|silly", ["funny", "comedy", "happy", "playful", "light"]),
    (r"drama|sad|grief|loss|emotion", ["sad", "emotional", "piano", "tender", "melancholy"]),
    (r"sci|space|future|cyber", ["space", "electronic", "ambient", "synth"]),
    (r"heartwarm|cozy|cosy|family|friend", ["warm", "gentle", "acoustic", "happy", "hopeful"]),
]
NEUTRAL_MUSIC = ["calm", "gentle", "soft", "piano", "acoustic", "cinematic", "warm"]


def mix_music_moments(cfg, bed, music, timeline, out, track):
    """Music where an audiobook has it: under the title and into the first words, at each new chapter, and at
    the end. Not under the whole story. Each piece fades in and out, and dips under the voice."""
    total = timeline["total"]
    segs = timeline["segments"]
    wins = []
    title = next((s_ for s_ in segs if s_["kind"] == "title"), None)
    if title:
        wins.append((0.0, min(total, title["end"] + 6), 0.5, 5.0))      # (start, length, fade in, fade out)
    first_scene = next((s_ for s_ in segs if s_["kind"] == "scene"), None)
    for s_ in segs:
        if s_["kind"] == "scene" and s_.get("chapter_start") and s_ is not first_scene:
            wins.append((max(0.0, s_["start"] - 2.5), 9.0, 1.5, 5.0))
    if total > 30:
        wins.append((max(0.0, total - 15), 15.0, 4.0, 5.0))
    tlen = max(1.0, duration(track))
    inputs, chains, labels = ["-i", str(bed)], [], ["[0:a]"]
    vol = float(cfg["MUSIC_VOLUME"]) / 0.18
    for n, (start, length, fi, fo) in enumerate(wins):
        off = 0.0 if n == 0 else (n * 41.0) % max(1.0, tlen - length)    # a different part of the track each time
        inputs += ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-i", str(track)]
        d = int(start * 1000)
        chains.append(f"[{n + 1}:a]atrim=0:{length:.2f},asetpts=N/SR/TB,volume={vol:.3f},"
                      f"afade=t=in:d={fi:.2f},afade=t=out:st={max(0.0, length - fo):.2f}:d={fo:.2f},"
                      f"adelay={d}|{d}[m{n}]")
        labels.append(f"[m{n}]")
    graph = ";".join(chains) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:duration=first:normalize=0[o]"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]",
         "-ar", "44100", "-ac", "2", str(out)])


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
                 "[b][key]sidechaincompress=threshold=0.025:ratio=4:attack=25:release=500:knee=4[duck];"
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
    music = pick_music(cfg, genre or "", music_choice, seed, level)
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
        texts, spans = subtitle_lines(timeline, None, story.get("title", ""))
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
        if not valid_media(f_):                     # past the end of the audio: silence of the right length
            silence(f_, max(0.5, seg["end"] - seg["start"]), channels=2)
        out.append(f_)
    return out


def pick_music(cfg, genre, choice, seed, level=""):
    """choice: None (auto) | 'off' | part of a file name."""
    if cfg["MUSIC"] != "on" or (choice or "").lower() == "off":
        return None
    folder = Path(os.path.expanduser(cfg["MUSIC_DIR"]))
    tracks = sorted(p for p in folder.glob("**/*") if p.suffix.lower() in AUDIO_EXTS) if folder.exists() else []
    if not tracks:
        return free_music(cfg, genre, level, [], random.Random(seed))
    if choice:
        hits = [t for t in tracks if choice.lower() in t.stem.lower()]
        if hits:
            return hits[0]
        log(f"No music file matches '{choice}'; picking one instead.")
    rnd = random.Random(seed)
    groups = [ws for pat, ws in MUSIC_MOODS if re.search(pat, (level or "").lower())]          # the level's mood,
    groups += [ws for pat, ws in MUSIC_MOODS if re.search(pat, (genre or "").lower())]         # the genre's moods,
    groups.append([w for w in re.findall(r"[a-z]+", (genre or "").lower()) if len(w) > 3])     # its words,
    groups.append(NEUTRAL_MUSIC)                                                               # or something calm
    for want in groups:
        hits = [t for t in tracks if any(w in t.stem.lower() for w in want)]
        if hits:
            return rnd.choice(hits)
    return free_music(cfg, genre, level, tracks, rnd)


FREE_MUSIC = [   # the story's level or genre → music to look for on Freesound (free, with credits)
    (r"romanc|love|explicit|mature|sensual|steamy", "romantic piano music"),
    (r"horror|thrill|suspense|dark|crime|noir|ghost", "dark suspense music"),
    (r"myster|detective", "mystery music"),
    (r"kid|child|young|bedtime|fairy|whims", "playful whimsical music"),
    (r"adventur|fantasy|epic|quest|action|hero", "epic adventure music"),
    (r"comed|funny|humou?r|silly", "funny quirky music"),
    (r"drama|sad|grief|loss|emotion", "sad piano music"),
    (r"sci|space|future|cyber", "ambient space music"),
    (r"heartwarm|cozy|cosy|family|friend", "warm acoustic music"),
]
MUSIC_CREDITS = {}


def free_music(cfg, genre, level, tracks, rnd):
    """No track in your music folder fits: free music that matches the mood, from Freesound (downloaded once and
    kept). If that can't be had either, a track from your folder, so the story still has music."""
    for key in ((level or "").lower(), (genre or "").lower(), ""):
        query = next((q for pat, q in FREE_MUSIC if key and re.search(pat, key)), None if key else "calm piano music")
        if not query:
            continue
        f, credit = find_sound(cfg, query, "music", level)
        if f:
            MUSIC_CREDITS[str(f)] = f"Music: {credit}"
            log(f"Music: {query} from Freesound ({credit}).")
            return Path(f)
        break
    if tracks:
        log("No music in your music folder matches this story's mood, and none could be found online, so one of "
            "yours is used. Name tracks by mood (romantic, tense, playful, epic, sad, calm…) to have them matched.")
        return rnd.choice(tracks)
    log("No music: your music folder is empty and no free music could be found (needs FREESOUND_API_KEY).")
    return None



# ───────────────────────────── video assembly ─────────────────────────────

ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
AFMT = "aformat=sample_rates=44100:channel_layouts=stereo"


def still_segment(cfg, pic, cap, wav, seg, w, h, zoom_in=True, fps=30):
    """One part of the video from one or more pictures: a slow push in or out across the whole part (never a
    sudden stop), and a soft cross-fade from one picture to the next."""
    pics = [p_ for p_ in (pic if isinstance(pic, (list, tuple)) else [pic]) if p_]
    secs = max(0.5, duration(wav)) + 0.6
    n = len(pics)
    part = secs / n
    fade = min(1.0, part / 4) if n > 1 else 0
    inputs, chains = [], []
    for k, p_ in enumerate(pics):
        length = part + fade
        frames = max(2, int(length * fps))
        if cfg["MOTION"] == "on":
            zi = zoom_in if k % 2 == 0 else not zoom_in
            step = 0.12 / frames
            z = f"min(1+{step:.6f}*on,1.12)" if zi else f"max(1.12-{step:.6f}*on,1)"
            chains.append(f"[{k}]scale={w * 2}:{h * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                          f":d={frames}:s={w}x{h}:fps={fps},setsar=1,format=yuv420p[p{k}]")
            inputs += ["-i", str(p_)]
        else:
            chains.append(f"[{k}]scale={w}:{h},fps={fps},setsar=1,format=yuv420p[p{k}]")
            inputs += ["-loop", "1", "-t", f"{length:.2f}", "-i", str(p_)]
    cur = "[p0]"
    for k in range(1, n):
        chains.append(f"{cur}[p{k}]xfade=transition=fade:duration={fade:.2f}:offset={k * part:.2f}[x{k}]")
        cur = f"[x{k}]"
    chains.append(f"{cur}[{n}]overlay=0:0,format=yuv420p[v];[{n + 1}]apad,{AFMT}[a]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-i", str(cap), "-i", str(wav),
         "-filter_complex", ";".join(chains),
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


def big_moments(story, n):
    """The scenes most worth animating: the fights, chases and shouts, weighted towards the climax."""
    scenes = story["scenes"]
    if n <= 0 or not scenes:
        return []
    last = max(1, len(scenes) - 1)
    def score(i):
        sc = scenes[i]
        text = " ".join([str(sc.get("narration", ""))] + [str(l.get("text", "")) for l in sc.get("lines") or []
                                                          if isinstance(l, dict)])
        return (10 if sc.get("big_moment") else 0) + 2 * len(CONFLICT.findall(text)) + min(4, text.count("!")) \
            + 3 * (i / last) + (1 if i == len(scenes) - 1 else 0)
    ranked = sorted(range(len(scenes)), key=score, reverse=True)
    picked = []
    for i in ranked:                                   # spread them out: not two in a row if it can be helped
        if len(picked) >= n:
            break
        if all(abs(i - j) > 1 for j in picked) or len(ranked) <= 2 * n:
            picked.append(i)
    for i in ranked:
        if len(picked) >= n:
            break
        if i not in picked:
            picked.append(i)
    return sorted(picked)


def moment_clips(cfg, story, job, pics, portrait, ref, reuse_only=False):
    """BIG_MOMENTS: a few scenes become short animated clips (Veo, online and paid), starting from their own
    picture. The rest of the story stays as pictures. Explicit stories never leave this computer, so not those."""
    try:
        want = max(0, min(5, int(cfg.get("BIG_MOMENTS") or 0)))
    except ValueError:
        want = 0
    if reuse_only:                             # remaking: the moments this story was made with
        want = int(story.get("big_moments") or 0)
    elif want:
        story["big_moments"] = want
    if not want or story.get("level") == "explicit":
        return {}
    picked = big_moments(story, want)
    out = {}
    for n_, i in enumerate(picked, 1):
        f = job / f"moment_{i + 1:02d}.mp4"
        if valid_media(f):
            out[i] = f
            continue
        if reuse_only or not cfg.get("GEMINI_API_KEY"):
            continue
        tmp = job / f"moment_{i + 1:02d}.part.mp4"
        report("video", 0.78, f"Animating big moment {n_} of {len(picked)} (a few minutes each)", image=pics[i])
        try:
            prompt = shot_prompt(story, story["scenes"][i], "video") + \
                " Dynamic, cinematic motion: this is the most dramatic moment of the scene."
            veo_clip(cfg, prompt, portrait, tmp, ref, first_frame=pics[i])
            if not valid_media(tmp):
                raise RuntimeError("the clip isn't a playable video")
            tmp.replace(f)
            out[i] = f
            log(f"Big moment {n_}/{len(picked)} animated (scene {i + 1}).")
        except Exception as e:
            tmp.unlink(missing_ok=True)
            log(f"Couldn't animate scene {i + 1} ({str(e)[:200]}); it stays as pictures.")
    return out


def moment_segment(cfg, clip, pics, cap, wav, seg, w, h, work, zoom_in=True):
    """A scene that opens with its animated clip, then carries on with its pictures for the rest of the narration."""
    clip_len, voice_len = duration(clip), duration(wav)
    if voice_len <= clip_len + 1.5:
        return clip_segment(cfg, clip, cap, wav, seg, w, h)
    stem = seg.with_suffix("")
    a_wav, b_wav = Path(f"{stem}_a.wav"), Path(f"{stem}_b.wav")
    a_mp4, b_mp4 = Path(f"{stem}_a.mp4"), Path(f"{stem}_b.mp4")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-t", f"{clip_len:.3f}", str(a_wav)])
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{clip_len:.3f}", "-i", str(wav), str(b_wav)])
    vid = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1[bg];"
           f"[bg][1]overlay=0:0,format=yuv420p[v]")
    vol = float(cfg["CLIP_SOUND_VOLUME"])
    aud = (f";[0:a]{AFMT},volume={vol},apad[ca];[2:a]{AFMT},apad[na];[ca][na]amix=inputs=2:duration=longest:normalize=0[a]"
           if has_audio(clip) and vol > 0 else f";[2:a]{AFMT},apad[a]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-i", str(cap), "-i", str(a_wav),
         "-filter_complex", vid + aud, "-map", "[v]", "-map", "[a]", "-t", f"{clip_len:.3f}", *ENC, str(a_mp4)])
    still_segment(cfg, pics, cap, b_wav, b_mp4, w, h, zoom_in=zoom_in)
    lst = Path(f"{stem}_list.txt")
    lst.write_text(f"file '{a_mp4}'\nfile '{b_mp4}'\n")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(seg)])


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

    extras = {}            # long scenes get more than one picture, when pictures are free (drawn on this Mac)
    if pick_image_provider(cfg) == "local" and cfg.get("MORE_PICTURES", "on") == "on":
        every = max(15, int(cfg.get("SECONDS_PER_PICTURE", "40")))
        todo = [(i, min(4, int((duration(wavs[i + 1]) + 0.6) // every) + 1)) for i, c in enumerate(clips) if c is None]
        todo = [(i, n) for i, n in todo if n > 1]
        total, done = sum(n - 1 for _, n in todo), 0
        for i, n in todo:
            for k, sentence in enumerate(scene_moments(story, i, n), start=1):
                if not sentence:
                    continue
                p_ = extra_picture(cfg, story, i, k, sentence, w, h, portrait, job)
                done += 1
                report("video", 0.80, f"Drawing more pictures for long scenes: {done} of {total}", image=p_)
                if p_:
                    extras.setdefault(i, []).append(p_)

    for i, c in enumerate(clips):            # extra pictures kept from before (remaking the video for new audio)
        if c is None and i not in extras:
            found = sorted(job.glob(f"pic_{i + 1:02d}_[0-9].png"))
            if found:
                extras[i] = found

    moments = {}
    if all(c is None for c in clips):          # a picture story: animate its biggest moments, if asked for
        moments = moment_clips(cfg, story, job, pics, portrait, ref, reuse_only=vprovider == "existing")

    log("Building the video…")
    segs = []
    cap = work / "cap_00.png"
    caption_overlay(story["title"], w, h, cap)
    segs.append(work / "seg_00.mp4")
    still_segment(cfg, title_pic, cap, wavs[0], segs[-1], w, h)
    for i, sc in enumerate(scenes):
        cap = work / f"cap_{i + 1:02d}.png"
        caption_overlay("", w, h, cap)       # pictures stay clean: words come as timed subtitles, line by line
        segs.append(work / f"seg_{i + 1:02d}.mp4")
        if clips[i]:
            clip_segment(cfg, clips[i], cap, wavs[i + 1], segs[-1], w, h)
        elif moments.get(i):
            moment_segment(cfg, moments[i], [pics[i]] + extras.get(i, []), cap, wavs[i + 1], segs[-1], w, h, work,
                           zoom_in=i % 2 == 1)
        else:
            still_segment(cfg, [pics[i]] + extras.get(i, []), cap, wavs[i + 1], segs[-1], w, h, zoom_in=i % 2 == 1)
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
        if vprovider == "slideshow" and cfg.get("GEMINI_API_KEY"):
            try:
                bm = max(0, min(5, int(cfg.get("BIG_MOMENTS") or 0), scenes))
            except ValueError:
                bm = 0
            if bm:
                items.append((f"{bm} animated big moment{'s' * (bm != 1)}", bm * 8 * f("PRICE_VEO_PER_SEC")))
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
    """Stories being made. JOBS_DIR puts them on a fast local disk when the library is on a NAS."""
    d = Path(os.path.expanduser(cfg.get("JOBS_DIR") or "")) if cfg.get("JOBS_DIR") else \
        Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / ".jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class JobStopped(BaseException):
    """You pressed Stop. A BaseException, so no "carry on after a problem" handler swallows it."""


_WORKER_PID = None                     # set in the background copy that does the work; the page's Stop uses it


def _stop_requested(signum, frame):
    raise JobStopped()


def write_status(cfg, job_id, **data):
    p = jobs_dir(cfg) / f"{job_id}.status.json"
    if _WORKER_PID and "pid" not in data:
        data["pid"] = _WORKER_PID
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


def subtitle_lines(timeline, seg_lens=None, title=""):
    """Subtitles line by line, each shown while it's spoken. seg_lens: the video's own segment lengths (it adds a
    short breath after each part), so the times follow the video; without them, the audio's clock."""
    texts, spans, at = [], [], 0.0
    for n_, seg in enumerate(timeline["segments"]):
        for ln in seg.get("lines") or []:
            text = str(ln.get("text") or "").strip() or (title if seg["kind"] == "title" else "")
            if not text:
                continue
            start = (at + ln["start"] - seg["start"]) if seg_lens else ln["start"]
            texts.append(text)
            spans.append((start, max(0.4, ln["end"] - ln["start"])))
        if seg_lens and n_ < len(seg_lens):
            at += seg_lens[n_]
    return texts, spans


def burn_subtitles(cfg, video, srt):
    """CAPTIONS=on: the subtitles burned into the picture, two short lines at most, always inside the frame."""
    if cfg.get("CAPTIONS", "off") != "on" or not Path(srt).exists():
        return
    tmp = Path(video).with_name("burned.mp4")
    style = "FontName=DejaVu Sans,FontSize=16,Outline=2,Shadow=1,MarginV=40,MarginL=60,MarginR=60,WrapStyle=0"
    path = str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"subtitles='{path}':force_style='{style}'",
             "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(tmp)])
        tmp.replace(video)
    except RuntimeError as e:
        log(f"Couldn't burn in the subtitles ({str(e)[:120]}); they're still in the subtitle file.")
        tmp.unlink(missing_ok=True)


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
        out.append({"name": s_["name"], "slug": f.parent.name, "episodes": len(s_["episodes"]), "level": s_.get("level", ""),
                    "last": s_["episodes"][-1]["title"] if s_["episodes"] else ""})
    return out


def save_episode(cfg, series, story, result, voices, out_dir, level, genre, new_name=None,
                 job_id="", complete=True, recap=None):
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
                               "job_id": job_id, "complete": complete,
                               **({"recap": recap["recap"]} if recap and recap.get("recap") else {})})
    if recap:                                  # what's true now, and what's still open, for the next episode
        if recap.get("state"):
            series["state"] = recap["state"]
        series["threads"] = recap.get("threads") or series.get("threads") or []
    if not series.get("bible", {}).get("characters"):     # a bible to start from: the characters as written
        series.setdefault("bible", {})["characters"] = bible_from_story(story)
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


# ───────────────────────────── exports: EPUB and a print-ready book ─────────────────────────────

def _fresh(out, *sources):
    """True if `out` exists and is newer than every source (so it doesn't need making again)."""
    return out.exists() and all(not s_.exists() or s_.stat().st_mtime <= out.stat().st_mtime for s_ in sources)


def _epub_image(src, dst, width=1400):
    im = Image.open(src).convert("RGB")
    if im.width > width:
        im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
    im.save(dst, "JPEG", quality=82)


def export_epub(cfg, run_id):
    """story.epub: for Apple Books, Kindle (send-to-Kindle takes EPUB) and any e-reader, with the pictures.
    A choose-your-own-adventure keeps working: each choice is a link to where it leads."""
    import html as H
    import uuid
    import zipfile
    folder = story_folder(cfg, run_id)
    out = folder / "story.epub"
    story_f, adv_f = folder / "story.json", folder / "adventure.json"
    if _fresh(out, story_f, adv_f):
        return {"ok": True, "epub": str(out)}
    story = json.loads(story_f.read_text())
    result = json.loads((folder / "result.json").read_text())
    adv = json.loads(adv_f.read_text()) if adv_f.exists() else None
    title = story.get("title") or result.get("title") or "Story"
    e = lambda t: H.escape(str(t), quote=True)
    uid = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'storymaker:' + run_id)}"
    files, manifest, spine, nav = {}, [], [], []

    def page(name, heading, body):
        files[f"OEBPS/{name}"] = (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                                  f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">'
                                  f'<head><meta charset="utf-8"/><title>{e(heading)}</title>'
                                  f'<link rel="stylesheet" href="style.css"/></head><body>{body}</body></html>')
        manifest.append(f'<item id="{name.split(".")[0]}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{name.split(".")[0]}"/>')
        nav.append((name, heading))

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        cover_src = next((p_ for p_ in (folder / "cover.jpg", folder / "thumb.jpg", folder / "scene_01.png") if p_.exists()), None)
        cover_html = ""
        if cover_src:
            _epub_image(cover_src, td / "cover.jpg", 1600)
            files["OEBPS/images/cover.jpg"] = (td / "cover.jpg").read_bytes()
            manifest.append('<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>')
            cover_html = '<figure class="cover"><img src="images/cover.jpg" alt=""/></figure>'
        sub = f"{result['series']}, episode {result['episode']}" if result.get("series") and result.get("episode", 1) > 1 else ""
        page("title.xhtml", title, f'{cover_html}<h1 class="title">{e(title)}</h1>'
             + (f'<p class="sub">{e(sub)}</p>' if sub else "")
             + (f'<p class="logline">{e(story.get("logline"))}</p>' if story.get("logline") else ""))
        if adv:
            order = list(adv["passages"])
            num = {k: i + 1 for i, k in enumerate(order)}
            body = ['<h2>How to read this book</h2><p>At the end of each part you choose what happens. Tap your choice to '
                    'go there. There are several endings: go back and try another path.</p>']
            for k in order:
                p_ = adv["passages"][k]
                body.append(f'<section id="p{num[k]}" class="passage"><h3>{num[k]}</h3>'
                            + "".join(f"<p>{e(x)}</p>" for x in paragraphs(p_["text"])))
                if p_.get("choices"):
                    body.append('<ul class="choices">' + "".join(
                        f'<li><a href="#p{num[c["to"]]}">{e(c["label"])}</a></li>' for c in p_["choices"]) + "</ul>")
                else:
                    body.append(f'<p class="end">The End <span>({e(p_.get("ending", "the end"))})</span></p>'
                                f'<p class="again"><a href="#p1">Start again</a></p>')
                body.append("</section>")
            page("story.xhtml", title, "".join(body))
        else:
            groups, cur = [], None                 # one file per chapter (or one for a short story)
            for i, sc in enumerate(story.get("scenes") or []):
                if cur is None or sc.get("chapter_title"):
                    cur = {"title": f"Chapter {sc['chapter']}: {sc['chapter_title']}" if sc.get("chapter_title") else title,
                           "scenes": []}
                    groups.append(cur)
                cur["scenes"].append((i, sc))
            for g_n, g in enumerate(groups, 1):
                body = [f"<h2>{e(g['title'])}</h2>"] if len(groups) > 1 else []
                for i, sc in g["scenes"]:
                    img = folder / f"scene_{i + 1:02d}.png"
                    if img.exists():
                        _epub_image(img, td / f"scene_{i + 1:02d}.jpg")
                        files[f"OEBPS/images/scene_{i + 1:02d}.jpg"] = (td / f"scene_{i + 1:02d}.jpg").read_bytes()
                        manifest.append(f'<item id="img{i + 1}" href="images/scene_{i + 1:02d}.jpg" media-type="image/jpeg"/>')
                        body.append(f'<figure><img src="images/scene_{i + 1:02d}.jpg" alt=""/></figure>')
                    body += [f"<p>{e(x)}</p>" for x in paragraphs(sc.get("narration", ""))]
                if g_n == len(groups):
                    body.append('<p class="end">The End</p>')
                page(f"part{g_n:02d}.xhtml", g["title"], "".join(body))
    files["OEBPS/style.css"] = ("body{font-family:Georgia,serif;line-height:1.55;margin:0 4%}h1.title{text-align:center;"
                                "margin-top:1.2em}p{margin:0 0 .9em;text-indent:0}.sub,.logline{text-align:center;"
                                "font-style:italic}figure{margin:1.2em 0;text-align:center}figure img{max-width:100%}"
                                ".cover img{max-height:70vh}.end{text-align:center;font-style:italic;margin-top:2em}"
                                ".choices{list-style:none;padding:0}.choices li{margin:.6em 0}.choices a{font-weight:bold}"
                                ".passage{margin-bottom:2.5em}.passage h3{text-align:center}")
    manifest.append('<item id="css" href="style.css" media-type="text/css"/>')
    nav_items = "".join(f'<li><a href="{n}">{e(t)}</a></li>' for n, t in nav)
    files["OEBPS/nav.xhtml"] = (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
                                f'xmlns:epub="http://www.idpf.org/2007/ops" lang="en"><head><meta charset="utf-8"/><title>Contents</title></head>'
                                f'<body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{nav_items}</ol></nav></body></html>')
    manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    modified = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files["OEBPS/content.opf"] = (f'<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" '
                                  f'version="3.0" unique-identifier="bookid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                                  f'<dc:identifier id="bookid">{uid}</dc:identifier><dc:title>{e(title)}</dc:title>'
                                  f'<dc:language>en</dc:language><dc:creator>StoryMaker</dc:creator>'
                                  + (f'<dc:description>{e(story.get("logline"))}</dc:description>' if story.get("logline") else "")
                                  + f'<meta property="dcterms:modified">{modified}</meta></metadata>'
                                  f'<manifest>{"".join(manifest)}</manifest><spine>{"".join(spine)}</spine></package>')
    files["META-INF/container.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                                       'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                                       '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                                       '</rootfiles></container>')
    tmp = out.with_suffix(".epub.part")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(out)
    return {"ok": True, "epub": str(out)}


def export_print(cfg, run_id):
    """story-print.pdf: a picture book ready for a printer (Lulu, Amazon KDP, a local print shop): 8.5 × 8.5 inch
    pages with 1/8 inch bleed at 300 dpi; each picture fills a left-hand page and its words face it. Plus
    story-print-cover.pdf, the front cover on its own, which print services ask for separately."""
    folder = story_folder(cfg, run_id)
    out, cover_out = folder / "story-print.pdf", folder / "story-print-cover.pdf"
    story_f = folder / "story.json"
    story = json.loads(story_f.read_text())
    scenes = story.get("scenes") or []
    imgs = [folder / f"scene_{i + 1:02d}.png" for i in range(len(scenes))]
    if not any(p_.exists() for p_ in imgs):
        return {"ok": False, "error": "This story has no pictures yet. Make it as a video or with a picture book, or "
                                      "use Remake the audio with new pictures, then try again."}
    if _fresh(out, story_f, *[p_ for p_ in imgs if p_.exists()]) and cover_out.exists():
        return {"ok": True, "pdf": str(out), "cover": str(cover_out)}
    dpi, trim, bleed = 300, 8.5, 0.125
    S = int((trim + 2 * bleed) * dpi)               # 2625 px square, bleed included
    safe = int((bleed + 0.5) * dpi)                 # words stay half an inch inside the trim
    paper, ink, faint = (253, 250, 244), (35, 30, 26), (140, 128, 116)
    pages = []

    def blank():
        return Image.new("RGB", (S, S), paper)

    cover_src = next((p_ for p_ in (folder / "cover.jpg", *imgs) if p_.exists()), None)
    cover = blank()
    if cover_src:
        cover.paste(fit_cover(Image.open(cover_src).convert("RGB"), S, S), (0, 0))
    band = Image.new("RGBA", (S, int(S * 0.30)), (20, 18, 30, 170))
    cover.paste(band, (0, S - band.height), band)
    text_block(ImageDraw.Draw(cover), story.get("title", ""), (safe, S - band.height + 60, S - safe, S - safe), 150,
               color=(250, 246, 238), min_size=70)
    cover.save(cover_out, "PDF", resolution=dpi)

    title = blank()                                  # page 1 (a right-hand page): the title
    d = ImageDraw.Draw(title)
    text_block(d, story.get("title", ""), (safe, int(S * 0.30), S - safe, int(S * 0.55)), 140, min_size=70)
    if story.get("logline"):
        text_block(d, story["logline"], (safe + 150, int(S * 0.58), S - safe - 150, int(S * 0.75)), 64, italic=True,
                   color=faint)
    pages.append(title)                              # page 1 is on the right, so every picture lands on a left page
    for i, (sc, img) in enumerate(zip(scenes, imgs)):
        pic = blank()
        if img.exists():
            pic.paste(fit_cover(Image.open(img).convert("RGB"), S, S), (0, 0))     # full bleed
        words = blank()
        dd = ImageDraw.Draw(words)
        text_block(dd, sc.get("narration", ""), (safe + 60, safe + 40, S - safe - 60, S - safe - 120), 66,
                   color=ink, min_size=40)
        n_ = str(len(pages) + 2)
        f_ = serif_font(40)
        dd.text(((S - dd.textlength(n_, font=f_)) / 2, S - safe - 20), n_, font=f_, fill=faint)
        pages += [pic, words]
    end = blank()
    text_block(ImageDraw.Draw(end), "The End", (safe, 0, S - safe, S), 160, italic=True)
    pages.append(end)
    while len(pages) % 4:                            # printers bind in groups of four pages
        pages.append(blank())
    tmp = out.with_suffix(".pdf.part")
    pages[0].save(tmp, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
    tmp.replace(out)
    return {"ok": True, "pdf": str(out), "cover": str(cover_out), "pages": len(pages)}


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


def clean_sound_plan(plan, old=None):
    """A scene's sounds as you set them. Background: blank = automatic (it follows where the scene is), "none" =
    silence, anything else = that sound for the whole scene. Effects play once at their words, or at every time
    those words are read if "every" is ticked."""
    fx = [{"sound": str(f_.get("sound", "")).strip()[:60], "at": str(f_.get("at", "")).strip()[:120],
           **({"every": True} if f_.get("every") else {})}
          for f_ in (plan.get("effects") or []) if isinstance(f_, dict) and str(f_.get("sound", "")).strip()][:8]
    amb = str(plan.get("ambience") or "").strip()[:80]
    if not amb or amb.lower() == "auto":                     # automatic: keep the designer's idea as a hint
        hint = (old or {}).get("ambience") if not (old or {}).get("chosen") else "auto"
        return {"ambience": hint or "auto", "effects": fx}
    return {"ambience": amb, "chosen": True, "effects": fx}


def set_sounds(cfg, run_id, payload):
    """payload: {"wishes": "rain, thunder", "scenes": {"0": {"ambience": "...", "effects": [{"sound", "at"}]}}}"""
    folder, story, result = load_story(cfg, run_id)
    if isinstance(payload.get("wishes"), str):
        story["sound_wishes"] = payload["wishes"].strip()[:300]
    for k, plan in (payload.get("scenes") or {}).items():
        i = int(k)
        if not (0 <= i < len(story["scenes"])) or not isinstance(plan, dict):
            continue
        story["scenes"][i]["sound"] = clean_sound_plan(plan, story["scenes"][i].get("sound"))
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
Stay on topic: the same things happen, in the same order, to the same people, with the same outcome and
details, consistent with the rest of the story (unless the instructions say otherwise). {fresh} {pov}
Aim for about {words} words unless the instructions ask for a different length; keep it moving, with no padding,
no repeated words or ideas, and every sentence a complete, clear thought. {extra}Write it as finished story
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
    rules += WRITING_CRAFT
    adult = level == "explicit"
    extra = ("If the scene involves sex, write it out fully and explicitly, moment by moment, in graphic detail. "
             if adult else "")
    steps = len(idxs) + (2 if then_remaster else 0)
    pov = (story.get("prefs") or {}).get("pov") or story.get("pov") or detect_pov([x["narration"] for x in story["scenes"]])
    names = [c.get("name") for c in story.get("cast") or [] if isinstance(c, dict)]
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
        old_sh, old_names = shingles(sc["narration"]), {n for n in names if n and n in sc["narration"]}
        note, new = "", ""
        for attempt in range(3):                  # on topic, but in new words: checked, and tried again if not
            wcfg = dict(cfg, OLLAMA_TEMPERATURE="0.7") if attempt else cfg
            new = clean_prose(ask_writer(wcfg, REWRITE_PROMPT.format(
                title=story["title"], genre=story.get("genre", ""), audience=audience, rules=rules,
                characters=story.get("characters", ""), context=context, text=sc["narration"],
                instructions=(instructions.strip() or "Write it again, better: the same events in fresh, vivid, "
                              "natural prose with natural dialogue.") + (" " + note if note else ""),
                words=words, extra=extra, pov=pov_rule(pov),
                fresh="But write it fresh, in your own new sentences: don't copy or lightly reword the old ones.",
                keywords=keywords_block(story.get("keywords"), " ".join(
                    s_["narration"] for j_, s_ in enumerate(story["scenes"]) if j_ != i))),
                as_json=False, system=EXPLICIT_SYSTEM if adult else None))
            if len(new.split()) < 40:
                note = "Write the whole scene."
                continue
            copied = len(shingles(new) & old_sh) / max(1, len(shingles(new)))
            lost = old_names and len({n for n in old_names if n in new}) < max(1, len(old_names) // 2)
            if copied > 0.45:
                note = "The last try copied too much of the old wording: say it all in new sentences."
            elif lost:
                note = f"The last try drifted off topic: keep {', '.join(sorted(old_names))} and the same events."
            elif scene_problems(new, [], pov):
                note = scene_problems(new, [], pov)
            else:
                break
            log(f"Rewrite of scene {i + 1} tried again: {note}")
        if len(new.split()) < 40:
            raise ValueError(f"The writer sent back almost nothing for scene {i + 1}. Try again.")
        new = proofread(cfg, new, EXPLICIT_SYSTEM if adult else None)
        new = fix_sentences(cfg, new, EXPLICIT_SYSTEM if adult else None, names, pov=pov)
        if adult:
            new = adult_guard(cfg, new, EXPLICIT_SYSTEM)
        sc["narration"] = new
        save_story_text(folder, story, result, [i])     # saved after every scene, so nothing is lost
    log(f"Rewrote {len(idxs)} scene(s).")
    if then_remaster and (result.get("audio_file") or result.get("video_file")):
        return remaster_story(cfg, run_id)
    return {"ok": True, "rewritten": len(idxs), "audio_outdated": bool(result.get("audio_outdated"))}


def check_writing(cfg, run_id):
    """The sentence checker over a finished story, by rule only (instant, no AI): how many problems, where, and a few
    examples."""
    folder, story, result = load_story(cfg, run_id)
    names = [c.get("name") for c in story.get("cast") or [] if isinstance(c, dict)]
    pov = (story.get("prefs") or {}).get("pov") or story.get("pov") or detect_pov(
        [x.get("narration", "") for x in story.get("scenes") or []])
    total, scenes_with, examples = 0, 0, []
    for i, sc in enumerate(story.get("scenes") or []):
        text = sc.get("narration", "")
        found = sentence_issues(tidy_sentences(text), names, pov)
        tidy = tidy_sentences(text) != text
        n = len(found) + (1 if tidy else 0)
        total += n
        scenes_with += 1 if n else 0
        for sent, prob in found[:2]:
            if len(examples) < 6:
                examples.append({"scene": i + 1, "sentence": sent[:220], "problem": prob})
    return {"run_id": run_id, "title": story.get("title", ""), "level": result.get("level") or story.get("level"),
            "scenes": len(story.get("scenes") or []), "problems": total, "scenes_with": scenes_with,
            "examples": examples, "has_audio": bool(result.get("audio_file") or result.get("video_file"))}


def cleanup_story(cfg, run_id, then_remaster=True, which=(1, 1)):
    """Run a finished story through the proofreader and the sentence checker, scene by scene, and save it. Nothing
    else changes: the events, names, heat and the sound plan stay. Then the audio is remade (only changed lines are
    recorded again) or marked as out of date. which = (this story's number, how many stories in this run)."""
    folder, story, result = load_story(cfg, run_id)
    level = result.get("level") or story.get("level") or "family"
    if level == "explicit":
        if cfg["ADULT_CONTENT"].lower() != "on":
            raise ValueError("This is an Explicit story, and Explicit is turned off for this account.")
        adult_config(cfg)
    cfg["PROOFREAD"] = "on"
    system = EXPLICIT_SYSTEM if level == "explicit" else None
    names = [c.get("name") for c in story.get("cast") or [] if isinstance(c, dict)]
    scenes = story.get("scenes") or []
    pov = (story.get("prefs") or {}).get("pov") or story.get("pov") or detect_pov([x.get("narration", "") for x in scenes])
    before = after = 0
    changed = []
    label = f"Story {which[0]} of {which[1]}: " if which[1] > 1 else ""
    for i, sc in enumerate(scenes):
        old = sc.get("narration", "")
        found = sentence_issues(old, names, pov)
        before += len(found)
        report("writing", i / max(1, len(scenes)) * (0.8 if then_remaster else 1),
               f"{label}Cleaning up \"{story.get('title', '')}\": scene {i + 1} of {len(scenes)}")
        text = proofread(cfg, old, system)
        text = fix_sentences(cfg, text, system, names, pov=pov)
        if level == "explicit":
            try:
                text = adult_guard(cfg, text, system)
            except SceneRedo:
                text = old                                  # keep it as it was rather than lose the scene
        left = sentence_issues(text, names, pov)
        if len(left) > len(found) and len(found) > 0:      # never leave it worse than it was
            text, left = old, found
        after += len(left)
        if text.strip() != old.strip():
            sc["narration"] = text
            sc["proofread"] = True
            changed.append(i)
            keep = sc.get("sound")
            save_story_text(folder, story, result, [i])      # saved after every scene, so nothing is lost
            if keep:
                sc["sound"] = keep                          # small text fixes keep the sounds you had
                (folder / "story.json").write_text(json.dumps(story, indent=2))
    log(f"Cleaned up \"{story.get('title', '')}\": {len(changed)} of {len(scenes)} scenes changed; sentence problems "
        f"{before} → {after}.")
    out = {"ok": True, "run_id": run_id, "title": story.get("title", ""), "scenes_changed": len(changed),
           "scenes": len(scenes), "problems_before": before, "problems_after": after}
    if changed and then_remaster and (result.get("audio_file") or result.get("video_file")):
        report("narration", 0.8, f"{label}Recording the changed lines of \"{story.get('title', '')}\"")
        remaster_story(cfg, run_id)
        out["remade"] = True
    return out


def rebuild_video(cfg, folder, story, final, timeline, job, redraw=False):
    """A story's video made again around new audio, with the same pictures and clips (nothing is drawn or filmed
    again), each scene's pictures lasting exactly as long as that scene is read now, and its subtitles retimed.
    timeline: {"segments": [{"kind": "title"|"scene", "index", "start", "end", "lines": [...]}], "total"} on the
    new audio `final`. The old video is kept as story.before.mp4 the first time."""
    job = Path(job)
    job.mkdir(parents=True, exist_ok=True)
    for f_ in list(folder.glob("clip_*.mp4")) + list(folder.glob("moment_*.mp4")):
        shutil.copy(f_, job / f_.name)
    if redraw:                    # new illustrations: planned again from each scene, drawn by the picture app
        for sc in story.get("scenes") or []:
            sc.pop("shot_planned", None)
    else:
        for f_ in folder.glob("scene_*.png"):
            shutil.copy(f_, job / f_.name.replace("scene_", "pic_"))
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(folder / "story.mp4")],
                           capture_output=True, text=True).stdout.strip().split(",")
    w, h = (int(probe[0]), int(probe[1])) if len(probe) == 2 else (1920, 1080)
    vcfg = dict(cfg) if redraw else dict(cfg, IMAGE_PROVIDER="cards")
    with tempfile.TemporaryDirectory() as tmpd:
        slices = slice_segments(final, timeline, job)
        _, pics, seg_lens = make_video(vcfg, story, slices, job, Path(tmpd), w, h, h > w,
                                       Path(tmpd) / "story.mp4", "existing", None)
        if redraw:                # the new pictures are kept with the story (the old ones go)
            for f_ in folder.glob("scene_*.png"):
                f_.unlink()
            for f_ in job.glob("pic_*.png"):
                shutil.copy(f_, folder / f_.name.replace("pic_", "scene_"))
        if not (folder / "story.before.mp4").exists() and (folder / "story.mp4").exists():
            shutil.copy2(folder / "story.mp4", folder / "story.before.mp4")
        shutil.copy(Path(tmpd) / "story.mp4", folder / "story.mp4")
    if cfg.get("SUBTITLES", "on") == "on":
        texts, spans = subtitle_lines(timeline, seg_lens, story.get("title", ""))
        write_subtitles(texts, spans, folder / "story.srt", folder / "story.vtt")
        burn_subtitles(cfg, folder / "story.mp4", folder / "story.srt")
    return seg_lens


def remaster_story(cfg, run_id, revoice=False, redesign=False, redraw=False, style=None):
    """Make the audio again (and the video's sound) from the story as it is now: new or changed lines
    are recorded, the sound is designed and mixed again with your current sound files."""
    folder, story, result = load_story(cfg, run_id)
    level = result.get("level") or "family"
    story["level"] = level                     # pictures follow the story's level (grown-up ones stay tasteful)
    if style and redraw:                       # drawn again in another look (Storybook, Realistic, Cinematic)
        story["picture_style"] = picture_style({"picture_style": style})
    if level == "explicit":
        adult_config(cfg)
    if story.get("bedtime") and not cfg.get("_BEDTIME"):
        bedtime_config(cfg)                    # a bedtime story stays soft when its audio is made again
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
        if redraw and pick_image_provider(cfg) == "local" and not local_image_ready(cfg):
            report("video", 0.05, "Opening Draw Things…")
            ok, what = open_mac_app(cfg, "draw-things")
            if not ok:
                raise RuntimeError(f"Draw Things isn't answering ({what}). {DRAW_THINGS_FIX}")
        if mode == "video" and (folder / "story.mp4").exists():
            report("video", 0.1, "Drawing new pictures and putting the video together" if redraw else
                   "Putting the video together again with the new sound")
            rebuild_video(cfg, folder, story, final, timeline, job, redraw)
            (folder / "story.json").write_text(json.dumps(story, indent=2))
        if redraw and style and (folder / "cover.jpg").exists():     # a new look gets a new cover too
            (folder / "cover.jpg").unlink()
            make_cover(cfg, story, folder, level)
        if redraw and (folder / "story-book.pdf").exists():
            report("finishing", 0.2, "Laying out the picture book with the new pictures")
            if mode != "video":
                make_pictures(cfg, story, job, 1650, 1100, False, range(len(story["scenes"])), None)
                for f_ in folder.glob("scene_*.png"):
                    f_.unlink()
                for f_ in job.glob("pic_[0-9][0-9].png"):
                    shutil.copy(f_, folder / f_.name.replace("pic_", "scene_"))
            imgs = [folder / f"scene_{i + 1:02d}.png" for i in range(len(story["scenes"]))]
            try:
                make_book(story, [f if f.exists() else None for f in imgs],
                          folder / "cover.jpg" if (folder / "cover.jpg").exists() else None, folder / "story-book.pdf",
                          result.get("series", "") if result.get("episode", 1) > 1 else "")
            except Exception as e:
                log(f"Picture book skipped ({e}).")
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
    try:                                                   # for the read-along player's chapter list
        (out_dir / "chapters.json").write_text(json.dumps(
            [{"start": round(st, 2), "title": name} for st, name in marks], indent=1))
    except OSError:
        pass
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


class AlreadyRunning(Exception):
    pass


def claim_job(job):
    """Only one copy of a story is ever made at a time (two at once would trip over each other's files): a second
    start of the same story while the first is still going is turned away."""
    lock = job / "running.pid"
    try:
        pid = int(lock.read_text().split()[0])
        if pid != os.getpid():
            os.kill(pid, 0)                                    # still alive?
            cmd = Path(f"/proc/{pid}/cmdline")
            if not cmd.exists() or b"storymaker" in cmd.read_bytes():
                return False
    except (OSError, ValueError, IndexError):
        pass
    lock.write_text(str(os.getpid()))
    return True


def run_job(cfg, args, idea, scenes, vprovider, job_id, job, level, series):
    clip_mode = args.mode == "video" and vprovider in ("omni", "veo", "demo")
    w, h = (1080, 1920) if args.portrait else (1920, 1080)
    job.mkdir(parents=True, exist_ok=True)
    if not claim_job(job):
        raise AlreadyRunning("This story is already being made. Its progress is on the page: there's no need to "
                             "start it again.")
    try:
        return _run_job(cfg, args, idea, scenes, vprovider, job_id, job, level, series, clip_mode, w, h)
    finally:
        (job / "running.pid").unlink(missing_ok=True)


def _run_job(cfg, args, idea, scenes, vprovider, job_id, job, level, series, clip_mode, w, h):
    write_status(cfg, job_id, state="running", step="writing")
    if getattr(args, "adventure", False):
        return make_adventure(cfg, args, idea, job_id, job, level)

    # A continuing series reuses its character sheet so the characters look the same as last time.
    if series:
        sref = series_root(cfg) / series["slug"] / "character_ref.png"
        if sref.exists() and not (job / "character_ref.png").exists():
            shutil.copy(sref, job / "character_ref.png")

    progress_start(cfg, job_id, job, args.mode,
                   bool(args.book) or (args.mode == "video" and cfg["BOOK"] == "on"))
    if cfg.get("_BEDTIME"):
        (job / "bedtime").touch()                 # so carrying on later keeps it a bedtime story
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
        if cfg.get("_BEDTIME"):
            story["bedtime"] = True
        story["keywords"] = cfg.get("_KEYWORDS") or []
        if cfg.get("_PREFS"):
            story["prefs"] = cfg["_PREFS"]
        if args.sounds:
            story["sound_wishes"] = args.sounds.strip()[:300]
        apply_media_style(cfg, story)
        if args.narrator and VOICE_SPEC.match(args.narrator):
            story["voice_choice"] = {"narrator": args.narrator, "characters": {}}
        bible = bible_of(series)
        story["picture_style"] = picture_style({"picture_style": args.picture_style or bible.get("picture_style")
                                                or last_episode_style(series) or cfg.get("PICTURE_STYLE")})
        if not args.narrator and (bible.get("narrator") or any(c.get("voice") for c in bible.get("characters") or [])):
            story["voice_choice"] = {"narrator": bible.get("narrator") or "",
                                     "characters": {c["name"]: c["voice"] for c in bible.get("characters") or []
                                                    if c.get("name") and c.get("voice")}}
        story_file.write_text(json.dumps(story, indent=2))
    if args.picture_style:                      # (carrying on: a style chosen now wins)
        story["picture_style"] = picture_style({"picture_style": args.picture_style})
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
               "picture_style": story.get("picture_style", "storybook"), "bedtime": bool(story.get("bedtime")),
               **(story.get("prefs") or {})}
    result = {"ok": True, "job_id": job_id, "request": request, "title": story["title"], "logline": story.get("logline", ""),
              "level": level, "story": story_markdown(story, subtitle), "folder": str(out_dir),
              "bedtime": bool(story.get("bedtime"))}
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
            for m_ in job.glob("moment_[0-9][0-9].mp4"):          # animated big moments, kept for remakes
                shutil.copy(m_, out_dir / m_.name)
            if story.get("big_moments"):
                (out_dir / "story.json").write_text(json.dumps(story, indent=2))
            for i, p_ in pics.items():
                shutil.copy(p_, out_dir / f"scene_{i + 1:02d}.png")
            for p_ in job.glob("pic_[0-9][0-9]_[0-9].png"):     # the extra pictures of long scenes, kept too
                shutil.copy(p_, out_dir / p_.name.replace("pic_", "scene_"))
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
        texts, spans = subtitle_lines(timeline, seg_lens, story.get("title", ""))
        write_subtitles(texts, spans, out_dir / "story.srt", out_dir / "story.vtt")
        if result.get("video_file"):
            burn_subtitles(cfg, out_dir / result["video_file"], out_dir / "story.srt")
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

    recap = episode_recap(cfg, story, series) if complete else None
    saved = save_episode(cfg, series, story, result, voices, out_dir, level, args.genre, args.series,
                         job_id, complete, recap)
    result.update(series=saved["name"], episode=episode, mode=args.mode,
                  created=dt.datetime.now().isoformat(timespec="seconds"), run_id=run_id)
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))
    if complete:
        shutil.rmtree(job, ignore_errors=True)        # everything is in the output folder now
    else:
        result["retry_available"] = True
    write_status(cfg, job_id, state="done", result=result)
    return result


# ───────────────────────────── choose your own adventure ─────────────────────────────
ADVENTURE_PROMPT = """You are writing a choose-your-own-adventure story that will be read aloud. The listener decides
what happens at each turning point.

Story idea: {idea}
Genre: {genre}
Reader: {audience}
{rules}
Write the whole branching story as JSON only, in exactly this shape:
{{"title": "a title", "logline": "one sentence that makes you want to play",
  "characters": "Name, a short description; Name, a short description",
  "start": "a",
  "passages": {{
    "a": {{"text": "the opening…", "choices": [{{"label": "Follow the light into the cave", "to": "b"}},
                                         {{"label": "Row back to the boat", "to": "c"}}]}},
    "b": {{"text": "…", "choices": [ … ]}},
    "z": {{"text": "…the last lines of this ending.", "ending": "happy"}}
  }}}}

Rules:
- Every path from the start passes {depth} decisions before it reaches an ending. Each decision has 2 choices
  (3 at most). Paths may join up again, so the story stays a sensible size: about {total} passages in all.
- At least {endings} different endings. Make them feel earned: mostly good or bittersweet, some funny or surprising.
  An ending's "ending" is one word: happy, bittersweet, funny, sad or surprising.
- Each passage is {words} words of vivid narration with a little dialogue in quotes. It carries straight on from the
  choice that led to it (it can say what the reader decided), and every passage that isn't an ending stops right at
  the moment of decision.
- Choice labels are short (under 10 words), clear and tempting, written as what the reader does.
- Passage ids are short and lowercase. Every "to" names a passage that exists. Endings have "ending" and no "choices".
- The characters keep the same names and personalities on every path.
"""


def tidy_adventure(adv):
    """Make sure the branching story works: every choice leads somewhere, every passage can be reached, and every
    path can still reach an ending."""
    raw = adv.get("passages") or {}
    if isinstance(raw, list):                                  # some writers send a list of {id, text, choices}
        raw = {str(p.get("id")): p for p in raw if isinstance(p, dict) and p.get("id") is not None}
    P = {}
    for k, p in raw.items():
        if isinstance(p, dict) and str(p.get("text") or "").strip():
            P[str(k).strip()] = {"text": str(p["text"]).strip(), "choices": p.get("choices") or [],
                                 "ending": p.get("ending")}
    if len(P) < 3:
        raise ValueError("The adventure came back too short. Try again.")
    for k, p in P.items():
        seen, good = set(), []
        for c in p["choices"] if isinstance(p["choices"], list) else []:
            if not isinstance(c, dict):
                continue
            to, label = str(c.get("to") or "").strip(), str(c.get("label") or "").strip()
            if to in P and to != k and label and to not in seen:
                seen.add(to)
                good.append({"label": label[:120], "to": to})
        p["choices"] = good[:3]
    start = str(adv.get("start") or "").strip()
    if start not in P:
        start = next(iter(P))
    # every passage must be able to reach an ending; a choice that only goes round in circles is dropped
    def ends_from(k, path=()):
        p = P[k]
        if not p["choices"]:
            return True
        return any(c["to"] not in path and ends_from(c["to"], path + (k,)) for c in p["choices"])
    for k, p in P.items():
        p["choices"] = [c for c in p["choices"] if ends_from(c["to"], (k,))]
    for p in P.values():
        if not p["choices"]:
            p["ending"] = str(p.get("ending") or "the end").strip().lower()[:20] or "the end"
            p.pop("choices")
        else:
            p.pop("ending", None)
    reach, todo = [], [start]                    # keep what can be reached, in reading order
    while todo:
        k = todo.pop(0)
        if k in reach:
            continue
        reach.append(k)
        todo += [c["to"] for c in P[k].get("choices", [])]
    P = {k: P[k] for k in reach}
    if not any("ending" in p for p in P.values()) or len(P) < 3:
        raise ValueError("The adventure didn't hold together. Try again.")
    return {"title": str(adv.get("title") or "An Adventure").strip()[:120], "logline": str(adv.get("logline") or "").strip(),
            "characters": adv.get("characters") or "", "start": start, "passages": P}


def demo_adventure(idea):
    t = idea.strip().rstrip(".") or "the lost lantern"
    return {"title": f"The Choice of {t.title()}", "logline": f"A demo adventure about {t}.",
            "characters": "Mira, a curious girl; Old Tom, the lighthouse keeper", "start": "a", "passages": {
        "a": {"text": "Mira found a lantern glowing on the beach. “Where did you come from?” she whispered. A path led "
                      "to the cliffs, and a little boat bobbed by the pier.",
              "choices": [{"label": "Climb the cliff path", "to": "b"}, {"label": "Take the little boat", "to": "c"}]},
        "b": {"text": "The path wound up to the lighthouse, where Old Tom was waiting. “You found it,” he said.",
              "choices": [{"label": "Give Tom the lantern", "to": "d"}, {"label": "Keep it and run", "to": "e"}]},
        "c": {"text": "The boat drifted out to a cave full of tiny lights, all blinking like they were saying hello.",
              "choices": [{"label": "Row into the cave", "to": "f"}, {"label": "Row home", "to": "e"}]},
        "d": {"text": "Tom hung the lantern at the top, and every ship at sea found its way home that night.", "ending": "happy"},
        "e": {"text": "Mira hurried home and hid the lantern under her bed. It glowed softly all night, keeping its secret.",
              "ending": "bittersweet"},
        "f": {"text": "The lights were fireflies of the sea, and they followed her home, a river of stars behind the boat.",
              "ending": "surprising"}}}


def adventure_markdown(adv):
    """The whole adventure on paper: every passage, and where each choice leads."""
    order = list(adv["passages"])
    num = {k: i + 1 for i, k in enumerate(order)}
    out = [f"# {adv['title']}", ""]
    if adv.get("logline"):
        out += [f"*{adv['logline']}*", ""]
    out += ["*A choose-your-own-adventure: start at 1 and choose.*", ""]
    for k in order:
        p = adv["passages"][k]
        out += [f"## {num[k]}", "", p["text"], ""]
        if p.get("choices"):
            out += [f"- {c['label']}: turn to {num[c['to']]}." for c in p["choices"]] + [""]
        else:
            out += [f"**The End** ({p.get('ending', 'the end')})", ""]
    return "\n".join(out)


def adventure_spoken(p):
    if not p.get("choices"):
        return p["text"] + " The end."
    labels = [c["label"].rstrip(".!?") for c in p["choices"]]
    return p["text"] + " What will you do? " + " Or ".join(f"{l}?" for l in labels)


def make_adventure(cfg, args, idea, job_id, job, level):
    """--adventure: a branching story. Written in one go, checked so every path works, then (in audio or video mode)
    each passage is read by the narrator. The page plays it: listen, then pick what happens next."""
    mode = "audio" if args.mode in ("audio", "video") else "story"
    progress_start(cfg, job_id, job, mode, False)
    audience, rules, length = level_info(level)
    rules += style_rules(cfg.get("_PREFS"), level) + style_block(cfg.get("_STYLE"), level) + WRITING_CRAFT
    if cfg.get("_BEDTIME"):
        rules += BEDTIME_RULES
    depth = {"brief": 2, "standard": 3, "rich": 3, "epic": 4}.get(args.detail, 3)
    words = {"brief": "60–90", "standard": "100–150", "rich": "160–220", "epic": "180–260"}.get(args.detail, "100–150")
    words = "–".join(str(max(30, int(int(x) * length))) for x in words.split("–"))
    total, endings = {2: (7, 3), 3: (12, 4), 4: (18, 5)}[depth]
    adv_file = job / "adventure.json"
    if adv_file.exists():
        adv = json.loads(adv_file.read_text())
        log(f"Resuming the adventure '{adv['title']}'.")
    else:
        provider = cfg["STORY_PROVIDER"].lower()
        with Ticker("writing", "Writing the adventure and all its paths", 200 if provider == "ollama" else 40):
            if provider == "demo":
                raw = demo_adventure(idea)
            else:
                raw = ask_writer(cfg, ADVENTURE_PROMPT.format(idea=idea, genre=args.genre, audience=audience, rules=rules,
                                                              depth=depth, total=total, endings=endings, words=words),
                                 think="plan")
            adv = tidy_adventure(raw)
        if cfg.get("_BEDTIME"):
            adv["bedtime"] = True
        adv_file.write_text(json.dumps(adv, indent=2))
    P = adv["passages"]
    n_end = sum(1 for p in P.values() if "ending" in p)
    report("writing", 1, f"Adventure written: {len(P)} passages, {n_end} endings", title=adv["title"],
           logline=adv.get("logline", ""), opening=P[adv["start"]]["text"][:320])
    run_id = f"{dt.datetime.now():%Y-%m-%d_%H%M%S}_{slugify(adv['title'])}"
    out_dir = Path(os.path.expanduser(cfg["OUTPUT_DIR"])) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    story = {"title": adv["title"], "logline": adv.get("logline", ""), "characters": adv.get("characters", ""),
             "adventure": True, "genre": args.genre, "level": level, "bedtime": bool(adv.get("bedtime")),
             "scenes": [{"heading": f"Passage {i + 1}", "narration": adventure_spoken(p), "lines": [],
                         "image_prompt": p["text"][:300], "passage": k} for i, (k, p) in enumerate(P.items())]}
    md = adventure_markdown(adv)
    (out_dir / "story.md").write_text(md)
    (out_dir / "story.json").write_text(json.dumps(story, indent=2))
    if mode == "audio":
        engine = pick_tts(cfg)
        voice = args.narrator if engine == "kokoro" and args.narrator and VOICE_SPEC.match(args.narrator) else None
        for i, (k, p) in enumerate(P.items()):
            mp3 = job / f"passage_{k}.mp3"
            if not valid_media(mp3):
                wav = job / f"passage_{k}.wav"
                narrate(cfg, adventure_spoken(p), wav, engine, voice)
                run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af", "apad=pad_dur=0.6", "-c:a", "libmp3lame",
                     "-b:a", "128k", str(mp3)])
                wav.unlink(missing_ok=True)
            shutil.copy(mp3, out_dir / f"passage_{k}.mp3")
            p["audio"] = f"passage_{k}.mp3"
            report("narration", (i + 1) / len(P), f"Reading the passages aloud: {i + 1} of {len(P)}")
    (out_dir / "adventure.json").write_text(json.dumps(adv, indent=2))
    report("finishing", 0.4, "Painting the cover")
    try:
        make_cover(cfg, story, out_dir, level)
    except Exception as e:
        log(f"Cover skipped ({e}).")
    make_thumbnail(out_dir, [out_dir / "cover.jpg"])
    request = {"idea": idea, "genre": args.genre, "level": level, "mode": mode, "detail": args.detail,
               "adventure": True, "bedtime": bool(adv.get("bedtime")), "narrator": args.narrator or ""}
    result = {"ok": True, "job_id": job_id, "request": request, "title": adv["title"], "logline": adv.get("logline", ""),
              "level": level, "story": md, "folder": str(out_dir), "adventure": True, "passages": len(P),
              "endings": n_end, "bedtime": bool(adv.get("bedtime")), "story_file": str(out_dir / "story.md"),
              "mode": mode, "episode": 1, "created": dt.datetime.now().isoformat(timespec="seconds"), "run_id": run_id}
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))
    shutil.rmtree(job, ignore_errors=True)
    write_status(cfg, job_id, state="done", result=result)
    return result


def job_key(args, idea, cfg, scenes, vprovider, level, series):
    parts = [idea, args.genre, args.audience or "", level, str(scenes), args.mode, cfg["STORY_PROVIDER"],
             vprovider, str(args.portrait), str(args.book),
             series["slug"] if series else "", str(len(done_episodes(series))) if series else "",
             str(args.chapters), args.detail, args.keywords or "", args.sounds or "", args.narrator or "",
             json.dumps(cfg.get("_PREFS") or {}, sort_keys=True)]
    if getattr(args, "adventure", False):
        parts.append("adventure")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def recover_settings(cfg, args):
    """An unfinished story: its plan (outline.json) and the scenes it wrote are in its job folder. Work out how it
    was set up (chapters and scenes from the plan; level and detail from its log) and carry on in that folder,
    so only the missing scenes are written."""
    rid = re.sub(r"[^a-f0-9]", "", args.recover or "")
    folder = jobs_dir(cfg) / rid
    if rid and not (folder / "outline.json").exists() and (folder / "story.json").exists():
        # written in full before it stopped (it was recording or filming): carry on from the finished text
        story = json.loads((folder / "story.json").read_text())
        if not args.idea:
            args.idea = (story.get("logline") or story.get("title") or "Continue the story.")[:2000]
        if not args.level and story.get("level") in LEVELS:
            args.level = story["level"]
        args.bedtime = args.bedtime or bool(story.get("bedtime"))
        args._job = rid
        return
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
    args.bedtime = args.bedtime or (folder / "bedtime").exists()
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
    ap.add_argument("--no-proofread", action="store_true", help="skip the copy editor's pass (faster)")
    ap.add_argument("--recover", metavar="JOB", help="carry on an unfinished story from what it already wrote")
    ap.add_argument("--background", action="store_true", help="return at once; notify when finished")
    ap.add_argument("--status", metavar="JOB_ID", help="print the status of a background job")
    ap.add_argument("--open", action="store_true", help="Mac: open the result when done")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--features", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--export-pdf", metavar="STORY", help="make a reading-copy PDF of a finished story (folder name)")
    ap.add_argument("--export-epub", metavar="STORY", help="make an EPUB e-book of a finished story")
    ap.add_argument("--export-print", metavar="STORY", help="make a print-ready picture book (8.5 in square, bleed, 300 dpi)")
    ap.add_argument("--delete", metavar="STORY", help="delete a finished story (folder name) and its series entry")
    ap.add_argument("--edit", metavar="STORY", help="change a finished story's text (JSON on stdin)")
    ap.add_argument("--rewrite", metavar="STORY", help="rewrite part of a story with the AI (instructions on stdin)")
    ap.add_argument("--target", default="story", help="with --rewrite: story, chapter:N or scene:N")
    ap.add_argument("--instructions", help=argparse.SUPPRESS)
    ap.add_argument("--no-remake", action="store_true", help="with --rewrite: don't remake the audio afterwards")
    ap.add_argument("--remaster", metavar="STORY", help="make a finished story's audio (and video sound) again")
    ap.add_argument("--check-writing", metavar="STORY[,STORY…]",
                    help="count the sentence and spelling problems in finished stories (instant, no AI)")
    ap.add_argument("--cleanup", metavar="STORY[,STORY…]",
                    help="proofread and fix the sentences of finished stories, then remake their audio")
    ap.add_argument("--revoice", action="store_true", help="with --remaster: choose the character voices again")
    ap.add_argument("--redesign", action="store_true", help="with --remaster: choose the sounds again")
    ap.add_argument("--redraw", action="store_true", help="with --remaster: draw new pictures for the video and book")
    ap.add_argument("--draw-portraits", metavar="SERIES_SLUG", help="draw a portrait for every character in a series' bible")
    ap.add_argument("--big-moments", type=int, choices=range(0, 6), metavar="N",
                    help="animate this many of the most dramatic scenes (Veo, paid) in a picture video")
    ap.add_argument("--adventure", action="store_true",
                    help="a choose-your-own-adventure: the reader picks what happens at each turning point")
    ap.add_argument("--bedtime", action="store_true",
                    help="a calm story for winding down: no scares, soft sounds, a slower narrator")
    ap.add_argument("--picture-style", choices=sorted(PICTURE_STYLES),
                    help="how the pictures look: storybook, realistic or cinematic (default: PICTURE_STYLE setting)")
    ap.add_argument("--skip-checks", dest="skip_checks", action="store_true",
                    help="don't check the writer, voices and picture app before starting")
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
    if args.child:                       # the background copy doing the work: Stop on the page ends it cleanly
        global _WORKER_PID
        _WORKER_PID = os.getpid()
        signal.signal(signal.SIGTERM, _stop_requested)

    cfg = load_settings()
    if args.provider:
        cfg["STORY_PROVIDER"] = args.provider
    if args.video:
        cfg["VIDEO_PROVIDER"] = args.video

    cfg["_STYLE"] = {} if args.no_style else style_from_env()
    if args.no_proofread:
        cfg["PROOFREAD"] = "off"
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
                          "voices": [dict(zip(("id", "name", "gender", "accent", "note"), v), group=voice_group(v))
                                     for v in KOKORO_CATALOGUE],
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
    if args.check_writing:
        out = []
        for x in [x for x in args.check_writing.split(",") if x.strip()]:
            try:
                out.append(check_writing(cfg, x))
            except Exception as e:
                out.append({"run_id": x, "error": str(e)[:200]})
        print(json.dumps({"ok": True, "stories": out}))
        return
    if args.draw_portraits:
        slug = re.sub(r"[^a-z0-9-]", "", args.draw_portraits.lower())
        job_id = args.job_id or hashlib.sha1(f"portraits|{slug}|{time.time()}".encode()).hexdigest()[:12]
        if args.background and not args.child:
            logf = jobs_dir(cfg) / f"{job_id}.log"
            argv = [a for a in sys.argv[1:] if a != "--background"]
            cmd = [sys.executable, str(Path(__file__).resolve()), *argv, "--job-id", job_id, "--child"]
            with open(logf, "a") as lf:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                        start_new_session=True, close_fds=True)
            write_status(cfg, job_id, state="queued", pid=proc.pid, kind="portraits", story=slug)
            print(json.dumps({"ok": True, "queued": True, "job_id": job_id}))
            return
        def rep(frac, text):
            write_status(cfg, job_id, state="running", kind="portraits", story=slug, progress=round(100 * frac),
                         detail=text)
        try:
            out = draw_portraits(cfg, slug, rep)
            write_status(cfg, job_id, state="done", kind="portraits", story=slug, progress=100, detail="Done",
                         result=out)
            print(json.dumps({"ok": True, **out}))
        except JobStopped:
            write_status(cfg, job_id, state="stopped", kind="portraits", story=slug, detail="Stopped.")
        except Exception as e:
            write_status(cfg, job_id, state="failed", kind="portraits", story=slug, error=str(e)[:300])
            print(json.dumps({"ok": False, "error": str(e)[:300]}))
        return
    if args.cleanup:
        ids = [x for x in args.cleanup.split(",") if x.strip()]
        try:
            for x in ids:
                story_folder(cfg, x)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return
        job_id = args.job_id or hashlib.sha1(f"cleanup|{args.cleanup}|{time.time()}".encode()).hexdigest()[:12]
        if args.background and not args.child:
            logf = jobs_dir(cfg) / f"{job_id}.log"
            argv = [a for a in sys.argv[1:] if a != "--background"]
            cmd = [sys.executable, str(Path(__file__).resolve()), *argv, "--job-id", job_id, "--child"]
            with open(logf, "a") as lf:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                        start_new_session=True, close_fds=True)
            write_status(cfg, job_id, state="queued", pid=proc.pid, kind="cleanup", story=ids[0], stories=ids)
            print(json.dumps({"ok": True, "queued": True, "job_id": job_id}))
            return
        progress_start(cfg, job_id, jobs_dir(cfg) / job_id, "audio", False)
        _PROG["status"].update(kind="cleanup", story=ids[0], stories=ids)
        results, failed = [], []
        try:
            for n_, x in enumerate(ids):
                try:
                    results.append(cleanup_story(dict(cfg), x, not args.no_remake, (n_ + 1, len(ids))))
                except Exception as e:                       # one story's problem doesn't stop the others
                    log(f"Couldn't clean up {x}: {str(e)[:200]}")
                    failed.append({"run_id": x, "error": str(e)[:200]})
        except JobStopped:
            msg = (f"Stopped. {len(results)} of {len(ids)} stories were cleaned up before you stopped it; the one it "
                   "was working on may have some scenes cleaned up and some not. Its sound wasn't remade.")
            log(msg)
            write_status(cfg, job_id, state="stopped", detail=msg, kind="cleanup", story=ids[0])
            print(json.dumps({"ok": False, "stopped": True, "error": msg}))
            return
        summary = {"stories": len(results), "scenes_changed": sum(r["scenes_changed"] for r in results),
                   "problems_before": sum(r["problems_before"] for r in results),
                   "problems_after": sum(r["problems_after"] for r in results), "failed": failed}
        if not results:
            write_status(cfg, job_id, state="failed", error=failed[0]["error"] if failed else "Nothing to clean up",
                         kind="cleanup", story=ids[0])
            print(json.dumps({"ok": False, "error": failed[0]["error"] if failed else "Nothing to clean up"}))
            raise SystemExit(1)
        write_status(cfg, job_id, state="done", kind="cleanup", story=ids[0], result={"run_id": ids[0], **summary})
        print(json.dumps({"ok": True, **summary, "results": results}))
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
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                        start_new_session=True, close_fds=True)
            write_status(cfg, job_id, state="queued", pid=proc.pid, kind="rewrite" if args.rewrite else "remaster", story=run_id)
            print(json.dumps({"ok": True, "queued": True, "job_id": job_id}))
            return
        progress_start(cfg, job_id, jobs_dir(cfg) / job_id, "audio", False)
        _PROG["status"].update(kind="rewrite" if args.rewrite else "remaster", story=run_id)
        try:
            if args.rewrite:
                out = rewrite_story(cfg, run_id, args.target, instructions or "", not args.no_remake, args.keywords)
            else:
                out = remaster_story(cfg, run_id, args.revoice, args.redesign, args.redraw, args.picture_style)
        except JobStopped:
            msg = ("Stopped. The story's sound and video may be half-made now: remake them when you're ready."
                   if not args.rewrite else "Stopped. The story wasn't changed.")
            log(msg)
            if not args.rewrite:
                try:                                         # the library shows "remake the audio to match"
                    rf = story_folder(cfg, run_id) / "result.json"
                    r_ = json.loads(rf.read_text())
                    r_["audio_outdated"] = True
                    rf.write_text(json.dumps(r_, indent=2))
                except Exception:
                    pass
            write_status(cfg, job_id, state="stopped", detail=msg, kind="rewrite" if args.rewrite else "remaster",
                         story=run_id)
            print(json.dumps({"ok": False, "stopped": True, "error": msg}))
            return
        except Exception as e:
            write_status(cfg, job_id, state="failed", error=str(e), kind="rewrite" if args.rewrite else "remaster",
                         story=run_id)
            print(json.dumps({"ok": False, "error": str(e)}))
            raise SystemExit(1)
        write_status(cfg, job_id, state="done", kind="rewrite" if args.rewrite else "remaster", story=run_id,
                     result={"run_id": run_id})
        print(json.dumps(out if isinstance(out, dict) else {"ok": True}))
        return
    if args.export_epub or args.export_print:
        try:
            print(json.dumps(export_epub(cfg, args.export_epub) if args.export_epub else export_print(cfg, args.export_print)))
        except (ValueError, OSError) as e:
            print(json.dumps({"ok": False, "error": str(e)}))
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
    if args.bedtime:
        bedtime_config(cfg)
    if args.big_moments is not None:
        cfg["BIG_MOMENTS"] = str(args.big_moments)

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
        checks = preflight(cfg, args, level, vprovider, auto=False)
        print(json.dumps({"ok": True, **est, "level": level, **extra, "checks": checks}))
        return
    if not args.child and not getattr(args, "skip_checks", False):
        checks = preflight(cfg, args, level, vprovider, auto=True)
        if any(c["blocking"] for c in checks):
            print(json.dumps({"ok": False, "error": "Not ready yet. " + preflight_message(checks), "checks": checks}))
            sys.exit(2)
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
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                                    start_new_session=True, close_fds=True)
        write_status(cfg, job_id, state="queued", pid=proc.pid)
        print(json.dumps({"ok": True, "queued": True, "job_id": job_id, **est,
                          "message": "Working on it — you'll get a notification when it's ready."}))
        return

    notify_on = cfg["NOTIFY"] == "always" or (cfg["NOTIFY"] == "background" and args.child)
    try:
        result = run_job(cfg, args, idea, scenes, vprovider, job_id, job, level, series)
    except AlreadyRunning as e:                           # the copy that's running keeps its progress as it is
        log(str(e))
        print(json.dumps({"ok": False, "error": str(e)}))
        raise SystemExit(0)
    except JobStopped:                                    # Stop (or Pause) pressed: what's written so far is kept
        wrote = (job / "outline.json").exists() or (job / "story.json").exists() or any(job.glob("scene_c*_s*.txt"))
        flag = jobs_dir(cfg) / f"{job_id}.pause"
        paused = flag.exists() and wrote
        flag.unlink(missing_ok=True)
        msg = ("Paused. Everything made so far is kept: press Resume in Unfinished stories to carry on from here."
               if paused else
               "Stopped. What was already made is kept: carry it on from Unfinished stories, or delete it there."
               if wrote else "Stopped before anything was written. Nothing was kept or spent.")
        if not wrote:
            shutil.rmtree(job, ignore_errors=True)
        log(msg)
        write_status(cfg, job_id, state="paused" if paused else "stopped", detail=msg,
                     title=(_PROG.get("status") or {}).get("title"))
        print(json.dumps({"ok": False, "stopped": True, "error": msg}))
        raise SystemExit(0)
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
