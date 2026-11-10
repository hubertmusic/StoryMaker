# StoryMaker — Apple Shortcut + Python

Type a story idea into a shortcut. StoryMaker writes the story, narrates it, and makes a video with
**real AI-generated video clips**, one cinematic 8-second shot per scene, with captions and the
narrator over each clip's own sound. You get back:

| File | What it is |
|---|---|
| `story.md` | The finished story in story form: title, one paragraph per scene, "The End" |
| `story.mp3` | The whole story narrated |
| `story.mp4` | Title shot, then one AI video clip per scene with captions and narration |
| `clip_01.mp4` … | The raw AI clips on their own |
| `character_ref.png` | The character sheet every clip was shown, so characters look the same |
| `story-book.pdf` | A printable picture book: cover, one page per scene with its picture and text, "The End" |
| `story.srt`, `story.vtt` | Subtitle files, timed to the narration (players can turn them on and off) |
| `thumb.jpg` | A cover picture for the library on the web page |
| `story.json`, `result.json` | The raw story data and the run's results |

**Built-in safety and convenience**

- **Price check first.** The shortcut shows "About $4.11: 5 video clips $4.00, …" and asks before anything is spent. Runs above `MAX_COST_USD` (default $10) refuse to start without your OK.
- **Nothing paid for is thrown away.** If a run fails or a clip is blocked, everything already made is kept. Run the same request again and it only pays for what's missing. Add `--fresh` to start over with a new story.
- **Same-looking characters.** It draws one character sheet first and shows it to every clip.
- **Runs in the background from iPhone.** The shortcut returns right away; a push notification with an **Open** button arrives when the video is ready.
- **Background music.** A track from your music folder, quieter whenever the narrator speaks.
- **Character voices.** The narrator keeps one voice; every character who speaks gets their own.
- **Story series.** Pick "Next episode" and it continues your last story with the same characters, look, voices and reading level, aware of what happened before.
- **Reading levels.** Young kids, kids, middle grade, teens, adults or everyone — changes vocabulary, length and how intense it gets, with stricter content rules for kids.
- **Free writing.** Groq's free tier or Ollama on your own computer can write the stories at no cost.
- **A web page with your library.** Make stories from any browser, watch the progress, and browse every story you've made.

It runs on your **Mac** (Run Shell Script) or on your **server** (Run Script Over SSH, so it works
from iPhone/iPad too). The same script handles both.

---

## Install or update (the safe way)

Download StoryMaker.zip, then in Terminal:

```bash
bash ~/StoryMaker/update.sh
```

The first time, when there's no `~/StoryMaker/update.sh` yet, use this instead:

```bash
bash <(unzip -p "$(ls -t ~/Downloads/StoryMaker*.zip | head -1)" storymaker/update.sh)
```

It uses the newest StoryMaker zip in Downloads and puts the program files in `~/StoryMaker`. It never touches
your stories, settings, sounds, music or Audio Studio books, and it uses full paths, so it does the same
thing from any folder. The first time, it asks for your user name, password and keys. Then it starts
StoryMaker (Docker) and Audio Studio.

## 1. Which AIs to sign up for

| Job | Recommended | Why | Roughly costs |
|---|---|---|---|
| Writing the story | **Claude** (Anthropic API) | Strong long-form creative writing | A few cents per story |
| Video clips | **Google Gemini Omni Flash** (Gemini API) | Top-ranked text-to-video with sound right now; Google's default video model | About $0.10 per second at 720p, so ~$0.80 per scene and ~$4 for a 5-scene story |
| Narrator voice | **ElevenLabs** | The best-known natural storytelling voices | Covered by an ElevenLabs plan's credits |

**Cheaper, one-key option:** a Google key alone can do all four jobs. Set `STORY_PROVIDER=gemini` and
leave the other keys blank. Gemini then writes the story and makes the clips, Gemini's voice
narrates, and Gemini draws a picture for any scene whose clip fails.

**More control:** `VIDEO_PROVIDER=veo` switches the clips to Veo 3.1, which costs more per second
than Omni.

### Subscriptions vs API keys (important)

The **app subscriptions** (Google AI Pro/Ultra, Claude Pro, ChatGPT Plus) are for using the apps and
websites. They **don't** cover a program calling the AI. For StoryMaker you need **API access**,
billed separately pay-as-you-go:

- **Google:** aistudio.google.com → Get API key, then set up billing. Video models aren't on the free tier.
- **Anthropic:** console.anthropic.com → API Keys, then add credit.
- **ElevenLabs:** a regular ElevenLabs plan works; its credits apply to API use. Profile → API Keys.

Set a monthly spending limit in the Google and Anthropic billing settings while you experiment.

## 2. Set up on the Mac

```bash
mkdir -p ~/StoryMaker && cd ~/StoryMaker
# copy storymaker.py, storymaker.env.example and requirements.txt into this folder
brew install ffmpeg
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp storymaker.env.example storymaker.env
open -e storymaker.env        # paste your API keys, save
```

Free test first (no keys, no cost; uses test-pattern "clips"):

```bash
.venv/bin/python storymaker.py --idea "a lighthouse keeper who befriends a storm" --provider demo --video demo --scenes 2 --open
```

Then a real, cheap test with 2 scenes:

```bash
.venv/bin/python storymaker.py --idea "a lighthouse keeper who befriends a storm" --scenes 2 --open
```

In **Shortcuts → Settings → Advanced**, turn on **Allow Running Scripts**.

## 3. Set up on the server (optional, for iPhone/iPad)

```bash
sudo apt install -y ffmpeg python3-venv
mkdir -p ~/StoryMaker && cd ~/StoryMaker
# copy the same files here
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp storymaker.env.example storymaker.env && nano storymaker.env
```

To get the video back to your phone, point `PUBLIC_DIR` at a folder nginx already serves and set
`PUBLIC_BASE_URL` to its address, for example:

```
PUBLIC_DIR=/srv/www/stories-7f3k9q
PUBLIC_BASE_URL=https://yourdomain.com/stories-7f3k9q
```

Anyone with a link can open those files, so use a hard-to-guess folder name or put basic auth on it.
In Shortcuts, the **Run Script Over SSH** action creates an SSH key; add its public key to
`~/.ssh/authorized_keys` on the server.

Video clips take a few minutes each (three are made at a time). If an SSH run stops partway, use
fewer scenes or run it on the Mac.

## 4. Music and notifications (optional)

**Music:** music plays softly under the whole story and dips while someone speaks. It's chosen to fit the mood:
romantic for Mature and Explicit, tense for thrillers, playful for kids, epic for adventure, and so on. Your own
tracks come first: drop MP3 or M4A files you have the right to use into `~/StoryMaker/music` and name them by mood,
for example `romantic-piano.mp3`, `tense-strings.mp3` or `calm-acoustic.mp3`. If none fits (or the folder is
empty), free music that matches the mood is downloaded from Freesound once and kept, with its credit in the sound
credits. Audio Studio does the same, with a **Background music** box in the Record step. For music only at the
start, at each new chapter and at the end, set `MUSIC_MODE=moments`. Use `--music piano` to pick a track by name,
or `--music off` for none.

**Notifications:** install the free **ntfy** app on your iPhone, tap **+**, and subscribe to a
hard-to-guess topic name like `storymaker-hub-7f3k9q`. Put the same name in `storymaker.env` as
`NTFY_TOPIC=`. When a background run finishes you get "Story ready: *title*" with an **Open** button
that plays the video (needs `PUBLIC_DIR`/`PUBLIC_BASE_URL` on the server). Anyone who knows a topic
name on ntfy.sh can read it, so keep it obscure, or run your own ntfy server in Docker and set
`NTFY_SERVER`. On a Mac you also get a normal macOS notification.

