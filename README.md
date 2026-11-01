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

**Music:** make the folder `~/StoryMaker/music` and drop in MP3 or M4A tracks you have the right to
use. The YouTube Audio Library and Pixabay Music have free ones. StoryMaker picks a track whose file
name contains a word from the genre (so `adventure-theme.mp3` for "heartwarming adventure"),
otherwise a random one. It loops and fades the track and lowers it about 13 dB whenever the narrator
speaks. Use `--music piano` to pick a track by name, or `--music off` for none. With no files in the
folder, there's simply no music.

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

Keep Draw Things open while stories are made. If the web page can't reach it, check that Draw Things'
API server allows connections from other apps on this Mac. Real AI video (moving characters) doesn't
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
  writer copies the voice of those passages, not their stories. Pick which stories each style is for: Kids & family,
  Teens & adults, or Grown-up (Mature & Explicit). Tick "Also use this style for the other kinds of stories"
  to share it: used for a gentler story than it's made for, it's softened automatically (same voice, rhythm
  and words, but no swearing and nothing crude, sexual or suggestive), and kids' stories never get grown-up
  passages.
- **Pictures, video, sounds:** the look of pictures and what never to show, camera and motion, and sounds you
  love or never want. Grown-up pictures always stay tasteful.
- **Feedback:** the Feedback button on any story saves what you liked or didn't (and, if you like, a passage to
  match) into the matching style. Every story after that uses it.
- To make one story without your style, untick **Use my style** under More options.
