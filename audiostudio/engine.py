"""
Audio Studio engine: turns a book or a StoryMaker story into a finished, mastered audiobook.

  import  → chapters and sections (from a StoryMaker story, or .txt / .md / .docx / .epub / .pdf / pasted text)
  script  → every section split into narration and dialogue lines (exact text, nothing rewritten)
  cast    → an AI casting director says who speaks every quote, their gender and age, and the emotion.
            Characters you've tagged (in StoryMaker or here) are LOCKED and never changed.
  sounds  → the AI sound designer picks each section's background and effects (shared with StoryMaker)
  record  → Chatterbox (natural, emotional; your Mac's graphics chip) or Kokoro (fast drafts),
            line by line, every line saved, so long books resume where they stopped
  mix     → per chapter: level-matched lines, studio voice polish, soundscapes ducked under the voice,
            mastered to -18 LUFS; then the whole book as M4B (chapters, cover) and MP3.

The heavy lifting for sound (Freesound, mixing, mastering, Kokoro) is shared with storymaker.py.
"""

import hashlib
import html
import html.parser
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# the StoryMaker program: normally right next to this folder (~/StoryMaker/storymaker.py)
for _where in (HERE.parent, Path.home() / "StoryMaker", HERE.parent / "storymaker", HERE):
    if (_where / "storymaker.py").exists():
        sys.path.insert(0, str(_where))
        break
else:
    raise SystemExit(f"Audio Studio needs StoryMaker's storymaker.py next to its folder ({HERE.parent}/storymaker.py) "
                     "and can't find it. Copy it there from the StoryMaker zip, then start the studio again.")
import storymaker as sm  # noqa: E402

STUDIO_VERSION = "1.7 (Oct 30)"          # shown on the page and in the log, so you can tell which one is running

# ───────────────────────────── settings ─────────────────────────────

STUDIO_DEFAULTS = {
    "STUDIO_DIR": "~/StoryMaker/studio",          # projects, recorded lines and finished books
    "STUDIO_PORT": "8096",
    "STORYMAKER_URL": "http://localhost:8095",    # sign-in and stories come from StoryMaker
    "STUDIO_ENGINE": "chatterbox",                # chatterbox | kokoro (default for new projects)
    "CHATTERBOX_MODEL": "standard",               # standard (emotion control) | turbo (faster)
    "CHATTERBOX_DEVICE": "auto",                  # auto | mps | cpu
    "CHATTERBOX_RECYCLE_GB": "3",                 # start Chatterbox afresh when it holds this much graphics memory…
    "CHATTERBOX_RECYCLE_LINES": "15",             # …or after this many lines, whichever comes first
    "STUDIO_MAX_LINE_CHARS": "260",               # longer lines are split at sentences for Chatterbox
    "STORYMAKER_PUBLIC_URL": "",                  # the StoryMaker link on the page (blank: same address, port 8095)
    "STORYMAKER_OUTPUT": "",                      # StoryMaker's library folder (blank: the one in storymaker.env)
}


def load_config():
    cfg = sm.load_settings()                      # storymaker.env: keys, Freesound, Ollama, sounds folder…
    for k, v in STUDIO_DEFAULTS.items():
        cfg.setdefault(k, v)
    env = HERE / "studio.env"
    if env.is_file():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = re.split(r"\s+#", v, maxsplit=1)[0].strip().strip('"').strip("'")
    for k in list(cfg):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    # the studio runs on the Mac itself, not in Docker: Docker's name for the Mac means "this computer" here
    for k in ("OLLAMA_URL", "LOCAL_IMAGE_URL"):
        if cfg.get(k):
            cfg[k] = cfg[k].replace("host.docker.internal", "localhost")
    for k in ("SOUNDS_DIR", "KOKORO_DIR", "MUSIC_DIR"):
        if str(cfg.get(k, "")).startswith("/data/"):            # a Docker path copied into the env file
            cfg[k] = str(Path.home() / "StoryMaker" / cfg[k].split("/data/", 1)[1])
    lib = cfg.get("STORYMAKER_OUTPUT") or cfg.get("OUTPUT_DIR") or "~/StoryMaker/output"
    if lib.startswith("/data/"):                                # Docker's name for ~/StoryMaker/output
        lib = str(Path.home() / "StoryMaker" / lib.split("/data/", 1)[1])
    cfg["SM_LIBRARY"] = str(Path(os.path.expanduser(lib)))
    cfg["OUTPUT_DIR"] = str(Path(os.path.expanduser(cfg["STUDIO_DIR"])) / "work")
    return cfg


def studio_dir(cfg):
    d = Path(os.path.expanduser(cfg["STUDIO_DIR"]))
    (d / "projects").mkdir(parents=True, exist_ok=True)
    (d / "voices").mkdir(parents=True, exist_ok=True)
    return d