## 5. Reading levels, series, voices and the picture book

**Reading levels** (`--level`):

| Level | For | What changes |
|---|---|---|
| `young-kids` | ages 3–5 | Very simple words, cozy, no scary moments; problems solved with kindness. Shorter scenes |
| `kids` | ages 6–8 | Lively and simple; brief mild adventure, nothing truly frightening |
| `middle-grade` | ages 9–12 | Real stakes and suspense; no gore, romance or strong language |
| `teen` | ages 13–17 | Higher stakes and emotional depth; nothing graphic or explicit |
| `adult` | adults | Literary, mature themes handled with restraint; nothing explicit |
| `mature` | adults (R) | Strong language, intense violence, dark themes; romance can happen but sex stays off the page. Works with every writer and with video |
| `explicit` | adults only (18+) | Explicit adult scenes. Off until you turn it on; see below |
| `family` (default) | everyone together | Works for kids and grown-ups at once |

For `young-kids` and `kids` every video clip and picture is also told to stay gentle and child-friendly.

**Explicit controls** (shown when you pick Explicit): **Heat** 1–5 (sensual → maximum), **Pacing**
(slow burn, steady, straight to it), **Language** (tasteful, frank, crude), **Turn-ons to include** and
**Hard limits**. Under **More options**, **Told by** sets first person ("I") or third person for any
story. The local writer also gets livelier settings (`OLLAMA_TEMPERATURE`) and a penalty against
repeating itself. Turn-ons must stay between consenting adults; anything else is refused.

**The best writer for a 16 GB Mac:** `dolphin3` is small and its scenes are thin. A 12B
fiction-tuned model writes far better and still fits next to the voices and pictures:

```bash
ollama pull hf.co/bartowski/TheDrummer_Rocinante-X-12B-v1-GGUF:Q4_K_M
```

then in `storymaker.env`: `OLLAMA_ADULT_MODEL=hf.co/bartowski/TheDrummer_Rocinante-X-12B-v1-GGUF:Q4_K_M`
(about 7.5 GB). If the pull says the file isn't found, open that page on huggingface.co and copy a
Q4_K_M name; model versions change often. Use **Scene length: Rich** or **Epic** for long scenes.

**Free pictures and video (Draw Things):** install **Draw Things** from the Mac App Store (free), open
it, download an SDXL model in its model list (a "Lightning" or "Turbo" SDXL is fast on 16 GB), then
**Settings → API Server**: turn it on, protocol **HTTP**, port **7860**. In `storymaker.env` set
`LOCAL_IMAGE_URL=http://host.docker.internal:7860` (web page) and `docker compose restart`. Then:
- every audio or text story gets painted **cover art** (also in the audiobook),
- the picture book and the **Picture slideshow** video are free (moving-camera video from the pictures),
- Explicit stories can have pictures and slideshow video too, but always tasteful (no nudity). They're
  made on your Mac, never online.

Draw Things forgets that switch whenever it closes; Audio Studio turns it back on by itself (section 18). Real AI video (moving characters) doesn't
fit in 16 GB at a usable speed; for that, Gemini video clips (paid) work for non-explicit stories.

**Explicit (18+):** the online AIs (Groq, Claude, Gemini, OpenAI, ElevenLabs) don't allow explicit
sexual content and can shut off your key for it, so this level runs only on your own computer:

