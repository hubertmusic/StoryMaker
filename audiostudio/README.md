# Audio Studio

A separate app just for the sound. Feed it a StoryMaker story or any book, and it makes a finished, mastered
audiobook with a distinct, natural voice for every character, soundscapes and sound effects, chapters and a
cover.

It runs directly on the Mac, like Ollama, not in Docker. That lets the voices use the Mac's graphics chip.
You sign in with your StoryMaker account, and your grown-up settings carry over.

## How it gets the voices right

1. **Characters list.** Every StoryMaker story now starts with a Characters list, for example "Mira: a woman",
   "Jake: a man", "Lily: a girl". These are locked. Change them under *Edit the text* or *Cast* in StoryMaker,
   and both apps follow. A book file or pasted text that starts with a list like this is locked the same way:

   ```
   Characters
   Mira: a woman
   Jake: an older man
   Lily: a girl
   Rex: a creature
   ```

2. **Casting director.** An AI reads the book and labels every quote with who says it and how they say it
   (warm, tense, whisper and so on). It also lists anyone new, with their gender and age. Locked characters
   are never changed.
3. **The text has the last word.**
   - A quote tagged "Sam said" is Sam's.
   - "She said" can't go to a man, and "he said" can't go to a woman.
   - "I said" belongs to the narrator.
   - A character the book calls "she" is a woman or a girl, whatever the AI guessed.

   Any line that still doesn't agree with the text is marked *to check* in the Script step.
4. **You check it.** In the Cast step, each part shows Woman/Man, Child/Teen/Adult/Elder, a voice you can
   listen to, and a Lock box. In the Script step, you can change who says any line, or how they say it, and
   listen to that one line.

## The voices

- **Chatterbox** (free, by Resemble AI) gives natural, expressive reading, with emotion. Recording takes
  about as long as the book itself, so a 10-hour book takes roughly 10 hours. It runs in the background,
  saves every line as it goes, and carries on where it stopped if you stop it or restart the Mac.
- **Kokoro** gives fast drafts: clear and smooth, with less acting. It takes a few minutes per hour of book.

Chatterbox learns each voice from a short sample. The studio makes those samples from the Kokoro voices, so
every Kokoro voice works in both engines. You can also add a 10–30 second clip of your own (Voice library,
then Add your own voice). Only use a voice you have the right to use.

Changing a line, a voice or a sound and recording again only redoes what changed.

## What you get

Step 5, **Listen**, has these downloads:
- **Download everything:** one zip with the audiobook (M4B), the MP3, every chapter as its own MP3, the book
  as a PDF (with the Characters list), the cover and the sound credits.
- **One at a time:** the M4B, the MP3, the book as a PDF, the sound credits, and each chapter. Each chapter
  also has a Download link in step 4.
- **Send to StoryMaker** (for books that came from StoryMaker): the story in your StoryMaker library then
  plays and downloads this audiobook, with matching subtitles. StoryMaker's earlier audio is kept in a backup
  folder inside the story. A video keeps its own soundtrack. Remaking the audio in StoryMaker later replaces
  it again.

## Set it up (once)

StoryMaker must already be running. Then, in Terminal:

```bash
cd ~/StoryMaker/audiostudio
./install.sh          # Python 3.13, ffmpeg, the studio and Chatterbox (a few minutes)
./start.sh            # then open http://localhost:8096
```

To have it start by itself whenever the Mac starts, run `./autostart.sh`. To stop that, run `./autostart.sh off`.

The first Chatterbox recording downloads its voice model, which is a few GB, one time only.

## Use it from anywhere (Tailscale)

```bash
tailscale serve --bg --https=8443 http://localhost:8096
```

Then open `https://<your-mac>.<your-tailnet>.ts.net:8443`. The **Send to Audio Studio** button in StoryMaker
opens it for you. When StoryMaker is open at the same address, you're signed in to the studio already.

## Settings (`studio.env`)

| Setting | What it does |
|---|---|
| `STUDIO_ENGINE` | `chatterbox` or `kokoro` for new books |
| `CHATTERBOX_MODEL` | `standard` (best acting, uses the emotions) or `turbo` (faster) |
| `STORYMAKER_URL` | where StoryMaker runs (default `http://localhost:8095`) |
| `STUDIO_DIR` | where books and recordings are kept (default `~/StoryMaker/studio`) |

The writer used for casting, the Freesound key, and the sound and voice-polish settings all come from
`~/StoryMaker/storymaker.env`.

- **Explicit books** are cast by your local Ollama model only. Nothing leaves the Mac.
- **Groq's free plan** has a per-minute limit. On a long book the studio waits and carries on by itself, so
  casting a whole novel can take half an hour.

## If something goes wrong

- **"StoryMaker isn't answering"**: start StoryMaker first (`docker compose up -d` in `~/StoryMaker`).
- **Chatterbox greyed out**: run `./install.sh` again and look for the Chatterbox step's message.
- **The log**: `~/StoryMaker/studio/studio.log` if it's set to start by itself; otherwise it's the Terminal window.
- **Memory (16 GB Mac)**: Chatterbox needs about 3–4 GB while recording. If Ollama has a big model loaded at
  the same time, things slow down. Ollama unloads its model after 30 seconds of quiet.