def log(msg):
    print(f"[studio {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ───────────────────────────── projects ─────────────────────────────

LEVELS = ["young-kids", "kids", "middle-grade", "teen", "adult", "family", "mature", "explicit"]
EMOTIONS = ["neutral", "warm", "happy", "excited", "playful", "tender", "sad", "fearful", "tense", "angry",
            "whisper", "seductive", "breathless"]


def project_dir(cfg, pid):
    if not re.fullmatch(r"[a-z0-9-]{6,80}", pid or ""):
        raise ValueError("Unknown project.")
    d = studio_dir(cfg) / "projects" / pid
    if not (d / "project.json").exists():
        raise ValueError("Unknown project.")
    return d


def load_project(cfg, pid):
    d = project_dir(cfg, pid)
    p = json.loads((d / "project.json").read_text())
    s = json.loads((d / "script.json").read_text()) if (d / "script.json").exists() else {"sections": {}}
    for ch in p.get("chapters", []):                # "Chapter 2: Chapter 2" from earlier StoryMaker imports
        m = re.fullmatch(r"(Chapter (\w+)): Chapter \2", ch.get("title", ""))
        if m:
            ch["title"] = m.group(1)
    return d, p, s


_save_lock = threading.Lock()


def save_project(d, p, s=None):
    with _save_lock:
        p["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = d / "project.json.tmp"
        tmp.write_text(json.dumps(p, indent=1))
        tmp.replace(d / "project.json")
        if s is not None:
            tmp = d / "script.json.tmp"
            tmp.write_text(json.dumps(s, indent=1))
            tmp.replace(d / "script.json")


def list_projects(cfg):
    out = []
    for f in sorted((studio_dir(cfg) / "projects").glob("*/project.json")):
        try:
            p = json.loads(f.read_text())
            st = read_status(f.parent)
            out.append({k: p.get(k) for k in ("id", "title", "owner", "level", "created", "updated", "engine")} |
                       {"chapters": len(p.get("chapters", [])), "words": p.get("words", 0),
                        "state": st.get("state", "new"), "has_book": (f.parent / "out" / "story.m4b").exists(),
                        "has_cover": (f.parent / "out" / "cover.jpg").exists(), "cast_done": p.get("cast_done", False)})
        except Exception:
            continue
    return sorted(out, key=lambda x: x.get("updated") or "", reverse=True)


def new_project(cfg, owner, title, level, chapters, source, cast=None):
    """chapters: [{"title": str, "sections": [{"text": str, "sound": {...}|None}]}]"""
    if level not in LEVELS:
        level = "family"
    slug = sm.slugify(title)[:40] or "book"
    pid = f"{slug}-{hashlib.sha1(f'{title}{time.time()}'.encode()).hexdigest()[:6]}"
    d = studio_dir(cfg) / "projects" / pid
    d.mkdir(parents=True)
    words = 0
    for ch in chapters:
        ch.pop("front_title", None)
    for ci, ch in enumerate(chapters):
        for si, sec in enumerate(ch["sections"]):
            sec["id"] = f"c{ci + 1:03d}s{si + 1:03d}"
            sec["text"] = sm.normalize_quotes(sec["text"].strip())
            words += len(sec["text"].split())
    p = {"id": pid, "title": title.strip()[:120] or "Untitled", "owner": owner, "level": level, "source": source,
         "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "engine": cfg["STUDIO_ENGINE"], "narrator": "",
         "cast": cast or [], "chapters": chapters, "words": words, "cast_done": False, "sounds_done": False}
    if level == "explicit":
        sm.check_adult_only(" ".join(sec["text"] for ch in chapters for sec in ch["sections"]))
    s = {"sections": {sec["id"]: segment_section(sec["text"]) for ch in chapters for sec in ch["sections"]}}
    save_project(d, p, s)
    return pid


def delete_project(cfg, pid):
    shutil.rmtree(project_dir(cfg, pid))


# ───────────────────────────── import ─────────────────────────────

CHAPTER_LINE = re.compile(
    r"^\s*(?:#{1,3}\s*)?((?:chapter|part|book|prologue|epilogue|interlude)\b[^\n]{0,80})\s*$", re.I)
MD_HEAD = re.compile(r"^\s*#{1,2}\s+(\S.{0,80})$")
SCENE_BREAK = re.compile(r"^\s*(?:\*\s*\*\s*\*|\*{3,}|#{3,}|-{3,}|—{2,}|~{3,}|•\s*•\s*•)\s*$")


def _clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sections(text, target_words=900):
    """Scene breaks (*** / ### / ---) split sections; otherwise ~900 words at paragraph boundaries."""
    parts, cur = [], []
    for para in re.split(r"\n\s*\n", text):
        if SCENE_BREAK.match(para):
            if cur:
                parts.append("\n\n".join(cur))
            cur = []
            continue
        cur.append(para.strip())
        if len(" ".join(cur).split()) >= target_words * 1.4:
            parts.append("\n\n".join(cur))
            cur = []
    if cur:
        parts.append("\n\n".join(cur))
    out = []
    for part in parts:                      # long scenes: split again near the target size
        paras, buf, first = part.split("\n\n"), [], len(out)
        for para in paras:
            buf.append(para)
            if len(" ".join(buf).split()) >= target_words:
                out.append("\n\n".join(buf))
                buf = []
        if buf:
            if len(out) > first and len(" ".join(buf).split()) < target_words * 0.3:
                out[-1] += "\n\n" + "\n\n".join(buf)
            else:
                out.append("\n\n".join(buf))
    return [{"text": t, "sound": None} for t in out if t.strip()]


def split_chapters(text):
    """Chapters from 'Chapter 1', 'Part Two', '# Heading' lines; otherwise parts of ~3,000 words."""
    text = _clean(text)
    lines, chapters, title, buf, first_head = text.split("\n"), [], None, [], ""
    for line in lines:
        m = (CHAPTER_LINE.match(line) or MD_HEAD.match(line)) if len(line) < 90 else None
        if m:
            if buf and " ".join(buf).strip():
                chapters.append((title, "\n".join(buf)))
            elif title and not chapters and not first_head:
                first_head = title                 # a heading with nothing under it: the book's title
            title, buf = m.group(1).strip().strip("#").strip(), []
        else:
            buf.append(line)
    if buf and " ".join(buf).strip():
        chapters.append((title, "\n".join(buf)))
    front = ""
    if len(chapters) > 1 and chapters[0][0] is None and len(chapters[0][1].split()) < 60:
        front = chapters.pop(0)[1].strip().split("\n")[0].strip("# ").strip()   # title page, contents…
    if not chapters or (len(chapters) == 1 and chapters[0][0] is None):     # no headings found: make parts
        words = text.split("\n\n")
        chapters, cur, n = [], [], 0
        for para in words:
            cur.append(para)
            if len(" ".join(cur).split()) >= 3000:
                n += 1
                chapters.append((f"Part {n}", "\n\n".join(cur)))
                cur = []
        if cur:
            chapters.append((f"Part {n + 1}" if chapters else None, "\n\n".join(cur)))
    out = []
    for i, (t, body) in enumerate(chapters, 1):
        secs = split_sections(body.strip())
        if secs:
            out.append({"title": t or f"Chapter {i}", "sections": secs})
    front = front or first_head
    if out and front:
        out[0]["front_title"] = front[:120]
    return out


class _HTMLText(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "li", "blockquote", "section", "tr"}

    def __init__(self):
        super().__init__()
        self.out, self.title, self._in_h, self._skip, self._in_title = [], "", False, 0, False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        if tag in self.BLOCK:
            self.out.append("\n\n")
        if tag in ("h1", "h2", "h3"):
            self._in_h = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        if tag in self.BLOCK:
            self.out.append("\n\n")
        if tag in ("h1", "h2", "h3"):
            self._in_h = False
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_h and (not self.title or self._in_title):
            self.title = (self.title + " " + data.strip()).strip() if self._in_title else data.strip()
            self._in_title = True
            return                               # the chapter title is read out as the heading, not twice
        self.out.append(data)

    def text(self):
        return _clean("".join(self.out))


def read_epub(path):
    z = zipfile.ZipFile(path)
    container = z.read("META-INF/container.xml").decode("utf8", "ignore")
    opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
    opf = z.read(opf_path).decode("utf8", "ignore")
    base = os.path.dirname(opf_path)
    manifest = {m.group(1): html.unescape(m.group(2)) for m in
                re.finditer(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf)}
    manifest.update({m.group(2): html.unescape(m.group(1)) for m in
                     re.finditer(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf)})
    title = re.search(r"<dc:title[^>]*>([^<]+)</dc:title>", opf)
    chapters = []
    for idref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
        href = manifest.get(idref)
        if not href:
            continue
        try:
            raw = z.read(os.path.normpath(os.path.join(base, href)).replace("\\", "/")).decode("utf8", "ignore")
        except KeyError:
            continue
        p = _HTMLText()
        p.feed(raw)
        body = p.text()
        if len(body.split()) < 120:          # cover, contents, copyright pages
            continue
        secs = split_sections(body)
        if secs:
            chapters.append({"title": p.title or f"Chapter {len(chapters) + 1}", "sections": secs})
    return (html.unescape(title.group(1)).strip() if title else ""), chapters


def read_docx(path):
    import docx                              # python-docx
    doc = docx.Document(path)
    lines = []
    for para in doc.paragraphs:
        style = (para.style.name or "").lower() if para.style is not None else ""
        if style.startswith("heading") or style == "title":
            lines.append(f"\n\n# {para.text.strip()}\n\n")
        else:
            lines.append(para.text + "\n\n")
    return "".join(lines)


def read_pdf(path):
    from pypdf import PdfReader
    pages = [pg.extract_text() or "" for pg in PdfReader(path).pages]
    text = "\n\n".join(pages)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)            # words hyphenated across lines
    text = re.sub(r"(?<![.!?”\"’:])\n(?!\n)", " ", text)       # line wraps inside paragraphs
    return text


CAST_HEAD = re.compile(r"^\s*(?:#{1,3}\s*)?\**(characters|the characters|cast|cast of characters|dramatis personae|"
                       r"who's who)\**\s*:?\s*$", re.I)


def extract_characters(text):
    """A Characters list near the start ("Mira: a woman", "Jake: a man", one per line) → locked cast, and the
    text without the list (it isn't read aloud)."""
    lines = text.split("\n")
    for i, line in enumerate(lines[:80]):
        if not CAST_HEAD.match(line):
            continue
        found, j = [], i + 1
        while j < len(lines):
            if not lines[j].strip():
                j += 1
                continue
            got = sm.parse_characters(lines[j])
            if not got:
                break
            found += got
            j += 1
        if found:
            cast = []
            for name, ty in found:
                g, a = TYPE_TO_GA.get(ty, ("female", "adult"))
                cast.append({"name": name, "gender": g, "age": a, "kind": "creature" if ty == "creature" else "person",
                             "voice": "", "locked": True, "tagged": True})
            return "\n".join(lines[:i] + lines[j:]), cast
    return text, []


def import_file(cfg, owner, path, filename, title, level):
    ext = Path(filename).suffix.lower()
    cast = []
    if ext == ".epub":
        t, chapters = read_epub(path)
        title = title or t
    else:
        if ext == ".docx":
            text = read_docx(path)
        elif ext == ".pdf":
            text = read_pdf(path)
        elif ext in (".txt", ".md", ".markdown", ".text", ""):
            raw = Path(path).read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
        else:
            raise ValueError("Use a .txt, .md, .docx, .epub or .pdf file.")
        text, cast = extract_characters(text)
        chapters = split_chapters(text)
        if chapters and not title:
            title = chapters[0].pop("front_title", "")
    if not chapters:
        raise ValueError("No readable text was found in that file.")
    return new_project(cfg, owner, title or Path(filename).stem.replace("_", " "), level, chapters,
                       {"type": "file", "name": filename}, cast)


def import_text(cfg, owner, title, text, level):
    text, cast = extract_characters(text)
    chapters = split_chapters(text)
    if not chapters:
        raise ValueError("There's no text to read.")
    title = title or chapters[0].get("front_title", "")
    return new_project(cfg, owner, title or "Untitled", level, chapters, {"type": "paste"}, cast)


TYPE_TO_GA = {"female": ("female", "adult"), "male": ("male", "adult"), "girl": ("female", "child"),
              "boy": ("male", "child"), "old_woman": ("female", "elder"), "old_man": ("male", "elder"),
              "creature": ("male", "adult")}


def import_storymaker(cfg, owner, full, story_id):
    """full: StoryMaker's /api/stories/<id>/full (permission already checked by StoryMaker)."""
    chapters, cur = [], None
    for sc in full["scenes"]:
        if sc.get("chapterTitle") or cur is None:
            ct = sc.get("chapterTitle") or ""
            cur = {"title": (full["title"] if not ct else ct if re.fullmatch(r"chapter\s+\w+", ct, re.I)
                             else f"Chapter {sc['chapter']}: {ct}"), "sections": []}
            chapters.append(cur)
        cur["sections"].append({"text": sc["narration"], "sound": sc.get("sound")})
    cast = []
    for c in full.get("cast") or []:
        g, a = TYPE_TO_GA.get(c.get("type"), ("female", "adult"))
        cast.append({"name": c["name"], "gender": g, "age": a, "kind": "creature" if c.get("type") == "creature" else "person",
                     "voice": "", "locked": bool(c.get("confirmed")), "tagged": bool(c.get("confirmed"))})
    pid = new_project(cfg, owner, full["title"], full.get("level") or "family", chapters,
                      {"type": "storymaker", "id": story_id}, cast)
    d, p, s = load_project(cfg, pid)
    p["sounds_done"] = all(sec.get("sound") for ch in chapters for sec in ch["sections"])
    save_project(d, p)
    return pid


# ───────────────────────────── script: lines (exact text) ─────────────────────────────

def segment_section(text):
    """Narration and dialogue lines, in order, with the exact words of the text."""
    segs = []
    for pi, para in enumerate(re.split(r"\n\s*\n", text)):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        if para.startswith("# "):
            segs.append({"kind": "heading", "speaker": "Narrator", "text": para[2:].strip(), "emotion": "neutral",
                         "para": pi})
            continue
        bits = re.split(r"(“[^”]+”)", para)
        if bits and "“" in bits[-1]:              # a speech that carries on into the next paragraph
            head, _, rest = bits[-1].partition("“")
            bits[-1:] = [head, f"“{rest}”"]
        for bit in bits:
            bit = bit.strip()
            if not bit:
                continue
            if bit.startswith("“") and bit.endswith("”"):
                segs.append({"kind": "dialogue", "speaker": "", "text": bit[1:-1].strip(), "emotion": "neutral",
                             "para": pi})
            else:
                segs.append({"kind": "narration", "speaker": "Narrator", "text": bit, "emotion": "neutral", "para": pi})
    for i, s_ in enumerate(segs):
        s_["id"] = hashlib.sha1(f"{i}|{s_['kind']}|{s_['text']}".encode()).hexdigest()[:10]
    return segs


# ───────────────────────────── casting director ─────────────────────────────

CAST_PROMPT = """You are the casting director for an audiobook of "{title}". For every numbered quote,
say exactly who speaks it and with what emotion.

Known characters. Genders marked LOCKED are certain: never change them.
{cast}

Rules:
- Use the speech tags ("Mira said", "he asked", "said Tom") and the flow of the conversation; in a
  back-and-forth, speakers usually alternate.
- Quotes spoken by "I" (first-person narration, "I said") belong to "Narrator".
- If someone not listed speaks, add them to "characters" with gender "female" or "male", age "child",
  "teen", "adult" or "elder", and kind "person" or "creature". Decide gender from the pronouns the
  text uses for them (she/her = female, he/him = male) and from their name and role (a mother, a
  king, a girl...). Never guess from nothing: read the text around them.
- "emotion" is one of: {emotions}.
{extra}
Text (the quotes are marked [Q1], [Q2]...):
---
{text}
---

Reply with ONLY a JSON object, no markdown fences:
{{"characters": [{{"name": "...", "gender": "female", "age": "adult", "kind": "person"}}],
  "quotes": {{"Q1": {{"speaker": "Name", "emotion": "neutral"}}}},
  "narrator_emotion": "neutral"}}"""


def cast_text(cast):
    if not cast:
        return "(none yet)"
    out = []
    for c in cast:
        out.append(f"- {c['name']}: {'woman' if c['gender'] == 'female' else 'man'}"
                   f"{'' if c.get('age', 'adult') == 'adult' else ', ' + c['age']}"
                   f"{', creature' if c.get('kind') == 'creature' else ''}{' (LOCKED)' if c.get('locked') else ''}")
    return "\n".join(out)


def writer_cfg(cfg, level):
    c = dict(cfg)
    if level == "explicit":
        c["ADULT_CONTENT"] = "on"
        sm.adult_config(c)                   # the local writer only; nothing leaves the Mac
    return c


def _norm_name(n):
    return re.sub(r"\s+", " ", str(n or "")).strip()


def ask_patiently(cfg, prompt, system=None, progress=None):
    """The writer, with patience for free plans' "too many requests" limits (Groq, Gemini): wait and try again."""
    for attempt in range(6):
        try:
            return sm.ask_writer(cfg, prompt, system=system)
        except RuntimeError as e:
            msg = str(e)
            if attempt == 5 or not re.search(r"\b429\b|rate.?limit|too many requests|overloaded|\b503\b", msg, re.I):
                raise
            m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", msg)
            wait = min(300, (int(m.group(1) or 0) * 60 + float(m.group(2))) + 2 if m else 20 * (attempt + 1))
            log(f"The writer asked us to slow down; waiting {int(wait)} s.")
            if progress:
                progress(f"Waiting {int(wait)} s for the writer (free-plan limit)")
            time.sleep(wait)


def run_casting(cfg, pid, progress=lambda f, d: None, only=None):
    """Label every quote with its speaker and emotion; add characters with gender and age.
    `only`: section ids to cast again (None = all not yet cast)."""
    d, p, s = load_project(cfg, pid)
    wc = writer_cfg(cfg, p["level"])
    use_ai = wc["STORY_PROVIDER"].lower() != "demo"
    cast = p["cast"]
    by_name = {c["name"].lower(): c for c in cast}
    sections = [sec for ch in p["chapters"] for sec in ch["sections"]]
    todo = [sec for sec in sections if (only is None and not s["sections"].get(sec["id"] + ":cast")) or
            (only is not None and sec["id"] in only)]
    batches, cur, words = [], [], 0
    for sec in todo:                         # about 2,500 words per request
        cur.append(sec)
        words += len(sec["text"].split())
        if words > 2500:
            batches.append(cur)
            cur, words = [], 0
    if cur:
        batches.append(cur)
    extra = ("This is an explicit adult story: every character is an adult (20 or older), so use age 'adult' or 'elder' only. "
             "Emotions like seductive and breathless fit intimate moments.\n"
             if p["level"] == "explicit" else "")
    for b_i, batch in enumerate(batches):
        progress(b_i / max(1, len(batches)), f"Casting: part {b_i + 1} of {len(batches)}")
        qmap, chunks, q = {}, [], 0
        for sec in batch:
            out = []
            last_para = None
            for seg in s["sections"][sec["id"]]:
                if last_para is not None and seg["para"] != last_para:
                    out.append("\n\n")
                last_para = seg["para"]
                if seg["kind"] == "dialogue":
                    q += 1
                    qmap[f"Q{q}"] = seg
                    out.append(f"[Q{q}] “{seg['text']}” ")
                else:
                    out.append(seg["text"] + " ")
            chunks.append("".join(out).strip())
        answer = None
        if use_ai and qmap:
            try:
                answer = ask_patiently(wc, CAST_PROMPT.format(
                    title=p["title"], cast=cast_text(cast), emotions=", ".join(EMOTIONS), extra=extra,
                    text="\n\n* * *\n\n".join(chunks)), system=sm.EXPLICIT_SYSTEM if p["level"] == "explicit" else None,
                    progress=lambda t: progress(b_i / max(1, len(batches)), t))
            except Exception as e:
                log(f"Casting by the writer failed ({str(e)[:160]}); using the speech tags instead.")
        if answer:
            for ch_ in answer.get("characters") or []:
                name = _norm_name(ch_.get("name"))
                if not name or name.lower() == "narrator":
                    continue
                g = "male" if str(ch_.get("gender", "")).lower().startswith("m") else "female"
                a = ch_.get("age") if ch_.get("age") in ("child", "teen", "adult", "elder") else "adult"
                k = "creature" if ch_.get("kind") == "creature" else "person"
                c = by_name.get(name.lower())
                if c is None:
                    c = {"name": name, "gender": g, "age": a, "kind": k, "voice": "", "locked": False, "review": True}
                    cast.append(c)
                    by_name[name.lower()] = c
                elif not c.get("locked"):
                    c.update(gender=g, age=a, kind=k)
            for key, seg in qmap.items():
                info = (answer.get("quotes") or {}).get(key) or {}
                who = _norm_name(info.get("speaker"))
                seg["speaker"] = "Narrator" if who.lower() in ("narrator", "i", "me") else who
                seg["emotion"] = info.get("emotion") if info.get("emotion") in EMOTIONS else "neutral"
            nem = answer.get("narrator_emotion")
            for sec in batch:
                for seg in s["sections"][sec["id"]]:
                    if seg["kind"] != "dialogue":
                        seg["emotion"] = nem if nem in EMOTIONS else "neutral"
        # anything still unknown: the speech tags, then the conversation flow
        for sec in batch:
            segs = s["sections"][sec["id"]]
            if any(seg["kind"] == "dialogue" and not seg["speaker"] for seg in segs):
                prose = "".join((f"“{seg['text']}” " if seg["kind"] == "dialogue" else seg["text"] + " ") for seg in segs)
                guesses = sm.lines_from_prose(prose, [{"name": c["name"], "voice": ga_type(c)} for c in cast])
                dlg = [ln for ln in guesses if ln["text"] in {seg["text"] for seg in segs if seg["kind"] == "dialogue"}]
                gi = 0
                for seg in segs:
                    if seg["kind"] == "dialogue" and not seg["speaker"]:
                        while gi < len(dlg) and dlg[gi]["text"] != seg["text"]:
                            gi += 1
                        seg["speaker"] = dlg[gi]["speaker"] if gi < len(dlg) else "Narrator"
                        gi += 1
            for seg in segs:                 # every speaker is in the cast, with a gender
                who = seg["speaker"]
                if who and who != "Narrator" and who.lower() not in by_name:
                    g = sm.guess_gender(who, sec["text"], [c["name"] for c in cast])
                    c = {"name": who, "gender": "male" if g == "he" else "female", "age": "adult", "kind": "person",
                         "voice": "", "locked": False, "review": True}
                    if not g:
                        c["why"] = "The text doesn't say if this is a woman or a man: please set it."
                    cast.append(c)
                    by_name[who.lower()] = c
                elif who and who.lower() in by_name:
                    seg["speaker"] = by_name[who.lower()]["name"]
            check_speakers(segs, cast)
            s["sections"][sec["id"] + ":cast"] = True
        p["cast"] = cast
        save_project(d, p, s)
    # the text has the last word: genders from its pronouns, then speakers from its speech tags again
    check_genders(p, s)
    if p["level"] == "explicit":                  # adults only: nobody in an explicit book may read as under 20
        young = [c["name"] for c in cast if c.get("age") in ("child", "teen")]
        if young:
            save_project(d, p, s)
            raise ValueError(f"Stopped: this is an explicit book, and the casting read {', '.join(young)} as under 20. "
                             "Explicit books are for adults only. If that's wrong, set their age to Adult in the "
                             "Cast step; if not, this book can't be made.")
    for sec_id, segs in s["sections"].items():
        if not sec_id.endswith(":cast"):
            check_speakers(segs, p["cast"])
    count_lines(p, s)
    p["cast"] = [c for c in p["cast"] if c.get("lines") or c.get("locked")]   # named by the AI but never speaks
    assign_voices(cfg, p)
    p["cast_done"] = True
    save_project(d, p, s)
    progress(1, "Cast")


SPEECH_VERBS = (r"said|says|asked|asks|replied|whispered|shouted|called|cried|answered|murmured|muttered|added|"
                r"snapped|laughed|breathed|moaned|sighed|yelled|told|began|continued|agreed|admitted|insisted|"
                r"demanded|warned|teased|gasped|groaned|purred|growled|hissed|screamed|exclaimed|offered|"
                r"pleaded|begged|repeated|corrected|joked|mumbled|stammered|blurted|whimpered|sobbed|chuckled")
TAG_AFTER = re.compile(r"^[,.!?—–-]?\s*(?:(?P<pro>he|she|I)|(?P<name>(?:Mr|Mrs|Ms|Dr|Miss)\.? [A-Z][\w'-]+|[A-Z][\w'-]+"
                       r"(?: [A-Z][\w'-]+)?))\s+(?:" + SPEECH_VERBS + r")\b"
                       r"|^[,.!?—–-]?\s*(?:" + SPEECH_VERBS + r")\s+(?:(?P<pro2>he|she)|(?P<name2>(?:Mr|Mrs|Ms|Dr|Miss)\.? "
                       r"[A-Z][\w'-]+|[A-Z][\w'-]+(?: [A-Z][\w'-]+)?))\b")


def speech_tag(seg_after):
    """The speech tag right after a quote: ('name', 'Mira') / ('she', None) / ('I', None) / None."""
    if not seg_after or seg_after["kind"] != "narration":
        return None
    m = TAG_AFTER.match(seg_after["text"])
    if not m:
        return None
    pro = m.group("pro") or m.group("pro2")
    if pro:
        return (pro if pro == "I" else pro.lower(), None)
    return ("name", m.group("name") or m.group("name2"))


def names_in(text, by_key):
    found = []
    for w in re.findall(r"(?:Mr|Mrs|Ms|Dr|Miss)\.? [A-Z][\w'-]+|[A-Z][\w'-]+", text):
        c = by_key.get(w.lower()) or by_key.get(re.sub(r"^(mr|mrs|ms|dr|miss)\.?\s+", "", w.lower()))
        if c and c["name"] not in found:
            found.append(c["name"])
    return found


def check_speakers(segs, cast):
    """Make the AI's choices agree with the text itself:
      - a quote tagged with a name ("…,” Sam said) is that person's;
      - a quote tagged "she said" can't go to a man, or "he said" to a woman: it goes to the person of
        the right gender who spoke most recently in the scene, or is flagged for you to check;
      - "I said" is the narrator's;
      - an untagged quote right after an action naming one character ("Mrs. Hart came down the
        stairs. “Bed!”") is that character's;
      - more quotes in the same paragraph, with no tag of their own, are the same speaker's.
    Returns the number of lines corrected."""
    by_key = {}
    for c in cast:
        by_key.setdefault(c["name"].lower(), c)
        parts = re.sub(r"^(mr|mrs|ms|dr|miss)\.?\s+", "", c["name"].lower()).split()
        for w in (parts[0], parts[-1]) if parts else ():
            by_key.setdefault(w, c)
    gender_of = {c["name"]: c["gender"] for c in cast}
    fixed, recent = 0, []                    # recent: speakers in the order they last spoke
    for i, seg in enumerate(segs):
        if seg["kind"] != "dialogue":
            continue
        seg.pop("check", None)
        nxt = segs[i + 1] if i + 1 < len(segs) and segs[i + 1]["para"] == seg["para"] else None
        tag = speech_tag(nxt)
        want = seg["speaker"]
        if tag and tag[0] == "name":
            c = by_key.get(tag[1].lower()) or by_key.get(re.sub(r"^(mr|mrs|ms|dr|miss)\.?\s+", "", tag[1].lower()).split()[0])
            if c:
                want = c["name"]
        elif tag and tag[0] == "I":
            want = "Narrator"
        elif tag and tag[0] in ("he", "she"):
            g = "female" if tag[0] == "she" else "male"
            if gender_of.get(want) != g and want != "Narrator":
                cands = [w for w in reversed(recent) if gender_of.get(w) == g] or \
                        [w for w in names_in(" ".join(x["text"] for x in segs), by_key) if gender_of.get(w) == g][:1]
                if cands:
                    want = cands[0]
                else:
                    seg["check"] = f"The text says “{tag[0]}”, but {want or 'nobody'} was chosen."
        elif not tag:                        # no tag: a second quote in the same paragraph is the same speaker
            prev = next((segs[k] for k in range(i - 1, -1, -1) if segs[k]["para"] == seg["para"]
                         and segs[k]["kind"] == "dialogue"), None)
            before = segs[i - 1] if i > 0 and segs[i - 1]["para"] == seg["para"] else None
            if prev and prev["speaker"]:
                want = prev["speaker"]
            elif before and before["kind"] == "narration":
                who = names_in(before["text"], by_key)
                if len(who) == 1 and not re.search(r"\b(?:to|at|with|toward|towards)\s+" + re.escape(who[0].split()[-1]),
                                                   before["text"]):
                    want = who[0]
        if want and want != seg["speaker"]:
            seg["speaker"] = want
            seg["fixed"] = True
            fixed += 1
        if seg["speaker"] and seg["speaker"] != "Narrator":
            if seg["speaker"] in recent:
                recent.remove(seg["speaker"])
            recent.append(seg["speaker"])
    return fixed


FEMALE_TITLES = re.compile(r"^(mrs|ms|miss|lady|queen|princess|aunt|auntie|grandma|granny|mother|mom|mum|sister|madam|"
                           r"madame|duchess|countess|empress|nana|mama|ma'am|dame|girl|woman)\b", re.I)
MALE_TITLES = re.compile(r"^(mr|sir|lord|king|prince|uncle|grandpa|grandad|granddad|father|dad|brother|duke|count|emperor|"
                         r"papa|pa|boy|man|monsieur|señor|senor|herr)\b", re.I)


def pronoun_votes(name, text, others):
    """After each mention of a name, the first "she" or "he" that follows (in that sentence or the next,
    before anyone else is named) is one vote. "Lily ran to the door. She threw it open" → one vote for "she"."""
    first = re.escape(name.split()[0])
    stop_names = [re.escape(re.sub(r"^(mr|mrs|ms|dr|miss)\.?\s+", "", o, flags=re.I).split()[0]) for o in others if o.strip()]
    stop = re.compile(r"\b(" + "|".join(stop_names) + r")\b") if stop_names else None
    she = he = 0
    for m in re.finditer(r"\b" + first + r"\b", text):
        window = text[m.end():m.end() + 220]
        ends = [k.end() for k in re.finditer(r"[.!?…]”?\s", window)]
        if len(ends) >= 2:
            window = window[:ends[1]]                          # this sentence and the next
        if stop:
            cut = stop.search(window)
            window = window[:cut.start()] if cut else window
        hit = re.search(r"\b(she|he|herself|himself)\b", window, re.I)     # "her"/"him" may be someone else
        if hit:
            if hit.group(1).lower() in ("she", "herself"):
                she += 1
            else:
                he += 1
    return she, he


def check_genders(p, s):
    """The text decides: a character the book calls "she" is a woman or girl, "he" a man or boy, whatever the AI
    said. Locked characters are never touched. Changed ones are marked for you to check."""
    sections = [(sec_id, segs) for sec_id, segs in s["sections"].items() if not sec_id.endswith(":cast")]
    full = " ".join(seg["text"] if seg["kind"] != "dialogue" else f"“{seg['text']}”"
                    for _, segs in sections for seg in segs)
    names = [c["name"] for c in p["cast"]]
    tags = {}
    for _, segs in sections:
        for i, seg in enumerate(segs):
            if seg["kind"] == "dialogue" and seg["speaker"]:
                nxt = segs[i + 1] if i + 1 < len(segs) and segs[i + 1]["para"] == seg["para"] else None
                t = speech_tag(nxt)
                if t and t[0] in ("he", "she"):
                    tags.setdefault(seg["speaker"], [0, 0])[0 if t[0] == "she" else 1] += 1
    changed = 0
    for c in p["cast"]:
        if c.get("locked"):
            continue
        bare = re.sub(r"^(mr|mrs|ms|dr|miss)\.?\s+", "", c["name"], flags=re.I)
        if FEMALE_TITLES.match(c["name"]):
            she, he = 99, 0
        elif MALE_TITLES.match(c["name"]):
            she, he = 0, 99
        else:
            ps, ph = pronoun_votes(bare, full, [n for n in names if n != c["name"]])
            ts, th = tags.get(c["name"], [0, 0])
            she, he = ps + ts, ph + th
        want = "female" if she >= 2 and she >= 2 * he else "male" if he >= 2 and he >= 2 * she else None
        if want and want != c["gender"]:
            c["gender"] = want
            c["voice"] = ""                      # a voice of the right gender is chosen again
            c["review"] = True
            c["why"] = (f"Changed to {'female' if want == 'female' else 'male'}: the book calls "
                        f"{'her “she”' if want == 'female' else 'him “he”'}.")
            changed += 1
    return changed


def count_lines(p, s):
    counts = {}
    for sec_id, segs in s["sections"].items():
        if sec_id.endswith(":cast"):
            continue
        for seg in segs:
            counts[seg["speaker"] or "?"] = counts.get(seg["speaker"] or "?", 0) + 1
    for c in p["cast"]:
        c["lines"] = counts.get(c["name"], 0)
    p["narrator_lines"] = counts.get("Narrator", 0)


# ───────────────────────────── voices ─────────────────────────────

def ga_type(c):
    """gender + age (+ kind) → StoryMaker's voice type."""
    if c.get("kind") == "creature":
        return "creature"
    if c["gender"] == "female":
        return {"child": "girl", "teen": "girl", "elder": "old_woman"}.get(c.get("age"), "female")
    return {"child": "boy", "teen": "boy", "elder": "old_man"}.get(c.get("age"), "male")


def voice_library(cfg):
    """Every voice you can pick: the Kokoro voices (Chatterbox learns them from a short sample made with
    Kokoro) and your own clips in the voices folder (only voices you have the right to use)."""
    out = [{"id": f"kokoro:{v[0]}", "name": v[1], "gender": v[2], "accent": v[3], "note": v[4], "own": False}
           for v in sm.KOKORO_CATALOGUE]
    meta_file = studio_dir(cfg) / "voices" / "voices.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    for f in sorted((studio_dir(cfg) / "voices").glob("*")):
        if f.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac") and not f.name.startswith("auto_"):
            m = meta.get(f.name, {})
            out.append({"id": f"clip:{f.name}", "name": m.get("name") or f.stem.replace("-", " ").replace("_", " ").title(),
                        "gender": m.get("gender", "female"), "accent": m.get("accent", ""), "note": m.get("note", "your clip"),
                        "own": True})
    return out


def assign_voices(cfg, p):
    """Every character without a voice gets a distinct one of the right gender and age; the narrator gets
    one too. Voices you picked stay."""
    taken = {c["voice"] for c in p["cast"] if c.get("voice")}
    if not p.get("narrator"):
        p["narrator"] = "kokoro:" + (cfg.get("KOKORO_VOICE") or "af_heart")
    taken.add(p["narrator"])
    pool = sm.parse_pool(cfg.get("KOKORO_VOICES", ""))
    family = {"female": ["female", "old_woman", "girl"], "girl": ["girl", "female"], "old_woman": ["old_woman", "female"],
              "male": ["male", "old_man", "boy"], "boy": ["boy", "male"], "old_man": ["old_man", "male"],
              "creature": ["creature", "male"]}
    for c in sorted(p["cast"], key=lambda c: -c.get("lines", 0)):     # main characters get first pick
        if c.get("voice"):
            continue
        options = [v for ty in family[ga_type(c)] for v in pool.get(ty, [])]
        fresh = [v for v in options if f"kokoro:{v}" not in taken]
        pick = (fresh or options or ["af_bella"])[0]
        c["voice"] = f"kokoro:{pick}"
        taken.add(c["voice"])


def voice_for(p, speaker):
    if not speaker or speaker == "Narrator":
        return p.get("narrator") or "kokoro:af_heart"
    for c in p["cast"]:
        if c["name"] == speaker:
            return c.get("voice") or p.get("narrator")
    return p.get("narrator") or "kokoro:af_heart"


def reference_clip(cfg, voice):
    """The short sample Chatterbox learns a voice from."""
    kind, _, name = voice.partition(":")
    vdir = studio_dir(cfg) / "voices"
    if kind == "clip":
        f = vdir / name
        if not f.exists():
            raise ValueError(f"Voice clip {name} is missing.")
        return f
    f = vdir / f"auto_{hashlib.sha1(name.encode()).hexdigest()[:10]}.wav"
    if not sm.valid_media(f):
        text = ("Every story begins with a voice. Listen closely, because this one has travelled a long way to reach you. "
                "“Are you ready?” she asked. “I have been ready all my life,” he answered, and smiled.")
        tmp = f.with_suffix(".src.wav")
        sm.narrate(cfg, text, tmp, "kokoro", name)
        sm.level_match(tmp, f, target_rms=-20)
        tmp.unlink(missing_ok=True)
    return f


# ───────────────────────────── recording engines ─────────────────────────────

EMOTION_STYLE = {   # Chatterbox: (exaggeration, cfg_weight, temperature); Kokoro: speed
    "neutral": (0.5, 0.5, 0.8, 1.0), "warm": (0.55, 0.5, 0.8, 0.97), "happy": (0.7, 0.45, 0.8, 1.04),
    "excited": (0.85, 0.35, 0.85, 1.08), "playful": (0.75, 0.4, 0.85, 1.04), "tender": (0.6, 0.45, 0.75, 0.94),
    "sad": (0.6, 0.5, 0.75, 0.9), "fearful": (0.75, 0.4, 0.85, 1.05), "tense": (0.7, 0.4, 0.8, 1.02),
    "angry": (0.9, 0.35, 0.85, 1.06), "whisper": (0.4, 0.55, 0.7, 0.9), "seductive": (0.7, 0.3, 0.8, 0.9),
    "breathless": (0.8, 0.35, 0.85, 1.0),
}


class Chatterbox:
    """Chatterbox, run in its own process (cb_worker.py) so its graphics memory can be handed back in full:
    the process is replaced with a fresh one whenever it has built up too much, or if it ever runs out."""
    _inst = None
    _lock = threading.Lock()
    _avail = None

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.limit = float(cfg.get("CHATTERBOX_RECYCLE_GB") or 3) * 1024 ** 3
        self.every = int(cfg.get("CHATTERBOX_RECYCLE_LINES") or 15)
        self.lines = 0
        self.device = None
        self._gen = threading.Lock()

    @classmethod
    def get(cls, cfg):
        with cls._lock:
            if cls._inst is None:
                cls._inst = Chatterbox(cfg)
            return cls._inst

    @classmethod
    def available(cls):
        if cls._avail is None:
            import importlib.util
            cls._avail = bool(importlib.util.find_spec("chatterbox") and importlib.util.find_spec("torch"))
        return cls._avail

    def start(self, device=None):
        import subprocess
        free_ollama(self.cfg)                       # the writer's model and Chatterbox don't both fit in 16 GB
        model = self.cfg.get("CHATTERBOX_MODEL", "standard")
        self.device = device or self.cfg.get("CHATTERBOX_DEVICE", "auto")
        self.lines = 0
        log(f"Starting Chatterbox ({model}{', on the processor' if self.device == 'cpu' else ''})…")
        env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK="1")
        self.proc = subprocess.Popen([sys.executable, str(HERE / "cb_worker.py")], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1, env=env)
        self._send({"model": model, "device": self.device})
        r = self._read()
        if not r.get("ok"):
            self.stop()
            raise RuntimeError(r.get("error") or "Chatterbox didn't start.")

    def stop(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=20)
            except Exception:
                self.proc.kill()
            self.proc = None

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            return {"ok": False, "error": "Chatterbox stopped unexpectedly.", "died": True}
        return json.loads(line)

    def idle(self):
        """Recording is finished: give all of its memory back to the Mac."""
        with self._gen:
            self.stop()

    def say(self, text, ref, emotion, out_wav, max_chars=260):
        ex, cw, temp, _ = EMOTION_STYLE.get(emotion, EMOTION_STYLE["neutral"])
        job = {"chunks": chunk_text(text, max_chars), "ref": str(ref), "ex": ex, "cw": cw, "temp": temp,
               "out": str(out_wav)}
        with self._gen:
            for attempt in range(4):
                fallback = attempt == 3 and self.cfg.get("CHATTERBOX_DEVICE", "auto") != "cpu"
                if fallback:                         # the graphics chip keeps running short: this one line on the processor
                    self.stop()
                    log("Recording this line on the processor instead (slower, but it has more room).")
                    self.start("cpu")
                elif self.proc is None or self.proc.poll() is not None:
                    self.start()
                try:
                    self._send(job)
                    r = self._read()
                except (BrokenPipeError, OSError):
                    r = {"ok": False, "error": "Chatterbox stopped unexpectedly.", "died": True}
                if r.get("ok"):
                    self.lines += 1
                    if fallback:
                        self.stop()                  # back to the graphics chip for the next line
                    elif r.get("mem", 0) > self.limit or self.lines >= self.every:
                        log(f"Refreshing Chatterbox to hand back its memory "
                            f"({r.get('mem', 0) / 1024 ** 3:.1f} GB after {self.lines} lines).")
                        self.stop()
                    return
                if r.get("oom") or r.get("died"):
                    log(f"Chatterbox needs a fresh start ({r.get('error', '')[:120]}); trying that line again.")
                    self.stop()
                    if self.proc is None:
                        free_ollama(self.cfg)
                        time.sleep(3)
                    continue
                raise RuntimeError(r.get("error") or "Chatterbox couldn't record that line.")
            raise RuntimeError("Chatterbox ran out of memory four times on one line. Close other apps (Draw Things, "
                               "Ollama) and press the button again, or set CHATTERBOX_MODEL=turbo in studio.env.")