1. Install Ollama (ollama.com), then `ollama pull dolphin3` (an open model that doesn't refuse adult fiction).
2. In `storymaker.env` set `ADULT_CONTENT=on`, then `docker compose restart` for the web page.
3. Pick **Explicit 18+** on the web page (or `--level explicit --mode audio`).

It always uses the local model whatever your `STORY_PROVIDER` is, narrates with a local voice (the Mac
voices from the shortcut, a plainer robotic voice inside Docker), and makes no pictures or video. Every
character must be an adult: ideas or stories that mention children, teens or under-18 ages are stopped
and nothing is made. Continuing a series keeps that series' level, so a kids' series can never turn explicit.

**Series:** every story you make is saved as the start of a series (in `~/StoryMaker/output/series/`).
`--continue` writes the next episode of your most recent one; `--series "Name"` continues a specific
one (or starts a new series with that name). The next episode reuses the same characters, art style,
character sheet, voices and reading level, and the writer is told what happened in every earlier
episode. With `--continue` the idea is optional: type what should happen ("they find a map"), or
"Surprise me". `--list-series` shows them all. If an episode only partly finishes, running it again
finishes that same episode.

**Character voices:** the writer marks who says each line, and each speaking character gets a voice
that fits (girl, old man, creature…), all different where possible. The same character keeps the
same voice in later episodes. This works with ElevenLabs, OpenAI, Gemini, the Mac voices and
edge-tts. To change which voices are used, edit the `*_VOICES` lists in `storymaker.env`; set
`VOICES=single` for the narrator only.

**Chapters:** pick a length on the web page (or `--chapters 6`) for a real multi-chapter story at
any reading level, including Explicit with your local AI. The writer plans the whole story first,
then writes each chapter as its own request, so long stories aren't cut short. Every finished
chapter is saved, so if a run stops, the next try continues from the next chapter. The narrator
announces each chapter, the reading view lists them, and the PDF starts each on a new page.
Long stories take time, especially on your own computer (a few minutes per chapter with Ollama).

**Voices:** with no voice key, StoryMaker uses **Kokoro**: smooth, natural voices that run on your
own computer for free, with a different voice for the narrator and each character. The web page in
Docker has it built in. For the Mac shortcut, `.venv/bin/pip install kokoro-onnx soundfile` (needs
Python 3.10 or newer; `brew install python` if yours is older); the voice files download the first
time. Explicit stories always use Kokoro (or the Mac voices) so the text never leaves your computer.
ElevenLabs is still the most lifelike if you add a key.

**Audiobook to keep:** every audio story also comes as `story.m4b`, a real audiobook with the
title, a cover and chapter markers (or one marker per scene), and the MP3 is tagged the same way.
On the web page use **Audiobook (M4B, chapters)** or **Audio (MP3)**; files are named after the story.
On iPhone, save it to Files and open it in Apple Books, or AirDrop it to any device.

**Character voices:** every character who speaks gets their own voice, and women always get a
woman's voice and men a man's. Characters the writer forgot to list are found from the speech tags
("Jake said"), and whether they're a man or a woman is worked out from the story's he/she. Each
voice also has a slightly different pace so they're easy to tell apart.

**How the audio is made (like a real audiobook):**
1. Every line is recorded in its speaker's voice.
2. Each line is brought to the same loudness, so dialogue never drops under the narration.
3. An AI sound designer reads each scene and picks its background (rain, a harbor, city traffic,
   crickets, a quiet bedroom…) and up to three sound effects at exact moments (a door knock, thunder,
   footsteps…). Explicit stories can also get intimate sounds such as moaning or breathing.
4. Real recordings are found for each sound (below).
5. It's mixed so the background dips gently while someone speaks. The voice never dips.
6. The voice gets a studio chain (rumble cut, softer "s" sounds, gentle compression, a little warmth
   and presence), then everything is mastered to audiobook loudness (-18 LUFS, peaks under -3 dB).
   Every line, character and chapter announcement lands within about 2 dB of the others.

**Real sound effects:** StoryMaker uses real recordings, never made-up noise:
- **Your own files first.** Put them in the `sounds` folder and name them by what they are:
  `rain-heavy.mp3`, `ocean-waves.wav`, `door-knock.m4a`, `female-moaning-1.mp3`. A file is used when
  its name matches the sound the designer asked for.
- **Then Freesound.org**, a free library of over 600,000 real recorded sounds. Make a free account,
  get an API key at freesound.org/apiv2/apply, and put it in `storymaker.env` as `FREESOUND_API_KEY=`.
  Each sound is downloaded once, kept in `sounds/library`, and reused. Only sounds that are free to
  use (CC0 or CC BY) are picked, and each story's `sound-credits.txt` lists them.
- With neither, the story simply has no background (better quiet than fake).

`AMBIENCE_VOLUME` and `EFFECTS_VOLUME` adjust the levels; `SOUNDSCAPES=off` turns sound design off.

**Character voices:** every speaker gets their own voice: women always a woman's voice, men always a
man's, and different women (or men) clearly different from each other. With the free Kokoro voices,
characters get blended voices and small pitch differences so even ten women sound distinct.
Speakers the writer forgot to list are found from the speech tags, and whether they're a man or a
woman is worked out from the story's he/she.

**Make it yours (on the make form):**
- **Words or phrases to include:** comma-separated, e.g. `lantern, salt air, "I never stopped looking for
  you"`. The writer is told which ones it hasn't used yet as it writes each scene, the last scene must
  use any still missing, and anything left over is worked into the scenes it fits best. This works at
  every level, including Explicit with your local AI.
- **Sounds you want to hear:** e.g. `rain on the window, thunder, seagulls`. The sound designer uses
  them wherever they fit.
- **Narrator's voice:** pick any of the free voices (women's and men's, American and British) and a
  pitch; press **Listen** to hear it first.

**Edit, rewrite and remake:** open any story and use:
- **Edit the text:** change any scene (or the title) yourself and save.
- **Rewrite with AI:** pick the whole story, a chapter or one scene, and say how it should change
  ("longer and more detailed", "change the ending so they stay together"). The audio is remade
  afterwards if you leave that ticked.
- **Voices:** choose the narrator and each character's voice (women's voices are listed first for
  women, men's for men), with a pitch and a **Listen** button. Only the lines of changed voices are
  recorded again.
- **Sounds:** change each scene's background and effects (and the words an effect plays on), add
  effects, or type the sounds you want and let the AI choose again.
- **Make a new version:** fills the make form with that story's idea and settings, so you can write
  a fresh one (change anything first).
- **Remake the audio:** records any changed lines, designs and fetches the sounds again, and
  remasters. Lines that didn't change are reused, so it's quick. Tick **Choose new character
  voices** or **Pick the sounds again** for a fresh take. Video stories get their sound replaced
  using the clips already made, so this never pays for new video.

**Accounts (gear icon on the web page):** everyone signs in with their own user name and password and
sees only their own stories. The owner (you; the first account, from `APP_PASSWORD`) sees everyone's
stories and manages people under **People**: add someone, set their password, allow Mature (R)
for them, or remove them (their stories become yours). **Explicit (18+) is only ever available to
the owner**, and only while **Show Mature and Explicit** is on. Accounts are kept in
`output/.storymaker.db` (passwords only as scrambled hashes). Forgot the owner password? Stop the
container, delete `output/.storymaker.db`, and start it again: the owner is recreated from
`APP_PASSWORD` (other accounts would need adding again; stories are kept).

**Reading:** stories show like a book: chapter headings, paragraphs with dialogue on its own line,
and buttons to make the text bigger or switch between night and paper.

**Picture book:** in video mode, `story-book.pdf` is made automatically from the video's own
pictures, so it costs nothing extra. In story or audio mode, add `--book` and it draws a picture per
scene (counted in the price check). Pages are US Letter landscape, ready to print.

**Subtitles:** every audio or video run also writes `story.srt` and `story.vtt`, timed to the
narration. Most players (VLC, QuickTime with the .srt next to the video, YouTube uploads) can show
them, and the web page turns them on with the CC button. This is separate from the words burned
into the video (`CAPTIONS=on`); set `CAPTIONS=off` if you'd rather only have the switchable kind.
`SUBTITLES=off` stops the files being written.

## 6. Free story writing: Groq or Ollama

Both write the story only. Voices and video still come from the settings above (edge-tts and the
slideshow are free too, so a fully free run is possible).

**Groq** (free tier, in the cloud, fast): sign up at console.groq.com, create an API key, and put
it in `storymaker.env`:

```
GROQ_API_KEY=gsk_...
STORY_PROVIDER=groq
```

The free tier has a daily limit that's far more than you'll use for stories. Stories are good, a
step below Claude.

**Ollama** (free, runs on your own computer, nothing leaves it): install from ollama.com, then

```bash
ollama pull llama3.1:8b
```

and set `STORY_PROVIDER=ollama`. The 8b model runs on any recent Mac in about a minute per story.
If the Mac has 32 GB or more, `ollama pull qwen2.5:32b` and `OLLAMA_MODEL=qwen2.5:32b` writes
noticeably better stories. Ollama must be running (it starts with the Mac once installed).

To try either for one story without changing your settings, use `--provider groq` or
`--provider ollama` (or pick it under **More options** on the web page).

## 7. The web page (on your server, with Docker)

A page you open in any browser to make stories and see everything you've made:

- Type an idea, choose who it's for, the genre, number of scenes and how much to make.
- **Check the price** shows the cost first; **Make it** starts it. Five lamps light up in turn
  (writing, voices, characters, filming, picture book). You can close the page; it keeps going.
- When it's done the video plays right there with switchable subtitles, plus buttons to download
  the video, audio, picture book and subtitles.
- **The next episode** continues any of your series.
- **Library**: every story as a cover. Filter by series, tap one to watch or read it.
- In any story: **Story (PDF)** downloads a clean reading copy (title page, then the story on Letter
  pages; works for every story, even text-only ones). **Picture book (PDF)** appears when there is one.
  **Delete this story** removes it after a second tap to confirm, taking its video, audio and book
  with it. Deleting a series' last episode removes the series.

It uses the same `storymaker.py`, settings and output folder as the shortcut, so stories made from
the shortcut show up in the library too (when they're saved in the same output folder).

**Start it** (Docker Desktop or OrbStack on the Mac mini). Put these in one folder:

```
StoryMaker/
  storymaker.py
  storymaker.env          ← copy of storymaker.env.example with your keys (make this first)
  docker-compose.yml
  web/                    ← Dockerfile, server.js, package.json, public/
  output/                 ← created automatically: your library
  music/                  ← optional
```

Open `docker-compose.yml` and change `APP_PASSWORD`, then:

```bash
cd ~/StoryMaker
docker compose up -d --build
```

Open http://localhost:8095 (or http://*mac-mini-name*.local:8095 from another device at home).
The browser asks for a user name and password: the user name is `story`, the password is
`APP_PASSWORD`.

**From anywhere with a Cloudflare Tunnel:** in the Cloudflare dashboard → Zero Trust → Networks →
Tunnels → your tunnel → **Public Hostname** → add, for example, `stories.yourdomain.com` →
service `http://localhost:8095` (or `http://storymaker:3000` if cloudflared runs in Docker on the
same network). Keep `APP_PASSWORD` set, because running a story spends money on your keys.

**Things that are different inside Docker:**

- The Mac's own voices (`say`) aren't available in the container. With no voice API key it uses
  edge-tts (free Microsoft voices) instead.
- To use Ollama from the page, Ollama runs on the Mac itself as normal. The compose file already
  points the container at it (`OLLAMA_URL=http://host.docker.internal:11434`).
- Change the page's look by editing the files in `web/public/`; just reload the browser. After
  changing `storymaker.py` or `server.js`, run `docker compose up -d --build` again.
- If you'd rather run the shortcut against the same library, set `OUTPUT_DIR` in the shortcut's
  `storymaker.env` to the `output` folder above.

**Without Docker:** install Node.js, then

```bash
cd ~/StoryMaker/web && npm install
APP_PASSWORD=change-me PYTHON=~/StoryMaker/.venv/bin/python node server.js
```

and open http://localhost:3000.

## 8. Build the shortcut

`[Brackets]` = a variable you insert. `code` = text you type.

The pictures in `shortcut-screens/` show every action. `[Brackets]` = a variable you insert;
`code` = text you type. `PY` below means
`~/StoryMaker/.venv/bin/python ~/StoryMaker/storymaker.py`, and `FLAGS` means the two variables
`[Next] [LevelFlag]` inserted one after the other with a space between.

**Part 1: new story or next episode**

1. **Choose from Menu** `New story or the next episode?`
    - `New story`
        - **Ask for Input** (Text) `What should the story be about?` → **Set Variable** `Idea`
        - **Choose from Menu** `Who is it for?`, six options, each a **Text** then **Set Variable** `LevelFlag`:
          `Young kids (3–5)` → `--level young-kids` · `Kids (6–8)` → `--level kids` ·
          `Middle grade (9–12)` → `--level middle-grade` · `Teens` → `--level teen` ·
          `Adults` → `--level adult` · `Everyone` → `--level family` · **End Menu**
        - an empty **Text** action → **Set Variable** `Next`
    - `Next episode of my last story`
        - **Ask for Input** (Text) `Anything that should happen this time?`, default `Surprise me` → **Set Variable** `Idea`
        - **Text** `--continue` → **Set Variable** `Next`
        - an empty **Text** action → **Set Variable** `LevelFlag` (the series keeps its reading level)
    - **End Menu**

**Part 2: details**

2. **Ask for Input** (Text) `Genre or mood?`, default `heartwarming adventure` → **Set Variable** `Genre`
3. **Ask for Input** (Number) `How many scenes? (3–8)`, default `5` → **Set Variable** `Scenes`
4. **Choose from Menu** `What should I make?`: `Story only` → **Text** `story`, `Story + audio` → `audio`,
   `Story + audio + video` → `video`, each → **Set Variable** `Mode` · **End Menu**

**Part 3: price check**

5. **Get Device Details** → **Device Model** → **Set Variable** `Device`
6. **If** [Device] **contains** `Mac`
    - **Run Shell Script** (zsh, Input [Idea], Pass Input **to stdin**): `PY --estimate --mode [Mode] --scenes [Scenes] FLAGS`
7. **Otherwise**
    - **Run Script Over SSH** (your server, SSH Key, Input [Idea]): the same line
8. **End If** → **Get Dictionary from** [If Result] → **Get Dictionary Value** `summary`
9. **Choose from Menu** with prompt [Dictionary Value] `— go ahead?`
    - `Make it` → (nothing; carries on)
    - `Cancel` → **Stop This Shortcut**
    - **End Menu**

**Part 4: make it**

10. **If** [Device] **contains** `Mac`
    - **Run Shell Script** (zsh, Input [Idea], to stdin):
      `PY --mode [Mode] --scenes [Scenes] --genre "[Genre]" FLAGS --yes --open`
11. **Otherwise**
    - **Run Script Over SSH** (Input [Idea]):
      `PY --mode [Mode] --scenes [Scenes] --genre "[Genre]" FLAGS --yes --background`
12. **End If** → **Get Dictionary from** [If Result] → **Set Variable** `Out`
13. **Get Dictionary Value** `queued` in [Out]
14. **If** [Dictionary Value] **has any value**
    - **Show Notification** `Working on it — you'll get a notification when it's ready.`
15. **Otherwise**
    - **Get Dictionary Value** `story` in [Out] → **Quick Look**
16. **End If**

On the Mac, the shortcut waits and `--open` plays the video when it's done. To keep using the Mac
while it works, add `--background` there too; a macOS notification tells you when it's ready.
Add `--portrait` to either line for phone-shaped 9:16 videos.

## 9. The story prompt

StoryMaker sends this to the writing AI. It's `STORY_PROMPT` near the top of `storymaker.py`; edit it
there to change the storytelling style.

> You are a master storyteller writing a story that will be read aloud by a narrator and shown as
> short cinematic video clips, one per scene. Write it as a real story with a beginning, a middle
> with rising tension, and a satisfying ending. Use vivid, sensory, spoken-word prose that sounds
> good out loud. Each scene's narration is 20–28 words (so it fits an 8-second clip). Keep names,
> looks and places consistent. For every scene also write a picture description and an 8-second
> shot description: the action, the camera move, lighting, mood and natural ambient sound. Characters
> don't speak; the narrator tells the story. No on-screen text.

The writer also returns the **cast** (each speaking character and the kind of voice they need) and,
for each scene, the narration split into **lines** by who speaks them, which is how the character
voices work. The reading level's content rules and, for a series, the earlier episodes are added to
the prompt too.

Every clip request also includes the story's fixed **visual style** and **character descriptions**,
which helps keep the characters looking the same from scene to scene.

## 10. Command-line options

| Option | Meaning |
|---|---|
| `--idea "..."` | The idea (or pipe it in on stdin, which is what the shortcut does) |
| `--mode story/audio/video` | How much to make (default `video`) |
| `--scenes N` | 1–12 scenes (default 5); per chapter when using `--chapters` |
| `--detail brief/standard/rich/epic` | How long and detailed each scene is (epic is about 2½ times standard). AI video clips keep scenes short |
| `--keywords "a, b, c"` | Words or phrases the story must include |
| `--sounds "rain, thunder"` | Sounds you want to hear |
| `--narrator VOICE` | Narrator voice, e.g. `bm_fable` or `af_bella^1.06` (`--voices-list` shows them all) |
| `--rewrite FOLDER --target scene:3` | Rewrite part of a story (instructions on stdin; `--keywords` to change the words) |
| `--remaster FOLDER` | Remake the audio (`--revoice` for new voices, `--redesign` for new sounds) |
| `--set-voices FOLDER` / `--set-sounds FOLDER` | Change voices or sounds (JSON on stdin), then `--remaster` |
| `--chapters N` | A longer story in N chapters (1–20). Planned first, then written one chapter at a time |
| `--genre "..."` | Genre or mood |
| `--level LEVEL` | `young-kids`, `kids`, `middle-grade`, `teen`, `adult` or `family` |
| `--audience "..."` | Optional extra note about the reader |
| `--continue` | Next episode of your most recent story |
| `--series "Name"` | Next episode of that series (or start a new series with that name) |
| `--list-series` | List your series and how many episodes each has |
| `--book` | Also make the picture-book PDF in story/audio mode |
| `--provider claude/gemini/openai/groq/ollama/demo` | Override the writer for one run |
| `--video omni/veo/slideshow/demo` | Override the video engine for one run (`slideshow` = pictures, cheaper) |
| `--music NAME/off` | Pick a music track by part of its file name, or no music |
| `--estimate` | Print the approximate cost and stop (nothing is spent) |
| `--yes` | Allow runs above `MAX_COST_USD` (the shortcut adds this after you confirm) |
| `--fresh` | Ignore an unfinished earlier attempt and write a brand-new story |
| `--background` | Return at once and finish in the background; notify when done |
| `--status JOB_ID` | Show where a background job is up to |
| `--portrait` | 9:16 phone-shaped video |
| `--open` | Mac: open the result when finished |
| `--export-pdf FOLDER` | Make `story.pdf`, a reading copy of a finished story (its folder name in the output folder) |
| `--delete FOLDER` | Delete a finished story and remove it from its series |

## 11. If something goes wrong

| What you see | Fix |
|---|---|
| `ffmpeg failed` or `No such file: ffmpeg` | Install ffmpeg (`brew install ffmpeg` / `apt install ffmpeg`) |
| `Missing packages` | Run `.venv/bin/pip install -r requirements.txt` |
| `..._API_KEY is missing` | Add that key to `storymaker.env` |
| `error 404` mentioning a model | The model name changed; update it in `storymaker.env` |
| `error 403` or a billing message from Google | Turn on billing for your Gemini API project |
| A scene shows a still picture instead of a clip | That clip failed or was blocked; the run continues. Check the log for the reason |
| "is over MAX_COST_USD" | Expected: confirm in the shortcut (it adds `--yes`), or raise `MAX_COST_USD` |
| I wanted a new story but got the same one | The last attempt wasn't finished, so it resumed. Add `--fresh` |
| No notification after a background run | Check `NTFY_TOPIC` matches the topic in the ntfy app; the run's log is in `~/StoryMaker/output/.jobs/` |
| "There's no earlier story to continue yet" | Make one story first, then use Next episode |
| Two characters sound the same | Add more voices of that type to the `*_VOICES` list in `storymaker.env` |
| Music is too loud or quiet | Change `MUSIC_VOLUME` (0.1 quieter, 0.3 louder) |
| Shortcut says scripts aren't allowed | Shortcuts → Settings → Advanced → Allow Running Scripts |
| "Can't reach Ollama" | Open the Ollama app (or run `ollama serve`), and `ollama pull` the model named in `OLLAMA_MODEL` |
| Ollama story comes out cut short or odd | Small models struggle with long stories; use fewer scenes or a bigger model |
| Groq `429` | You hit the free daily limit; try again tomorrow or switch writer |
| Web page keeps asking for the password | User name is `story`; the password is `APP_PASSWORD` in `docker-compose.yml` |
| Web page: "storymaker.env" is a folder | Docker made it because the file didn't exist. Delete the folder, create the file from the example, `docker compose up -d` |
| Library is empty but stories exist | The page only shows stories in its own `output` folder |

## 12. Audio Studio (voices and sound only)

`audiostudio/` is a separate app that turns a StoryMaker story, or any book (.epub, .docx, .pdf, .txt or
pasted text), into a finished audiobook. It uses natural Chatterbox voices, gets every character's gender
right, and adds soundscapes, chapters and a cover. It runs directly on the Mac (not in Docker) so it can use
the graphics chip. See `audiostudio/README.md` to set it up. In short: `cd ~/StoryMaker/audiostudio && ./install.sh && ./start.sh`,
then open http://localhost:8095's **Send to Audio Studio** button, or http://localhost:8096.

**Characters list:** every story now starts with a list like "Mira: a woman", "Jake: a man", "Lily: a girl".
Those genders are locked. Change them in *Edit the text* (the Characters box at the top) or in *Cast*. The
voices, later episodes and the Audio Studio all follow them.

## 13. Your style

Press **Style** at the top of StoryMaker to teach it your taste.
- **Writing:** tone and voice, words and phrasing to use or avoid, and passages of writing to match. The
  writer copies the voice of those passages, not their stories. Pick which stories each style is for: Kids (Young
  kids, Kids, Middle grade), Family (Everyone), Safe for work (Teens, Adults), Mature, or Explicit. Tick
  "Also use this style for the other kinds of stories" to share it. Used for a gentler story than it's made
  for, it's softened automatically, keeping the same voice, rhythm and words: Mature gets nothing graphic;
  Safe for work, Family and Kids get no swearing and nothing crude, sexual or suggestive. Family and Kids
  stories never get passages from Mature or Explicit styles.
- **Pictures, video, sounds:** the look of pictures and what never to show, camera and motion, and sounds you
  love or never want. Grown-up pictures always stay tasteful.
- **Feedback:** the Feedback button on any story saves what you liked or didn't (and, if you like, a passage to
  match) into the matching style. Every story after that uses it.
- To make one story without your style, untick **Use my style** under More options.

## 14. Cleaner writing, fresh names, more voices

- **Proofreading:** after a story is written, a copy editor goes over every scene. It fixes spelling, grammar,
  broken or jumbled sentences, and makes it clear who is speaking in every exchange. It keeps the events,
  length, style and heat. It roughly doubles the writing time; untick **Proofread every scene** under More
  options to skip it. (Setting: `PROOFREAD=on` in storymaker.env.)
- **Sentence check:** after proofreading, every scene is checked by rule (not by AI) for badly built sentences:
  very long or run-on sentences, two sentences joined by only a comma, the same word twice in a row, quotes
  that don't open and close, the same sentence twice, three sentences in a row starting with the same word, and
  likely misspellings. Small things (doubled words, stray spaces, a missing capital) are fixed directly. The rest
  go back to the writer as a numbered list, and only those sentences are rewritten, up to twice. A fix is kept only
  when it has fewer problems and keeps the length. It's part of **Proofread every scene**.
- **Clean up stories you already have:** open a story and press **Clean up the writing**. It shows how many
  sentence and spelling problems the checker finds, with examples. **Clean it up** then runs the proofreader and
  the sentence checker over every scene. The events, names, style, heat and sounds stay the same, and the audio is
  remade with only the changed lines recorded again. To do many at once, use **Clean up the writing** next to the
  Library heading. It lists every story with its count, ticks the ones with problems, and cleans them up one
  after another in the background. From Terminal: `--check-writing STORY` counts and `--cleanup STORY,STORY`
  cleans.
- **Fresh names:** every story gets its own random set of names to choose from, and the overused ones (Mira,
  Jake, Elara, Lily…) and names from your recent stories are left out. Names you write in the idea are kept.
- **More voices and accents:** 54 English voices: Kokoro's 28, plus 26 character voices made by blending two of
  them (Scarlett, smoky and sultry; Silas, gravelly; Victor, commanding; Pippa, bubbly; Edmund, grandfatherly…).
  Also French, Spanish, Italian, Brazilian, Indian, Japanese and Chinese accents, strong and light. Pick them
  under Voices on any story, or in Audio Studio's cast; the lists are grouped into voices, character voices and
  accents. New characters are also given character voices automatically.
- **Acting with the free voices:** each line is read the way the story says it's said. "She whispered" is soft
  and breathy; "he shouted" is louder and quicker; "she sobbed" is slower and lower; "he snapped" is sharp; "she
  laughed" is lighter. (Chatterbox in Audio Studio acts with its own emotions on top.)
- **Faster local writing:** on the Mac, give Ollama these two settings once, then quit and reopen Ollama:
  `launchctl setenv OLLAMA_FLASH_ATTENTION 1` and `launchctl setenv OLLAMA_KV_CACHE_TYPE q8_0`.

## 15. Sounds, pictures and video

- **Backgrounds follow the story.** Sentence by sentence, StoryMaker reads where the story is. A background starts at
  the sentence that puts you somewhere (she walks out into the rain, they reach the café, the waves on the shore),
  lasts exactly as long as the story stays there (into the next scene too, without a break, when it carries on), and
  stops when the story moves somewhere else or goes inside. Rain heard from indoors is softer. Quiet rooms
  (bedroom, kitchen table, living room) and busy places described as quiet ("the street was quiet") get no
  background. A place mentioned only in what someone says, or used as an image ("waves of relief", "a train of
  thought"), doesn't count. In **Sounds**, leave a background blank for this, type a sound to have it under the
  whole scene, or type **none** for silence.
- **Effects play once, for their moment.** An effect plays at its words and lasts only as long as that line: a knock
  is a knock (2–3 seconds), and a ringing phone rings to the end of that line, no further. Tick **every time** next
  to an effect to have it play each time those words are read.
- **Only sounds that make sense.** A background sound is used only when the scene's own words put you somewhere
  with that sound (rain, the sea, a busy street, a fire, a café). Quiet rooms, bedrooms and conversations get no
  background, and hums, room tone and "quiet" backgrounds are never used. An effect plays only when the thing
  actually happens: "she held her breath" is not breathing.
- **Love scenes in Explicit stories.** Breathing and moaning are never placed on a word like "breath". Instead,
  StoryMaker finds the love scenes themselves (several intimate sentences in a row). It lays soft breathing under
  just that passage, and adds moans at the moments the text describes, such as moaning, gasping, crying out and the
  climax. Each moan is a woman's or a man's depending on who the sentence is about. They're kept low and dip
  under the voice, so the words always come first. Your own recordings in `~/StoryMaker/sounds`, named for
  example `female-moaning-1.wav` or `heavy-breathing.wav`, are used before Freesound. Turn it off with
  `INTIMATE_SOUNDS=off`.
- **Older stories:** the same checks run whenever the audio is made, so **Remake the audio** on an older story
  removes the out-of-place sounds. To have the AI choose its sounds again under the new rules, tick **Pick the
  sounds again** under Remake the audio.
- **Sound effects land on the right words.** Every effect is checked against the scene's text. If the AI's "when"
  words aren't really in the scene, the sentence that best matches the sound is used instead (a phone ringing goes
  to the sentence about the phone). An effect that fits nowhere is left out rather than played at a random moment.
  Effects never pile on top of each other, are kept short and fade out before the scene ends. If no recording
  is found for a background, a simpler one that matches the scene is tried.
- **Pictures stay consistent.** With Draw Things, every picture in a story uses the same seed, so faces, clothes
  and the look stay the same. The shot comes first in the prompt (picture apps read the start most closely).
  Pictures are drawn one at a time, and the picture app gets two more tries if it's busy or just opening.
- **Longer scenes get more pictures.** In video mode with Draw Things, a scene gets a new picture of a later moment
  every 40 seconds (up to 4 per scene), with a slow push in or out across the whole picture and a soft fade
  between them. Settings: `MORE_PICTURES=on`, `SECONDS_PER_PICTURE=40`.
- **Pictures for Explicit stories.** Explicit stories can have a cover, a picture book and a moving-picture video,
  all drawn by Draw Things on your Mac; nothing leaves the computer. They are always tasteful: everyone is an
  adult and fully clothed. Anything sexual in a picture prompt is taken out before it reaches Draw Things, which
  also gets a strong "never show" list. To turn it on:
  1. Open Draw Things, then Settings → API Server: turn it on, HTTP, port 7860 (after that Audio Studio keeps it on).
  2. In `~/StoryMaker/storymaker.env`: `LOCAL_IMAGE_URL=http://host.docker.internal:7860`.
     For SDXL Turbo or Lightning models, also `LOCAL_IMAGE_STEPS=4` and `LOCAL_IMAGE_CFG=1.5`.
  3. `cd ~/StoryMaker && docker compose up -d`. The Video choice and the picture book then work for Explicit.

## 16. Better writing, real illustrations

- **No more broken sentences.** The local writer's settings no longer punish it for reusing everyday words. That
  punishment is what made it drop "the", "to" and "was" and write fragments like "she wanted love be happy stir
  trouble". Any sentence that still comes out like that is caught (it has lost all its small words, or trails off
  in "…" pieces) and sent back to be rewritten as a complete idea.
- **One point of view.** The first scene decides first person ("I") or third person (or your choice under More
  options), and every later scene is held to it. A scene that switches is written again, and a narration line
  that slips into "I" in a third-person story is fixed.
- **No repeats, no padding.** A scene that repeats an earlier one is thrown away and written again with "what
  happens next". The writer is told to keep things moving and never to pad. A word used five times or more in one
  scene is flagged and varied. Part 2 of a series picks up where part 1 ended instead of retelling it.
- **Rewrite with AI** keeps the same events, people, order and outcome, in new sentences. Each try is checked: if
  it copied too much of the old wording, or lost the characters (went off topic), it's written again.
- **Videos show real illustrations.** Before drawing, the writer reads each finished scene and describes one
  illustration of its most telling moment: which characters, exactly what each is doing (mid-action), their
  expressions, where, the light. Draw Things draws that in the story's look (a storybook style unless you set one
  in Style → Pictures), with the same characters throughout. Long scenes get several pictures of different
  moments, picking sentences where someone is doing something.
- **No more text on the pictures.** The whole scene's text used to be drawn over the picture and ran off the
  screen. Now subtitles appear a line at a time, as they're spoken, always inside the frame (`CAPTIONS=on`;
  `off` leaves them only as the switchable subtitle track).
- **Draw Things must be on.** If it isn't answering, the story stops with a message instead of filling the video
  with title cards. Open it and press **Make it again**: nothing already made is redone.