_OLLAMA_CFG = {}


def free_ollama(cfg):
    """Ask Ollama to unload any model it's holding (it loads again by itself the next time it's needed)."""
    cfg = cfg or _OLLAMA_CFG.get("cfg")
    if not cfg:
        return
    _OLLAMA_CFG["cfg"] = cfg
    base = (cfg.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
    try:
        import requests
        for m in requests.get(f"{base}/api/ps", timeout=5).json().get("models", []):
            requests.post(f"{base}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=30)
            log(f"Unloaded {m['name']} from Ollama to make room for the voices.")
    except Exception:
        pass


def chunk_text(text, max_chars=260):
    """Sentences, packed into pieces short enough for one Chatterbox call (about 40 seconds at most)."""
    sents = [s_ for s_ in re.split(r"(?<=[.!?…])\s+", text.strip()) if s_]
    out, cur = [], ""
    for s_ in sents:
        while len(s_) > max_chars:                       # a very long sentence: split at commas or spaces
            cut = max(s_.rfind(", ", 0, max_chars), s_.rfind("; ", 0, max_chars))
            cut = cut + 1 if cut > max_chars // 3 else s_.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            out.append((cur + " " + s_[:cut]).strip()) if cur else out.append(s_[:cut].strip())
            cur, s_ = "", s_[cut:].strip()
        if len(cur) + len(s_) + 1 > max_chars and cur:
            out.append(cur)
            cur = s_
        else:
            cur = (cur + " " + s_).strip()
    if cur:
        out.append(cur)
    return out or [text]


ENGINE_VERSION = "1"


def part_key(engine, voice, emotion, text, cfg):
    extra = cfg.get("CHATTERBOX_MODEL", "standard") if engine == "chatterbox" else cfg.get("KOKORO_SPEED")
    return hashlib.sha1(f"{ENGINE_VERSION}|{engine}|{extra}|{voice}|{emotion}|{text}".encode()).hexdigest()[:16]


def render_part(cfg, engine, voice, emotion, text, parts_dir, work):
    """One line, recorded and level-matched; saved so it's never recorded twice."""
    key = part_key(engine, voice, emotion, text, cfg)
    out = parts_dir / f"{key}.flac"
    if sm.valid_media(out):
        return out
    raw = work / f"raw_{key}.wav"
    lv = work / f"lv_{key}.wav"
    if engine == "chatterbox":
        Chatterbox.get(cfg).say(text, reference_clip(cfg, voice), emotion, raw, int(cfg["STUDIO_MAX_LINE_CHARS"]))
    else:
        kind, _, name = voice.partition(":")
        if kind != "kokoro":                 # your own clips need Chatterbox; Kokoro uses a voice of the same gender
            meta_file = studio_dir(cfg) / "voices" / "voices.json"
            meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
            name = "am_michael" if meta.get(name, {}).get("gender") == "male" else "af_heart"
        speed = EMOTION_STYLE.get(emotion, EMOTION_STYLE["neutral"])[3]
        spec = name if "@" in name else f"{name}@{speed}"
        sm.narrate(cfg, text, raw, "kokoro", spec)
    sm.level_match(raw, lv)
    sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(lv), "-c:a", "flac", str(out)])
    raw.unlink(missing_ok=True)
    lv.unlink(missing_ok=True)
    return out


# ───────────────────────────── jobs (one at a time, in the background) ─────────────────────────────

def status_file(d):
    return d / "status.json"


def read_status(d):
    try:
        return json.loads(status_file(d).read_text())
    except Exception:
        return {}


def write_status(d, **kw):
    st = read_status(d)
    st.update(kw, updated=time.strftime("%Y-%m-%dT%H:%M:%S"), pid=os.getpid())
    tmp = d / "status.json.tmp"
    tmp.write_text(json.dumps(st))
    tmp.replace(status_file(d))


class Worker:
    """A single background worker: recording uses the whole graphics chip, so jobs run one after another."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.queue = []
        self.cv = threading.Condition()
        self.stop_flags = {}
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, pid, kind, **kw):
        d = project_dir(self.cfg, pid)
        with self.cv:
            if any(j[0] == pid for j in self.queue):
                raise ValueError("This book already has a job waiting.")
            self.queue.append((pid, kind, kw))
            write_status(d, state="queued", job=kind, progress=0, detail="Waiting its turn", error=None)
            self.cv.notify()

    def stop(self, pid):
        self.stop_flags[pid] = True
        with self.cv:
            self.queue = [j for j in self.queue if j[0] != pid]

    def _loop(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                pid, kind, kw = self.queue.pop(0)
            self.stop_flags[pid] = False
            try:
                d = project_dir(self.cfg, pid)
            except ValueError:
                continue
            def progress(frac, detail, **extra):
                if self.stop_flags.get(pid):
                    raise StopJob()
                write_status(d, state="running", job=kind, progress=round(100 * max(0, min(1, frac))), detail=detail,
                             **extra)
            for tries in range(1, 7):
                try:
                    self._run(d, pid, kind, kw, progress)
                    break
                except StopJob:
                    write_status(d, state="stopped", detail="Stopped. Start again to carry on where it left off.")
                    break
                except Exception as e:
                    traceback.print_exc()
                    memory = re.search(r"out of memory|stopped unexpectedly|MPS|Metal|killed", str(e), re.I)
                    if memory and tries < 6 and not self.stop_flags.get(pid):
                        # a memory problem: clear everything out, wait a moment, and carry on by itself
                        if Chatterbox._inst:
                            Chatterbox._inst.idle()
                        free_ollama(self.cfg)
                        log(f"Memory ran short ({str(e)[:100]}); clearing it and carrying on (try {tries + 1} of 6).")
                        write_status(d, state="running", job=kind, error=None,
                                     detail=f"Clearing memory and carrying on (try {tries + 1} of 6)…")
                        time.sleep(30)
                        continue
                    write_status(d, state="failed", error=str(e)[:400],
                                 detail="Something went wrong. Start again to carry on where it left off.")
                    break

    def _run(self, d, pid, kind, kw, progress):
        write_status(d, state="running", job=kind, progress=0, detail="Starting", error=None)
        if kind == "cast":
            run_casting(self.cfg, pid, progress, kw.get("only"))
        elif kind == "sounds":
            run_sounds(self.cfg, pid, progress, kw.get("redo", False))
        elif kind == "record":
            run_record(self.cfg, pid, progress, kw.get("chapters"))
        elif kind == "all":
            _, p, _ = load_project(self.cfg, pid)
            if not p.get("cast_done"):
                run_casting(self.cfg, pid, lambda f, t: progress(f * 0.1, t))
            if not p.get("sounds_done") and self.cfg["SOUNDSCAPES"].lower() == "on":
                run_sounds(self.cfg, pid, lambda f, t: progress(0.1 + f * 0.05, t))
            run_record(self.cfg, pid, lambda f, t, **x: progress(0.15 + f * 0.85, t, **x))
        write_status(d, state="done", progress=100, detail="Finished")


class StopJob(Exception):
    pass


# ───────────────────────────── sound design ─────────────────────────────

def run_sounds(cfg, pid, progress=lambda f, d: None, redo=False):
    d, p, s = load_project(cfg, pid)
    sections = [sec for ch in p["chapters"] for sec in ch["sections"]]
    story = {"title": p["title"], "genre": p.get("genre", ""),
             "scenes": [{"heading": ch["title"], "narration": sec["text"], **({"sound": sec["sound"]} if sec.get("sound") and not redo else {})}
                        for ch in p["chapters"] for sec in ch["sections"]]}
    if p.get("sound_wishes"):
        story["sound_wishes"] = p["sound_wishes"]
    progress(0.1, "Designing the sound")
    wc = writer_cfg(cfg, p["level"])
    wc["_STYLE"] = {"sounds": (p.get("style") or {}).get("sounds") or {}}     # your sound style from StoryMaker
    sm.plan_sounds(wc, story, p["level"], redo=redo)
    for sec, sc in zip(sections, story["scenes"]):
        sec["sound"] = sc.get("sound")
    p["sounds_done"] = True
    save_project(d, p)
    progress(1, "Sounds chosen")


# ───────────────────────────── recording and mixing ─────────────────────────────

def chapter_plan(p, s, ci):
    """Everything spoken in one chapter, in order: (section index, segment, voice, emotion, pause after)."""
    ch = p["chapters"][ci]
    items = [(None, {"kind": "heading", "speaker": "Narrator", "text": f"{ch['title']}.", "emotion": "neutral", "para": -1},
              voice_for(p, "Narrator"), "neutral", 1.2)]
    for si, sec in enumerate(ch["sections"]):
        segs = [g for g in s["sections"].get(sec["id"], []) if g["text"].strip()]
        for k, seg in enumerate(segs):
            nxt = segs[k + 1] if k + 1 < len(segs) else None
            if nxt is None:
                pause = 1.1                                            # end of a section
            elif nxt["para"] != seg["para"]:
                pause = 0.55 if (nxt["speaker"] or "Narrator") == (seg["speaker"] or "Narrator") else 0.45
            else:
                pause = 0.2 if seg["kind"] == "dialogue" or nxt["kind"] == "dialogue" else 0.15
            items.append((si, seg, voice_for(p, seg["speaker"] or "Narrator"),
                          seg.get("emotion") or "neutral", pause))
    return items


def run_record(cfg, pid, progress=lambda f, d, **x: None, chapters=None):
    d, p, s = load_project(cfg, pid)
    engine = p.get("engine") or cfg["STUDIO_ENGINE"]
    if engine == "chatterbox" and not Chatterbox.available():
        raise RuntimeError("Chatterbox isn't installed in the studio yet. Run the studio's install step for "
                           "Chatterbox, or switch this book to Kokoro.")
    if not p.get("cast_done"):
        run_casting(cfg, pid)
        d, p, s = load_project(cfg, pid)
    rcfg = writer_cfg(cfg, p["level"])
    rcfg["TTS_PROVIDER"] = "kokoro"
    if engine == "chatterbox":
        free_ollama(rcfg)                     # casting is done: give the memory to the voices
    parts_dir, work = d / "parts", d / "work"
    parts_dir.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    out = d / "out"
    (out / "chapters").mkdir(parents=True, exist_ok=True)
    targets = list(range(len(p["chapters"]))) if not chapters else [c for c in chapters if 0 <= c < len(p["chapters"])]

    # 1) record every line not recorded yet (grouped by voice: switching voices is the slow part)
    plans = {ci: chapter_plan(p, s, ci) for ci in targets}
    todo, seen = [], set()
    for ci in targets:
        for item in plans[ci]:
            _, seg, voice, emo, _ = item
            k = part_key(engine, voice, emo, seg["text"], rcfg)
            if k not in seen and not sm.valid_media(parts_dir / f"{k}.flac"):
                seen.add(k)
                todo.append((voice, emo, seg["text"], ci))
    todo.sort(key=lambda x: (x[0], x[3]))
    total_chars = sum(len(x[2]) for x in todo) or 1
    done_chars, t0 = 0, time.time()
    for i, (voice, emo, text, ci) in enumerate(todo):
        eta = ""
        if done_chars > 400:
            secs = (time.time() - t0) / done_chars * (total_chars - done_chars)
            eta = f", about {int(secs // 3600)} h {int(secs % 3600 // 60)} min left" if secs > 3600 else \
                f", about {max(1, int(secs // 60))} min left"
        progress(0.85 * done_chars / total_chars, f"Recording line {i + 1} of {len(todo)}{eta}")
        render_part(rcfg, engine, voice, emo, text, parts_dir, work)
        done_chars += len(text)
    if engine == "chatterbox" and Chatterbox._inst:
        Chatterbox._inst.idle()

    # 2) mix and master each chapter (skipped when nothing in it changed)
    for n, ci in enumerate(targets):
        progress(0.85 + 0.13 * n / len(targets), f"Mixing chapter {ci + 1} of {len(p['chapters'])}")
        mix_chapter(cfg, rcfg, d, p, s, ci, plans[ci], engine)
        write_status(d, chapters_done=sorted(set(read_status(d).get("chapters_done", []) + [ci])))

    # 3) the whole book
    progress(0.98, "Putting the book together")
    build_book(cfg, d, p)
    for f in work.glob("*"):
        if f.is_file():
            f.unlink(missing_ok=True)
    progress(1, "Finished")


def mix_chapter(cfg, rcfg, d, p, s, ci, plan, engine):
    ch = p["chapters"][ci]
    sig = hashlib.sha1(json.dumps([[it[0], it[1]["text"], it[2], it[3], it[4]] for it in plan] +
                                  [sec.get("sound") for sec in ch["sections"]] +
                                  [engine, cfg.get("SOUNDSCAPES"), cfg.get("VOICE_POLISH"), cfg.get("AMBIENCE_VOLUME"),
                                   cfg.get("EFFECTS_VOLUME")], sort_keys=True).encode()).hexdigest()[:12]
    final = d / "out" / "chapters" / f"{ci + 1:03d}.flac"
    sigf = final.with_suffix(".sig")
    if sm.valid_media(final) and sigf.exists() and sigf.read_text() == sig:
        return final
    job = d / "work" / f"ch{ci + 1:03d}"
    job.mkdir(parents=True, exist_ok=True)
    parts_dir = d / "parts"
    # lay the lines end to end, with natural pauses, and note when each one is spoken
    files, t, gaps = [], 0.0, {}
    segments = [{"kind": "title", "index": None, "start": 0.0, "end": 0.0, "lines": []}]
    cur_si = None
    for si, seg, voice, emo, pause in plan:
        if si is not None and si != cur_si:
            segments[-1]["end"] = round(t, 3)
            segments.append({"kind": "scene", "index": si, "start": round(t, 3), "end": 0.0, "lines": []})
            cur_si = si
        f = parts_dir / f"{part_key(engine, voice, emo, seg['text'], rcfg)}.flac"
        dur = sm.duration(f)
        segments[-1]["lines"].append({"start": round(t, 3), "end": round(t + dur, 3),
                                      "speaker": seg["speaker"] or "Narrator", "text": seg["text"]})
        files.append(f)
        t += dur
        key = f"{pause:.2f}"
        if key not in gaps:
            gaps[key] = job / f"gap_{key}.flac"
            sm.silence(gaps[key], pause)
        files.append(gaps[key])
        t += pause
    segments[-1]["end"] = round(t, 3)
    timeline = {"segments": segments, "total": round(t, 3)}
    lst = job / "list.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    voice = job / "voice.wav"
    sm.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "1",
            str(voice)])
    story = {"title": p["title"], "scenes": [{"heading": ch["title"], "narration": sec["text"], "sound": sec.get("sound")}
                                             for sec in ch["sections"]]}
    bed = None
    if cfg["SOUNDSCAPES"].lower() == "on":
        bed, credits = sm.build_bed(rcfg, story, timeline, job, p["level"])
        if credits:
            cred = d / "out" / "credits.json"
            allc = json.loads(cred.read_text()) if cred.exists() else {}
            allc[str(ci)] = credits
            cred.write_text(json.dumps(allc, indent=1))
    out_wav = job / "final.wav"
    sm._PROG.clear()
    sm._PROG["cfg"] = rcfg                   # mix_and_master reads the voice-polish setting from here
    try:
        sm.mix_and_master(voice, bed, out_wav, job)
    finally:
        sm._PROG.clear()
    sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_wav), "-c:a", "flac", str(final)])
    sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(out_wav), "-c:a", "libmp3lame", "-b:a", "160k",
            str(final.with_suffix(".mp3"))])
    (d / "out" / "chapters" / f"{ci + 1:03d}.timeline.json").write_text(json.dumps(timeline))
    sigf.write_text(sig)
    shutil.rmtree(job, ignore_errors=True)
    return final


def build_book(cfg, d, p):
    """All finished chapters → story.mp3 and story.m4b with a chapter marker for each, the cover and credits."""
    out = d / "out"
    chs = [(ci, out / "chapters" / f"{ci + 1:03d}.flac") for ci in range(len(p["chapters"]))]
    chs = [(ci, f) for ci, f in chs if sm.valid_media(f)]
    if not chs:
        return
    lst = out / "book_list.txt"
    lst.write_text("".join(f"file '{f}'\n" for _, f in chs))
    full = out / "book.wav"
    sm.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), str(full)])
    starts, t = [], 0.0
    for _, f in chs:
        starts.append(t)
        t += sm.duration(f)
    mp3 = out / "story.mp3"
    sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)])
    timeline = {"segments": [{"start": 0.0}] + [{"start": s_} for s_ in starts]}
    story = {"title": p["title"], "logline": p.get("logline", ""),
             "scenes": [{"mark": p["chapters"][ci]["title"]} for ci, _ in chs]}
    if not (out / "cover.jpg").exists():
        try:
            sm.make_cover(writer_cfg(cfg, p["level"]), {"title": p["title"], "logline": p.get("logline", ""),
                                                         "visual_style": "", "characters": ""}, out, p["level"])
        except Exception as e:
            log(f"Cover skipped ({e})")
    sm.make_audiobook(story, mp3, timeline, out, "", 1)
    full.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    cred = out / "credits.json"
    if cred.exists():
        allc = json.loads(cred.read_text())
        seen = []
        for k in sorted(allc, key=int):
            for c in allc[k]:
                if c not in seen:
                    seen.append(c)
        (out / "sound-credits.txt").write_text("Sounds used in this book\n\n" + "\n".join(f"- {c}" for c in seen) + "\n")


def preview_line(cfg, pid, sec_id, seg_id, engine=None):
    """One line on its own, as it will sound, for checking a voice or an emotion."""
    d, p, s = load_project(cfg, pid)
    seg = next((g for g in s["sections"].get(sec_id, []) if g["id"] == seg_id), None)
    if not seg:
        raise ValueError("Unknown line.")
    engine = engine or p.get("engine") or cfg["STUDIO_ENGINE"]
    if engine == "chatterbox" and not Chatterbox.available():
        engine = "kokoro"
    rcfg = writer_cfg(cfg, p["level"])
    (d / "parts").mkdir(exist_ok=True)
    (d / "work").mkdir(exist_ok=True)
    f = render_part(rcfg, engine, voice_for(p, seg["speaker"] or "Narrator"), seg.get("emotion") or "neutral",
                    seg["text"], d / "parts", d / "work")
    mp3 = d / "work" / f"preview_{f.stem}.mp3"
    if not mp3.exists():
        sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-b:a", "128k", str(mp3)])
    return mp3


def voice_sample(cfg, voice, engine):
    """A sample of any voice in the library, with the chosen engine."""
    if engine == "chatterbox" and not Chatterbox.available():
        engine = "kokoro"
    folder = studio_dir(cfg) / "voices" / "samples"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{hashlib.sha1(f'{engine}|{voice}'.encode()).hexdigest()[:12]}.mp3"
    if not sm.valid_media(out):
        work = folder / "work"
        work.mkdir(exist_ok=True)
        f = render_part(cfg, engine, voice, "warm", sm.SAMPLE_TEXT, work, work)
        sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(f), "-b:a", "128k", str(out)])
        shutil.rmtree(work, ignore_errors=True)
    return out


# ───────────────────────────── edits from the page ─────────────────────────────

def update_cast(cfg, pid, cast_in, narrator=None):
    d, p, s = load_project(cfg, pid)
    by_name = {c["name"]: c for c in p["cast"]}
    lib = {v["id"] for v in voice_library(cfg)}
    for item in cast_in:
        c = by_name.get(item.get("name"))
        if not c:
            continue
        if item.get("gender") in ("female", "male"):
            if item["gender"] != c["gender"] and item.get("voice", c.get("voice")) == c.get("voice"):
                c["voice"] = ""                          # a voice of the other gender no longer fits
            c["gender"] = item["gender"]
        if item.get("age") in ("child", "teen", "adult", "elder"):
            if p["level"] == "explicit" and item["age"] in ("child", "teen"):
                raise ValueError("Explicit books are for adults only: every character must be Adult or Elder.")
            c["age"] = item["age"]
        if item.get("kind") in ("person", "creature"):
            c["kind"] = item["kind"]
        if "voice" in item and (item["voice"] in lib or str(item["voice"]).startswith("kokoro:")):
            c["voice"] = item["voice"]
        if "locked" in item:
            c["locked"] = bool(item["locked"])
        c.pop("review", None)
        c.pop("why", None)
        if item.get("rename") and item["rename"].strip() and item["rename"].strip() != c["name"]:
            new = item["rename"].strip()[:60]
            for segs in s["sections"].values():
                if isinstance(segs, list):
                    for g in segs:
                        if g["speaker"] == c["name"]:
                            g["speaker"] = new
            c["name"] = new
    if narrator and (narrator in lib or narrator.startswith("kokoro:")):
        p["narrator"] = narrator
    assign_voices(cfg, p)
    count_lines(p, s)
    save_project(d, p, s)
    return p


def update_script(cfg, pid, changes):
    """changes: [{"section": id, "id": seg_id, "speaker"?, "emotion"?, "text"?}]"""
    d, p, s = load_project(cfg, pid)
    names = {c["name"] for c in p["cast"]} | {"Narrator"}
    for ch in changes:
        segs = s["sections"].get(ch.get("section"))
        seg = next((g for g in segs or [] if g["id"] == ch.get("id")), None)
        if not seg:
            continue
        if ch.get("speaker") in names:
            seg["speaker"] = ch["speaker"]
        if ch.get("emotion") in EMOTIONS:
            seg["emotion"] = ch["emotion"]
        if isinstance(ch.get("text"), str) and ch["text"].strip():
            if p["level"] == "explicit":
                sm.check_adult_only(ch["text"])
            seg["text"] = sm.normalize_quotes(ch["text"].strip()).strip("“”")
    count_lines(p, s)
    save_project(d, p, s)


def update_sounds(cfg, pid, sounds, wishes=None):
    d, p, s = load_project(cfg, pid)
    by_id = {sec["id"]: sec for ch in p["chapters"] for sec in ch["sections"]}
    for sec_id, plan in (sounds or {}).items():
        sec = by_id.get(sec_id)
        if not sec or not isinstance(plan, dict):
            continue
        fx = [{"sound": str(f.get("sound", ""))[:60], "at": str(f.get("at", ""))[:120]}
              for f in plan.get("effects") or [] if isinstance(f, dict) and str(f.get("sound", "")).strip()][:8]
        sec["sound"] = {"ambience": str(plan.get("ambience") or "none")[:80] or "none", "effects": fx}
    if wishes is not None:
        p["sound_wishes"] = str(wishes)[:300]
    save_project(d, p)


def add_voice_clip(cfg, data, filename, name, gender, accent=""):
    """Your own voice clip (10–30 seconds of clear speech; only voices you have the right to use)."""
    ext = Path(filename).suffix.lower()
    if ext not in (".wav", ".mp3", ".m4a", ".flac"):
        raise ValueError("Use a .wav, .mp3, .m4a or .flac clip.")
    vdir = studio_dir(cfg) / "voices"
    safe = sm.slugify(name or Path(filename).stem)[:40] or "voice"
    tmp = vdir / f".upload{ext}"
    tmp.write_bytes(data)
    out = vdir / f"{safe}.wav"
    sm.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-t", "30", "-ac", "1", "-ar", "24000", str(out)])
    tmp.unlink(missing_ok=True)
    if sm.duration(out) < 5:
        out.unlink(missing_ok=True)
        raise ValueError("The clip needs at least 5 seconds of speech (10–30 is best).")
    meta_file = vdir / "voices.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    meta[out.name] = {"name": name or safe, "gender": gender if gender in ("female", "male") else "female",
                      "accent": accent[:30], "note": "your clip"}
    meta_file.write_text(json.dumps(meta, indent=1))
    return f"clip:{out.name}"


# ───────────────────────────── downloads and sending back to StoryMaker ─────────────────────────────

def recorded_chapters(d, p):
    """The chapters in the finished book, in order, with where each starts in it."""
    out, t = [], 0.0
    for ci in range(len(p["chapters"])):
        f = d / "out" / "chapters" / f"{ci + 1:03d}.flac"
        if sm.valid_media(f):
            dur = sm.duration(f)
            out.append({"index": ci, "start": t, "length": dur, "flac": f, "mp3": f.with_suffix(".mp3"),
                        "timeline": d / "out" / "chapters" / f"{ci + 1:03d}.timeline.json"})
            t += dur
    return out


def book_story(p, s):
    """The book as text, from the script as it's read (with your line changes), for the PDF."""
    scenes = []
    for ci, ch in enumerate(p["chapters"]):
        rest = re.sub(r"^\s*chapter\s+[\w-]+\s*[:.\-–—]\s*", "", ch["title"], flags=re.I).strip()
        for si, sec in enumerate(ch["sections"]):
            paras, cur, last = [], [], None
            for seg in s["sections"].get(sec["id"], []):
                if seg["kind"] == "heading":
                    continue
                if last is not None and seg["para"] != last and cur:
                    paras.append(" ".join(cur))
                    cur = []
                last = seg["para"]
                cur.append(f"“{seg['text']}”" if seg["kind"] == "dialogue" else seg["text"])
            if cur:
                paras.append(" ".join(cur))
            text = "\n\n".join(re.sub(r"\s+([,.!?;:])", r"\1", x) for x in paras)
            sc = {"narration": text, "chapter": ci + 1}
            if si == 0 and len(p["chapters"]) > 1:
                sc["chapter_title"] = rest or ch["title"]
            scenes.append(sc)
    cast = [{"name": c["name"], "voice": ga_type(c)} for c in p["cast"]]
    return {"title": p["title"], "logline": p.get("logline", ""), "cast": cast, "scenes": scenes}