- **Older stories:** in **Remake the audio**, tick **Draw new pictures** to replace a video's (and picture book's)
  pictures with new illustrations.

## 17. Ready checks (and Draw Things opens by itself)

Before a story starts, StoryMaker checks it has everything it needs, and the price check shows the list:
- **Writer:** Groq answers with your key, or Ollama is running and has your model (Explicit). If Ollama is closed,
  it's opened.
- **Voices:** Kokoro's voice files are there (or will download the first time).
- **Pictures** (video or picture book): Draw Things answers. **If it's closed, StoryMaker opens it and waits for
  it.** With no picture app set up at all, it tells you rather than making a video of title cards.
- **Sounds:** Freesound key or your own sounds folder (a note only, it never stops a story).
- **Disk space:** at least 3 GB free.

Anything marked ✕ stops the story before anything is spent or written, with what to do. The same check runs
when a cut-off story is carried on, and Draw Things is opened again in the middle of a story if it was closed.

How the opening works: StoryMaker runs inside Docker and can't open Mac apps, so it asks Audio Studio (which
runs on the Mac) to. The two share a private key in StoryMaker's library folder, so nothing else can ask. Audio
Studio must be running (it starts by itself a few minutes after the Mac starts).

## 18. Draw Things' API server stays on

Draw Things forgets its **Settings → API Server** switch every time the app closes (a restart, a crash, a Mac
update, quitting it). So Audio Studio watches it: every minute it checks that the server answers on port 7860,
and if it doesn't, it opens Draw Things in the background, goes to Settings → All → API Server and turns the
switch on, the way you would.