def _newest(paths):
    return max((f.stat().st_mtime for f in paths if f.exists()), default=0)


def book_pdf(cfg, pid):
    d, p, s = load_project(cfg, pid)
    out = d / "out" / "book.pdf"
    if not out.exists() or out.stat().st_mtime < _newest([d / "project.json", d / "script.json"]):
        out.parent.mkdir(exist_ok=True)
        tmp = out.with_suffix(".tmp.pdf")
        sm.make_text_pdf(book_story(p, s), tmp)
        tmp.replace(out)
    return out


def safe_name(title):
    return re.sub(r"[^\w .,'()&-]", "", title.replace(": ", " - ")).strip()[:80] or "Audiobook"


def everything_zip(cfg, pid):
    """One zip: the audiobook (M4B), the MP3, every chapter, the book as a PDF, the cover and the credits."""
    d, p, _ = load_project(cfg, pid)
    out = d / "out"
    if not (out / "story.m4b").exists():
        raise ValueError("Make the audiobook first.")
    pdf = book_pdf(cfg, pid)
    chs = recorded_chapters(d, p)
    name = safe_name(p["title"])
    files = [(out / "story.m4b", f"{name}.m4b"), (out / "story.mp3", f"{name}.mp3"), (pdf, f"{name}.pdf"),
             (out / "cover.jpg", "Cover.jpg"), (out / "sound-credits.txt", "Sound credits.txt")]
    for n, c in enumerate(chs, 1):
        t = safe_name(p["chapters"][c["index"]]["title"])
        files.append((c["mp3"], f"Chapters/{n:02d} - {t}.mp3"))
    files = [(f, a) for f, a in files if f.exists()]
    z = out / "everything.zip"
    if z.exists() and z.stat().st_mtime >= _newest([f for f, _ in files]):
        return z
    tmp = out / "everything.tmp.zip"
    with zipfile.ZipFile(tmp, "w") as zf:
        for f, arc in files:
            kind = zipfile.ZIP_DEFLATED if f.suffix in (".txt", ".pdf") else zipfile.ZIP_STORED   # audio is already packed
            zf.write(f, f"{name}/{arc}", compress_type=kind)
    tmp.replace(z)
    return z