- **Allow it once:** the first time, the Mac asks whether "Python" may control the computer. Say yes, or turn it
  on in System Settings → Privacy & Security → Accessibility. (If you start the studio by hand in a Terminal
  window, it's Terminal that needs the permission.) After a Python update the Mac may ask again.
- **Check it:** Audio Studio → Settings shows "Draw Things: On / Off", with a **Turn it on now** button.
- **It only acts when the server is off.** It never touches the switch when the server is answering, and after
  a failed attempt it waits 10 minutes before trying again.
- **Quit Draw Things on purpose?** It's opened again within a minute. To stop that, put `DRAW_THINGS_REOPEN=off`
  in `audiostudio/studio.env` (the server is still switched on whenever Draw Things is open). To turn all of
  this off: `DRAW_THINGS_ALWAYS_ON=off`.
- If a Draw Things update moves the setting somewhere else, the studio log says it couldn't find the switch;
  turn it on by hand until StoryMaker is updated for the new layout.

## 19. Stop anything, and check before it starts

- **Stop** is under every progress bar: a story being made, a remake, a clean-up, a rewrite, and the jobs from
  other devices listed at the top. It asks first, then stops within a few seconds.
- **A stopped story isn't lost.** What it wrote is kept in *Unfinished stories*: press **Recover** to carry on
  from where it stopped, read what's written, or delete it. If it's stopped before anything was written,
  nothing is kept or spent.
- **A stopped remake** leaves the story marked "remake the audio to match": its sound may be half-made until you
  remake it. A stopped clean-up keeps the stories it finished.
- **Remake the audio** and **Clean up the writing** show what they're about to do (voices, sounds, pictures,
  remaking the audio afterwards) with **Start** and **Change**, and nothing starts until you press Start. That
  includes "Remake the audio to match": it opens the choices instead of starting by itself.

## 20. Free writers' limits (Groq)

Groq's free plan allows about 8,000 tokens a minute (roughly three scenes) and a daily total. StoryMaker no
longer stops when it's turned away: it waits as long as the writer says (up to 15 minutes) and carries on, and
with Groq it paces itself so it rarely gets turned away at all. Long stories take longer on the free plan.

If the day's allowance runs out partway, the rest of the story is written by Ollama on the Mac, so it still
finishes (`GROQ_FALLBACK=off` in storymaker.env stops instead; the story waits in Unfinished stories for
**Recover**). The same waiting applies to Claude, Gemini and OpenAI when they're busy.

Groq also counts the longest reply StoryMaker *allows* against that 8,000, so each request is sized to fit
(`GROQ_TPM`, raise it if you upgrade), and gpt-oss thinks as hard as each job needs (`GROQ_REASONING=auto`): hard when planning the plot and chapters,
a fair amount for each scene, briefly for spelling and sentence fixes. If an answer is ever cut off because it
thought too long, that request is asked again with a little less thinking.

## 21. Better, more realistic pictures

Each story picks how its pictures look, under **Pictures** on the make form (when it makes a video or a picture
book): **Storybook** (illustrated), **Realistic** (like photographs), **Cinematic** (like stills from a film) or
**3D animated** (like a 3D animated film).
**Remake → Draw new pictures** can redraw an existing story in another look (with a new cover), and Audio Studio
has the same choice for its videos.

- **Use a realistic model for realistic pictures.** SDXL Turbo is a fast sketch model. In Draw Things, open the
  model list, download a realistic model (Juggernaut XL or RealVisXL work well on a 16 GB Mac mini) and select
  it. The checks before a story starts say which model is loaded and warn when it's a sketch model.
- **Settings follow the model.** StoryMaker asks Draw Things which model is loaded and uses steps, guidance and
  size that suit it (Turbo: 6 steps, low guidance; full SDXL: 30 steps at 1344×768). Set
  `LOCAL_IMAGE_STEPS` / `LOCAL_IMAGE_CFG` to numbers to choose yourself, or `LOCAL_IMAGE_MODEL` to the model's
  file name to always use one model for StoryMaker.
- **Fights and arguments look like fights and arguments.** Scenes with punches, shouting, pointing, struggling
  or chases are drawn as the clash itself, with intense action and emotion. Grown-up stories' pictures stay
  tasteful (fully clothed, nothing sexual, no gore), but action words like *pinned*, *slammed against the wall*,
  *sweating* or *panting* are no longer cut out of them.
- **Only who's in the moment is drawn** (the characters named in that scene, or the two leads on the cover),
  instead of the whole cast in every picture.
- **Draw Things keeps a copy of every picture** in its own project history. StoryMaker already saves every
  picture in the story's folder, so you can delete old projects in Draw Things' **Projects** tab whenever it
  gets big.

## 22. The NAS: free up space, bring stories back

Stories (and Audio Studio books) can be moved to the TerraMaster NAS and brought back whenever you want to
watch, remake or record them again.

**Set it up once (on the Mac):**
1. On the NAS (TOS): make a shared folder, e.g. `StoryMaker`, with SMB turned on and a user who can read and write.
2. On the Mac: Finder → **Go → Connect to Server** → `smb://<NAS address>/StoryMaker`, sign in, and tick
   **Remember this password in my keychain**.
3. In `~/StoryMaker/audiostudio/studio.env`:
   ```
   ARCHIVE_DIR=/Volumes/StoryMaker
   NAS_URL=smb://<NAS address>/StoryMaker
   ```
   then run the update (or restart Audio Studio). If the Mac asks whether Python may use files on a network
   volume, say **Allow**.

**Using it:**
- StoryMaker → Library → **Move to NAS**: pick stories (sizes shown), optionally *keep a copy here too* (backup
  only). Each story is copied, every file is checked on the NAS, and only then is it removed from the Mac. If
  anything doesn't match, nothing is removed.
- **On the NAS** (under the library) lists what's there: **Bring back** copies it home again (the NAS keeps its
  copy as the backup). Then remake it, or send it to Audio Studio as usual.
- Audio Studio: **Move to NAS** beside *Your audiobooks*, and **On the NAS** below them. To bring a StoryMaker
  story from the NAS straight into the studio: *Bring in a book* → **The NAS**.
- Audio Studio does the copying (it runs on the Mac, where the share is connected), so it needs to be running.
  If the NAS is off, the lists say so; nothing is lost.

**Moving the apps to the server later:** connect the same NAS share on the server and set `ARCHIVE_DIR` to
where it appears there (`NAS_URL` is only for the Mac). Everything on the NAS shows up on the server's apps and
can be brought back there; whose story it is travels with it.

## 23. StoryMaker on the server (the OptiPlex), the Mac as the workshop

StoryMaker can run on the Linux server while the Mac keeps what needs the Mac: **Draw Things**, **Ollama** (the
better writer) and **Audio Studio** with Chatterbox. The story library lives on the **NAS**, where both reach it.

```
 OptiPlex (Linux)                      Mac mini                           NAS
 StoryMaker in Docker  ── asks ──▶  Draw Things :7860                ┌─ library/   (every story)
   database, work folders            Ollama :11434 (first choice)     ├─ sounds/, music/
   backup Ollama (Mac off)           Audio Studio :8096 (+ Chatterbox) └─ StoryMaker Archive/
        └──────────── both read and write the library on the NAS ───────┘
```