def send_to_storymaker(cfg, pid):
    """Put the finished audiobook into the StoryMaker story it came from: its MP3, M4B, subtitles and credits.
    StoryMaker's earlier audio is kept in a backup folder inside the story."""
    d, p, _ = load_project(cfg, pid)
    src = p.get("source") or {}
    if src.get("type") != "storymaker":
        raise ValueError("This book didn't come from StoryMaker.")
    out = d / "out"
    if not (out / "story.m4b").exists():
        raise ValueError("Make the audiobook first.")
    folder = Path(cfg["SM_LIBRARY"]) / src["id"]
    if not (folder / "result.json").exists():
        raise ValueError(f"Can't find that story in StoryMaker's library ({folder}). If your library is somewhere "
                         "else, set STORYMAKER_OUTPUT in the studio's studio.env.")
    result = json.loads((folder / "result.json").read_text())
    backup = folder / ".before-audio-studio"
    if not backup.exists():                                  # keep StoryMaker's own audio, once
        backup.mkdir()
        for name in ("story.mp3", "story.m4b", "story.srt", "story.vtt", "sound-credits.txt"):
            if (folder / name).exists():
                shutil.copy2(folder / name, backup / name)
    shutil.copy2(out / "story.mp3", folder / "story.mp3")
    shutil.copy2(out / "story.m4b", folder / "story.m4b")
    if (out / "sound-credits.txt").exists():
        shutil.copy2(out / "sound-credits.txt", folder / "sound-credits.txt")
    texts, spans = [], []                                    # subtitles timed to the new reading
    for c in recorded_chapters(d, p):
        if c["timeline"].exists():
            tl = json.loads(c["timeline"].read_text())
            for seg in tl["segments"]:
                for ln in seg.get("lines", []):
                    texts.append(ln["text"])
                    spans.append((c["start"] + ln["start"], max(0.3, ln["end"] - ln["start"])))
    if texts:
        sm.write_subtitles(texts, spans, folder / "story.srt", folder / "story.vtt")
    base = str(result.get("folder") or "").rstrip("/") or str(folder)
    result["audio_file"] = f"{base}/story.mp3"
    result["audio_outdated"] = False
    result["voiced_by"] = "Audio Studio" + (" (Chatterbox)" if (p.get("engine") or "") == "chatterbox" else "")
    result["studio_sent"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = folder / "result.json.tmp"
    tmp.write_text(json.dumps(result, indent=2))
    tmp.replace(folder / "result.json")
    p["sent_to_storymaker"] = result["studio_sent"]
    save_project(d, p)
    return {"ok": True, "story": src["id"], "had_video": bool(result.get("video_file"))}