**Move it (from the Mac):**
1. The NAS share is connected on the Mac (section 22), and you can `ssh` to the server.
2. StoryMaker.zip (this version) is in Downloads, and the update has been run on the Mac.
3. Run:
   ```bash
   bash ~/StoryMaker/mac/move-to-server.sh
   ```
   It asks for the server's address and your user there, then:
   - pauses StoryMaker on the Mac, and **copies** every story to `NAS/library` (each file checked; a second run
     skips what's already there). Your sounds and music go too.
   - sends your settings, accounts and unfinished stories to the server over SSH (not via the NAS), and runs
     the server's setup there. It asks for the NAS's address and login, and whether to install a backup Ollama.
   - lets the server use the Mac's Ollama (it listens on the network from now on) and asks you to set Draw
     Things' **API Server → IP** to the one that isn't "localhost only".
   - points Audio Studio at the server and the NAS library, and switches StoryMaker on the Mac off for good.
4. Open StoryMaker at `http://<server>:8095` and update the Apple Shortcut and bookmarks to it.
5. Once a few stories open and play from the server, free up the Mac's space with the `rm -rf` line the script
   prints. Nothing on the Mac is deleted before that.

**How it behaves afterwards:**
- **Writing:** the Mac's Ollama first (faster, better stories). If the Mac is off, asleep or its Ollama isn't
  answering, the server's backup Ollama writes instead (slower), even partway through a story. The checks before
  a story say which one it's using.
- **Pictures:** Draw Things on the Mac, opened and switched on by Audio Studio when it's needed.
- **If the NAS is off,** StoryMaker won't start a story (the checks say so) rather than saving it in the wrong place.
- **Updating:** run `bash ~/StoryMaker/update.sh` on the Mac (Audio Studio) **and** on the server (StoryMaker) with
  the same zip (copy it to the server's Downloads, or `~/storymaker-move/`). On the Mac the update no longer
  starts StoryMaker.
- **The server's settings** are in `~/StoryMaker/storymaker.env` there: `OLLAMA_URL`, `LOCAL_IMAGE_URL` and
  `STUDIO_HELPER_URL` point at the Mac, and `OLLAMA_FALLBACK_URL` at the server's own Ollama.
- **Security:** Ollama and Draw Things now listen on the network with no password. Fine on a home network and
  Tailscale. Don't forward their ports on the router.

## 24. Archive tabs, and Pause / Resume

**Archive tab (both apps).** Stories and books moved to the NAS leave the main shelf and show only in **Archive**:
a plain list (title, size, date moved, level) with **Bring back** on each. Move things there with **Move to NAS**
(several at once), from a story's own buttons (**Move to archive**), or at the bottom of a studio book's page.

**Pause.** While a story is being made (StoryMaker) or a book recorded (Audio Studio), **Pause** stops it and keeps
everything done so far, so another story or book can go first. Resume carries on from where it stopped:
- StoryMaker: the story waits in *Unfinished stories* marked **Paused**, with **Resume**.
- Audio Studio: the book shows **Paused** with **Resume** on its page and in the library. Recording pauses at once
  (with Chatterbox the line being read is dropped and read again on Resume); other steps finish the step they're
  on first. A paused book still resumes after the studio or the Mac restarts.

## 25. Series: the bible, memory and Next episode

- **Next episode** (on every story) opens the make form set to continue that series. Say what should happen, or
  leave it blank to be surprised.
- **Series bible** (on every story): the characters (name, kind, age, looks, what they usually wear, voice), the look
  of every picture, the picture style and the narrator. Every new episode uses exactly these: the writer gets the
  descriptions word for word, the pictures use them, and the voices follow them. Edit it any time.
- **Portraits:** *Draw the portraits* draws each character from the bible's words, with a seed of its own, so the
  reference looks the same every time (until you change the words). It's how you check a character looks right
  before the next episode.
- **What the writer remembers:** after each episode the writer notes what happened, where things stand (injuries,
  secrets, who has what) and the open threads. The next episode is written from that, and you can edit those
  notes in the bible to steer it.
- **Epic action** picture style: fights and battles frozen at the decisive moment, dramatic light, dust and sparks.
- A series keeps its own picture style from episode to episode; pick one on the form only to change it for one
  episode.


## 26. Listening: read along, pick up anywhere, offline, podcast feed, bedtime

- **Read along:** under a story's player, *Read along* shows the words with the line being read lit up. Tap any
  line to jump there. It remembers whether you like it open.
- **Chapters:** the *Chapter* list jumps straight to a chapter (older stories get theirs from the audiobook).
- **Speed** (0.8× to 1.5×) and a **sleep timer** (10 minutes to an hour, or the end of the chapter). The sound fades
  out over the last few seconds.
- **Pick up where you stopped:** the place is saved to your account every few seconds, so a story stopped on the Mac
  carries on from there on your phone. The library shows a thin bar for how far you got.
- **Offline:** *Save for offline* keeps a story on that phone or iPad so it plays with no connection (on a plane, in
  the car). Add StoryMaker to the Home Screen (Share → Add to Home Screen) and it opens like an app. This needs the
  secure https address. On the server, run `sudo tailscale serve --bg 8095` once, then use
  `https://<server-name>.<your-tailnet>.ts.net`. The app is always fetched fresh while you're online, so updates show
  up right away; the saved copy is only used offline.
- **Podcast feed:** *Podcast feed* in the Library gives you a private address. Add it in Apple Podcasts (Library → ⋯
  → Follow a Show by URL) and every story with audio arrives as an episode, with chapters and its cover. Anyone with
  the address can listen, so *New address* switches the old one off. Explicit stories stay out unless you tick them in.
  Apps that fetch feeds on their own servers can't reach a private address. If the feed address shows the wrong name,
  set `PUBLIC_URL=https://<server-name>.<your-tailnet>.ts.net` in storymaker.env.
- **Bedtime story** (a tick box on the form): calm and safe from start to finish, slowing down towards a cosy ending,
  with quieter sounds and music and a slightly slower narrator. It's marked *Bedtime* in the library, and its player
  opens dimmed with a 20-minute sleep timer set.

## 27. Overnight list, phone notifications, status page

- **Make it tonight:** after checking a story's price, *Make it tonight* puts it on tonight's list instead of starting
  it. The list is made one story after another during the night hours (11pm to 6:30am; change them with QUEUE_START
  and QUEUE_END in storymaker.env), while the Mac's writer and Draw Things are free. The list is in the Library
  under **Tonight**: move stories up or down, remove them, or *Start now*. If one can't start (say the Mac is asleep),
  it's tried again half an hour later, up to three times.
- **Time zone:** the update adds your time zone (TZ=) to storymaker.env so "tonight" is your night. On the server,
  check it's there: `grep ^TZ ~/StoryMaker/storymaker.env`.
- **Phone notifications:** in Settings (the gear), *Turn on phone notifications*, install the free **ntfy** app and
  subscribe to the topic shown. Then press *Send a test*. You get a message when a story is ready (tap it to open
  the story) or stops with a problem. Audio Studio uses the same topic for finished audiobooks. The topic is a
  private channel, so messages only ever give a story's title, and Explicit stories don't show even that.
- **Status page** (Status at the top): what's being made now, tonight's list, and a live check of everything
  StoryMaker uses (the NAS and free space, the server disk, the writers, the backup Ollama, Draw Things, Audio Studio
  and what it's recording), plus what finished lately.
- **On the lock screen:** a playing story shows its title, cover and skip and chapter buttons on the lock screen
  and in Control Center. *Listen only* plays a video story as sound, so it keeps going with the screen off.
