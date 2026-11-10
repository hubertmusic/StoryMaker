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
import tempfile
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

import requests

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

STUDIO_VERSION = "3.2 (Nov 9)"          # shown on the page and in the log, so you can tell which one is running

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
    "GUTENDEX_URL": "https://gutendex.com",       # search for free (public-domain) books from Project Gutenberg
    "ARCHIVE_DIR": "",                            # the NAS share as this computer sees it, e.g. /Volumes/StoryMaker
    "NAS_URL": "",                                # mount it when it isn't connected, e.g. smb://192.168.1.50/StoryMaker
    "DRAW_THINGS_ALWAYS_ON": "on",                # turn Draw Things' API Server back on whenever it goes off
    "DRAW_THINGS_REOPEN": "on",                   # …and open Draw Things again if it's been quit
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
    words = swapped = 0
    for ch in chapters:
        ch.pop("front_title", None)
    for ci, ch in enumerate(chapters):
        if level == "explicit":
            ch["title"], k = sm.adult_wording(ch.get("title", ""))
            swapped += k
        for si, sec in enumerate(ch["sections"]):
            sec["id"] = f"c{ci + 1:03d}s{si + 1:03d}"
            # line wraps inside a paragraph (plain-text books, pasted text) are undone; paragraphs stay apart
            sec["text"] = sm.normalize_quotes(re.sub(r"[ \t]*(?<!\n)\n(?!\n)[ \t]*", " ", sec["text"].strip()))
            if level == "explicit":                   # "young girl" → "young woman": everyone here is an adult
                sec["text"], k = sm.adult_wording(sec["text"])
                swapped += k
            words += len(sec["text"].split())
    p = {"id": pid, "title": title.strip()[:120] or "Untitled", "owner": owner, "level": level, "source": source,
         "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "engine": cfg["STUDIO_ENGINE"], "narrator": "",
         "cast": cast or [], "chapters": chapters, "words": words, "cast_done": False, "sounds_done": False}
    if level == "explicit":
        title_, k = sm.adult_wording(p["title"])
        p["title"], swapped = title_, swapped + k
        sm.check_adult_only(" ".join([p["title"]] + [ch.get("title", "") for ch in chapters] +
                                     [sec["text"] for ch in chapters for sec in ch["sections"]]), where="this book")
        if swapped:
            p["adult_wording"] = swapped
            log(f"\"{p['title']}\": changed “young girl/boy” to “young woman/man” in {swapped} "
                f"place{'s' if swapped != 1 else ''} (everyone in a grown-up book is an adult).")
    s = {"sections": {sec["id"]: segment_section(sec["text"]) for ch in chapters for sec in ch["sections"]}}
    d.mkdir(parents=True)                         # only once it passed the checks (no empty folder left behind)
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


PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s+of\s+\d{1,4})?\s*$", re.I)


def read_pdf(path):
    """A PDF's text as a book: page numbers and running headers/footers taken out, words hyphenated across lines
    joined, the line wraps inside paragraphs undone, and paragraphs kept apart (so speakers stay clear)."""
    import unicodedata
    from pypdf import PdfReader
    pages = []
    for pg in PdfReader(path).pages:
        t = unicodedata.normalize("NFKC", pg.extract_text() or "")       # ﬁ → fi, ﬂ → fl, odd spaces
        pages.append([ln.rstrip() for ln in t.replace("\r", "\n").split("\n")])
    # running headers and footers: the same top or bottom line (numbers aside) on many pages
    edge = {}
    for lines in pages:
        body = [ln for ln in lines if ln.strip()]
        for ln in body[:2] + body[-2:]:
            key = re.sub(r"\d+", "#", ln.strip().lower())
            edge[key] = edge.get(key, 0) + 1
    repeated = {k for k, n in edge.items() if n >= max(3, len(pages) * 0.3) and len(k) < 90}
    paras, cur = [], ""
    for lines in pages:
        body = [ln for ln in lines if ln.strip()]
        keep = []
        for k, ln in enumerate(body):
            at_edge = k < 2 or k >= len(body) - 2
            if at_edge and (PAGE_NUMBER.match(ln) or re.sub(r"\d+", "#", ln.strip().lower()) in repeated):
                continue
            keep.append(ln.strip())
        widths = sorted(len(ln) for ln in keep if len(ln) > 20)
        full = widths[len(widths) // 2] if widths else 60                    # a typical full line on this page
        for ln in keep:
            heading = len(ln) < 60 and (CHAPTER_LINE.match(ln) or (ln.isupper() and len(ln.split()) <= 8))
            if heading:
                if cur:
                    paras.append(cur)
                paras.append(ln)
                cur = ""
                continue
            if cur.endswith("-") and re.match(r"[a-z]", ln):
                cur = cur[:-1] + ln                                          # hyphen-\nated
            elif cur and re.match(r"^[“\"‘']", ln) and re.search(r"[.!?…”\"’]$", cur):
                paras.append(cur)                                            # a new line of dialogue
                cur = ln
            else:
                cur = (cur + " " + ln).strip()
            if len(ln) < full * 0.8 and re.search(r"[.!?…”\"’:]$", ln):
                paras.append(cur)                                            # a short last line ends a paragraph
                cur = ""
    if cur:
        paras.append(cur)
    return "\n\n".join(paras)


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


def book_cfg(cfg, p):
    """Settings for recording one book: its writer rules, its Chatterbox version and how much acting it wants."""
    c = writer_cfg(cfg, p["level"])
    if p.get("voice_model") in ("standard", "turbo"):
        c["CHATTERBOX_MODEL"] = p["voice_model"]
    c["_ACTING"] = float(p.get("acting") or 1.0)
    return c


def writer_cfg(cfg, level):
    c = dict(cfg)
    if level == "explicit":
        c["ADULT_CONTENT"] = "on"
        sm.adult_config(c)                   # the local writer only; nothing leaves the Mac
    return c


def _norm_name(n):
    return re.sub(r"\s+", " ", str(n or "")).strip()


def ask_patiently(cfg, prompt, system=None, progress=None, as_json=True):
    """The writer, with patience for free plans' "too many requests" limits (Groq, Gemini): wait and try again."""
    for attempt in range(6):
        try:
            return sm.ask_writer(cfg, prompt, as_json=as_json, system=system)
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


# ───────────────────────────── clean up the writing (optional) ─────────────────────────────

def tidy_text(wc, text, level, names=(), progress=None, pov=""):
    """StoryMaker's proofreader and sentence checker over one section. Never worse than before: each step is kept
    only if it keeps the length (and fixes problems), and an explicit book's text never gains youth words."""
    system = sm.EXPLICIT_SYSTEM if level == "explicit" else None
    old = text
    text = sm.tidy_sentences(text)
    if wc["STORY_PROVIDER"].lower() == "demo" or len(text.split()) < 40:
        return text
    say = lambda t: progress(t) if progress else None
    try:
        new = sm.clean_prose(ask_patiently(wc, sm.PROOF_PROMPT.format(text=text, extra=""), system, say, as_json=False))
        if 0.85 * len(text.split()) <= len(new.split()) <= 1.25 * len(text.split()):
            text = sm.tidy_sentences(new)
    except Exception as e:
        log(f"Proofreading skipped for a section ({str(e)[:100]})")
    issues = sm.sentence_issues(text, names, pov)
    for _ in range(2):
        if not issues:
            break
        listed = "\n".join(f"{k + 1}. \"{x[:300]}\": {why}" for k, (x, why) in enumerate(issues[:14]))
        try:
            new = sm.clean_prose(ask_patiently(wc, sm.SENTENCE_FIX_PROMPT.format(issues=listed, text=text, extra=""),
                                               system, say, as_json=False))
        except Exception as e:
            log(f"Sentence fixing skipped for a section ({str(e)[:100]})")
            break
        new = sm.tidy_sentences(new)
        left = sm.sentence_issues(new, names, pov)
        if not (0.85 * len(text.split()) <= len(new.split()) <= 1.2 * len(text.split())) or len(left) >= len(issues):
            break
        text, issues = new, left
    if level == "explicit":
        young = lambda t: bool(sm.MINOR_HARD.search(t) or sm.youth_mention(t) or sm.young_age(t))
        if young(text) and not young(old):
            return sm.tidy_sentences(old)
    return text


def run_tidy(cfg, pid, progress=lambda f, d: None):
    """Clean up the whole book's writing, section by section (saved as it goes, so it carries on after a stop).
    Changed sections are split into lines again and, if the book was already cast, cast again."""
    d, p, s = load_project(cfg, pid)
    wc = writer_cfg(cfg, p["level"])
    names = [c["name"] for c in p.get("cast") or []]
    sections = [sec for ch in p["chapters"] for sec in ch["sections"]]
    pov = sm.detect_pov([x["text"] for x in sections[:40]])
    before = after = changed = 0
    for i, sec in enumerate(sections):
        if s.setdefault("tidy", {}).get(sec["id"]):
            continue
        progress(i / max(1, len(sections)), f"Cleaning up the writing: section {i + 1} of {len(sections)}")
        found = len(sm.sentence_issues(sec["text"], names, pov))
        new = tidy_text(wc, sec["text"], p["level"], names,
                        lambda t: progress(i / max(1, len(sections)), t), pov)
        left = len(sm.sentence_issues(new, names, pov))
        better = not (found and left > found)
        before, after = before + found, after + (left if better else found)
        d, p, s = load_project(cfg, pid)                  # fresh copy: edits made meanwhile are kept
        sec = next(x for ch in p["chapters"] for x in ch["sections"] if x["id"] == sec["id"])
        if new.strip() != sec["text"].strip() and better:
            sec["text"] = sm.normalize_quotes(new.strip())
            s["sections"][sec["id"]] = segment_section(sec["text"])
            s["sections"].pop(sec["id"] + ":cast", None)
            changed += 1
        s.setdefault("tidy", {})[sec["id"]] = True
        save_project(d, p, s)
    d, p, s = load_project(cfg, pid)
    p["tidy_pending"] = False
    p["tidy_result"] = {"sections": len(sections), "changed": changed, "before": before, "after": after}
    p["words"] = sum(len(x["text"].split()) for ch in p["chapters"] for x in ch["sections"])
    count_lines(p, s)
    save_project(d, p, s)
    log(f"Cleaned up \"{p['title']}\": {changed} of {len(sections)} sections changed; problems {before} → {after}.")
    if changed and p.get("cast_done"):
        run_casting(cfg, pid, lambda f, t: progress(0.95, "Casting the changed sections again"))


def start_tidy(cfg, pid):
    """Mark a book for cleaning (again): every section is checked once more."""
    d, p, s = load_project(cfg, pid)
    s["tidy"] = {}
    p["tidy_pending"] = True
    save_project(d, p, s)


# ───────────────────────────── free books (Project Gutenberg, public domain) ─────────────────────────────

UA = {"User-Agent": "StoryMaker-AudioStudio/2.0 (personal audiobook maker)"}
BOOK_IDEAS_PROMPT = """Someone wants an audiobook of a book. They asked for: "{query}"
Using what you know about books, which book or books do they most likely mean? Give up to 5, best first.
For each, say whether it is in the public domain in the United States (published before 1930, or otherwise free),
so it would be on Project Gutenberg.
Reply with ONLY JSON: {{"books": [{{"title": "...", "author": "...", "year": 1900, "public_domain": true,
"note": "one short line about it"}}]}}"""


def _gutendex(cfg, params=None, path="/books/"):
    r = requests.get(cfg["GUTENDEX_URL"].rstrip("/") + path, params=params or {}, headers=UA, timeout=40)
    r.raise_for_status()
    return r.json()


def _book_card(b, note=""):
    fmts = b.get("formats") or {}
    return {"id": b["id"], "title": b.get("title", ""), "authors": ", ".join(
        re.sub(r"^([^,]+), (.+)$", r"\2 \1", a.get("name", "")) for a in b.get("authors") or []),
        "years": ", ".join(f"{a.get('birth_year') or '?'}–{a.get('death_year') or '?'}" for a in b.get("authors") or []),
        "languages": b.get("languages") or [], "downloads": b.get("download_count") or 0, "note": note,
        "readable": any(k.startswith("text/plain") or k.startswith("application/epub") for k in fmts)}


def find_books(cfg, query, level="family"):
    """Free books for a request: the request searched directly, and, for a description ("the one about a whale")
    or when nothing turns up, the writer's own knowledge of which book is meant, each looked up on Project
    Gutenberg. Books that aren't free are listed too, with why, so you can bring your own copy."""
    query = (query or "").strip()[:200]
    if not query:
        return {"results": [], "not_free": []}
    found, seen, not_free = [], set(), []

    def add(items, note=""):
        for b in items:
            if b["id"] not in seen and ("en" in (b.get("languages") or []) or not b.get("languages")):
                seen.add(b["id"])
                found.append(_book_card(b, note))
    try:
        add((_gutendex(cfg, {"search": query}).get("results") or [])[:8])
    except Exception as e:
        log(f"Book search failed ({str(e)[:120]})")
        raise ValueError("The free-book library (Project Gutenberg) isn't answering. Check the Mac's internet.")
    if len(query.split()) >= 4 or not found:
        wc = writer_cfg(cfg, "family")
        try:
            ideas = ask_patiently(wc, BOOK_IDEAS_PROMPT.format(query=query)).get("books") or []
        except Exception as e:
            log(f"The writer couldn't suggest books ({str(e)[:100]})")
            ideas = []
        for idea in ideas[:5]:
            t, a = str(idea.get("title", "")).strip(), str(idea.get("author", "")).strip()
            if not t:
                continue
            try:
                hits = _gutendex(cfg, {"search": f"{t} {a.split()[-1] if a else ''}".strip()}).get("results") or []
            except Exception:
                hits = []
            words = set(re.findall(r"[a-z]{3,}", t.lower()))
            hits = [h for h in hits if words & set(re.findall(r"[a-z]{3,}", h.get("title", "").lower()))]
            if hits:
                add(hits[:2], str(idea.get("note", ""))[:160])
            else:
                not_free.append({"title": t, "author": a, "year": idea.get("year"),
                                 "note": ("Not on Project Gutenberg: it's most likely still under copyright. "
                                          "If you own a copy (EPUB, PDF or text), bring it in as a file.")
                                 if not idea.get("public_domain") else
                                 "It should be free, but Project Gutenberg doesn't have it. Try another title."})
    found.sort(key=lambda b: (not b["readable"], -b["downloads"]))
    return {"results": found[:12], "not_free": not_free[:5]}


GUTENBERG_START = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG E(?:BOOK|TEXT)[^\n]*\*\*\*", re.I)
GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG E(?:BOOK|TEXT)[^\n]*\*\*\*", re.I)


def gutenberg_text(raw):
    """A Project Gutenberg plain-text book without its header, licence and footer, and without the
    transcriber's notes and the table of contents (they aren't read aloud)."""
    m1, m2 = GUTENBERG_START.search(raw), GUTENBERG_END.search(raw)
    text = raw[m1.end() if m1 else 0:m2.start() if m2 else len(raw)]
    text = text.replace("\r\n", "\n")
    text = re.sub(r"^\s*(?:Produced by|Transcribed from|E-text prepared by|This e-?book was produced)[^\n]*(?:\n[^\n]+)*",
                  "", text, flags=re.I | re.M)
    text = re.sub(r"\[Illustration[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r"(?<=\w)_(\w[^_\n]*)_|_(\w[^_\n]*)_", lambda m_: m_.group(1) or m_.group(2), text)  # _italics_
    lines = text.split("\n")
    # a table of contents: a run of short heading-like lines near the start; keep only the last time each appears
    heads = [i for i, ln in enumerate(lines[:800]) if CHAPTER_LINE.match(ln) and len(ln) < 90]
    if len(heads) >= 4:
        first_text = next((i for i in heads if any(len(x.split()) > 12 for x in lines[i + 1:i + 12])), None)
        if first_text and first_text > heads[0]:
            contents_end = first_text
            lines = lines[:heads[0]] + lines[contents_end:]
    return "\n".join(lines).strip()


def import_gutenberg(cfg, owner, book_id, level):
    """Bring in a free book from Project Gutenberg by its number."""
    b = _gutendex(cfg, path=f"/books/{int(book_id)}")
    fmts = b.get("formats") or {}
    url = next((fmts[k] for k in ("text/plain; charset=utf-8", "text/plain; charset=us-ascii", "text/plain")
                if k in fmts and not fmts[k].endswith(".zip")), None)
    card = _book_card(b)
    title = card["title"][:120]
    if url:
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        raw = r.content.decode("utf-8", "replace")
        text = gutenberg_text(raw)
        text, cast = extract_characters(text)
        chapters = split_chapters(text)
        for ch in chapters:
            ch.pop("front_title", None)
    else:
        epub = next((fmts[k] for k in fmts if k.startswith("application/epub")), None)
        if not epub:
            raise ValueError("That book has no text version to read.")
        r = requests.get(epub, headers=UA, timeout=120)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as f:
            f.write(r.content)
        try:
            _, chapters = read_epub(f.name)
        finally:
            os.unlink(f.name)
        chapters = [ch for ch in chapters if not re.search(r"project gutenberg", " ".join(
            x["text"][:400] for x in ch["sections"][:1]), re.I)]
        cast = []
    if not chapters:
        raise ValueError("No readable text was found in that book.")
    return new_project(cfg, owner, title, level, chapters,
                       {"type": "gutenberg", "id": int(book_id), "author": card["authors"],
                        "url": f"https://www.gutenberg.org/ebooks/{int(book_id)}"}, cast)


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
        if answer is not None and not isinstance(answer, dict):     # an answer in the wrong shape: the speech tags
            log("The writer's casting answer wasn't usable; using the speech tags instead.")
            answer = None
        if answer:
            chars = answer.get("characters") if isinstance(answer.get("characters"), list) else []
            for ch_ in chars:
                if isinstance(ch_, str):
                    ch_ = {"name": ch_}
                if not isinstance(ch_, dict):
                    continue
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
            quotes = answer.get("quotes") if isinstance(answer.get("quotes"), dict) else {}
            for key, seg in qmap.items():
                info = quotes.get(key) or {}
                if isinstance(info, str):
                    info = {"speaker": info}
                if not isinstance(info, dict):
                    info = {}
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
    out = [{"id": f"kokoro:{v[0]}", "name": v[1], "gender": v[2], "accent": v[3], "note": v[4], "own": False,
            "group": sm.voice_group(v)} for v in sm.KOKORO_CATALOGUE]
    meta_file = studio_dir(cfg) / "voices" / "voices.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    for f in sorted((studio_dir(cfg) / "voices").glob("*")):
        if f.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac") and not f.name.startswith("auto_"):
            m = meta.get(f.name, {})
            out.append({"id": f"clip:{f.name}", "name": m.get("name") or f.stem.replace("-", " ").replace("_", " ").title(),
                        "gender": m.get("gender", "female"), "accent": m.get("accent", ""), "note": m.get("note", "your clip"),
                        "own": True, "group": "own"})
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
    # more feeling than Chatterbox's defaults: higher exaggeration, and a lower cfg_weight for a natural pace
    "neutral": (0.55, 0.45, 0.8, 1.0), "warm": (0.62, 0.42, 0.8, 0.97), "happy": (0.78, 0.38, 0.82, 1.04),
    "excited": (0.95, 0.3, 0.85, 1.08), "playful": (0.85, 0.33, 0.85, 1.04), "tender": (0.68, 0.38, 0.75, 0.94),
    "sad": (0.7, 0.42, 0.75, 0.9), "fearful": (0.88, 0.32, 0.85, 1.05), "tense": (0.8, 0.33, 0.8, 1.02),
    "angry": (1.05, 0.28, 0.85, 1.06), "whisper": (0.45, 0.5, 0.7, 0.9), "seductive": (0.82, 0.28, 0.8, 0.9),
    "breathless": (0.95, 0.28, 0.85, 1.0),
}


class Chatterbox:
    """Chatterbox, run in its own process (cb_worker.py) so its graphics memory can be handed back in full:
    the process is replaced with a fresh one whenever it has built up too much, or if it ever runs out."""
    _inst = None
    _lock = threading.Lock()
    _avail = None
    aborting = False                   # Stop pressed: the line being recorded is abandoned straight away

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
        model = self.model_name = getattr(self, "want_model", None) or self.cfg.get("CHATTERBOX_MODEL", "standard")
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

    def say(self, text, ref, emotion, out_wav, max_chars=260, model=None, acting=1.0):
        ex, cw, temp, _ = EMOTION_STYLE.get(emotion, EMOTION_STYLE["neutral"])
        acting = max(0.5, min(1.8, float(acting or 1.0)))     # the book's "acting" setting: calmer ↔ more dramatic
        ex, cw = min(2.0, ex * acting), max(0.15, cw / acting ** 0.5)
        if model and model != getattr(self, "model_name", None):
            with self._gen:                                    # this book wants the other Chatterbox version
                self.stop()
            self.want_model = model
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
                if r.get("died") and Chatterbox.aborting:       # Stop was pressed: it was ended on purpose
                    self.proc = None
                    raise StopJob()
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
    acting = float(cfg.get("_ACTING") or 1.0)
    if engine == "chatterbox" and abs(acting - 1.0) > 0.01:
        extra = f"{extra}|a{acting:.2f}"
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
        Chatterbox.get(cfg).say(sm.tts_clean(text), reference_clip(cfg, voice), emotion, raw, int(cfg["STUDIO_MAX_LINE_CHARS"]),
                                model=cfg.get("CHATTERBOX_MODEL", "standard"), acting=cfg.get("_ACTING") or 1.0)
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


JOB_KINDS = ("cast", "sounds", "record", "all", "tidy", "video")


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
        self.pause_flags = {}
        self.current = None
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, pid, kind, **kw):
        d = project_dir(self.cfg, pid)
        with self.cv:
            waiting = next((j for j in self.queue if j[0] == pid), None)
            if waiting and waiting[1] == "tidy":       # cleaning is done first by any job anyway
                self.queue.remove(waiting)
            elif waiting:
                raise ValueError("This book already has a job waiting.")
            self.queue.append((pid, kind, kw))
            self.pause_flags.pop(pid, None)
            write_status(d, state="queued", job=kind, progress=0, detail="Waiting its turn", error=None, resume=None)
            self.cv.notify()

    def pause(self, pid):
        """Pause this book's job so another book gets the studio now; Resume carries on where it stopped (what's
        recorded is kept). A waiting job is paused straight away; a running one as soon as it can stop."""
        d = project_dir(self.cfg, pid)
        with self.cv:
            waiting = next((j for j in self.queue if j[0] == pid), None)
            if waiting:
                self.queue.remove(waiting)
                write_status(d, state="paused", detail="Paused before it started. Resume when you're ready.",
                             resume={"kind": waiting[1], "kw": waiting[2]})
                return "paused"
        if self.current == pid:
            self.pause_flags[pid] = True
            self.stop(pid, pausing=True)
            return "pausing"
        return "idle"

    def stop(self, pid, pausing=False):
        """Stop this book's job: a waiting one is taken off the list; a running one stops at once if it's
        recording with Chatterbox (the line being read is abandoned), otherwise at the end of the step it's on."""
        if not pausing:
            self.pause_flags.pop(pid, None)
        self.stop_flags[pid] = True
        with self.cv:
            self.queue = [j for j in self.queue if j[0] != pid]
        if self.current == pid:
            cb = Chatterbox._inst
            if cb and cb.proc:
                Chatterbox.aborting = True
                try:
                    cb.proc.kill()
                except Exception:
                    pass

    def _loop(self):
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                pid, kind, kw = self.queue.pop(0)
            try:
                self._one(pid, kind, kw)
            except Exception:                       # never let one job's trouble stop every job after it
                traceback.print_exc()
            finally:
                self.current = None
                Chatterbox.aborting = False

    def _one(self, pid, kind, kw):
        self.stop_flags[pid] = False
        self.current = pid
        Chatterbox.aborting = False
        try:
            d = project_dir(self.cfg, pid)
        except ValueError:
            return
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
                self._stopped(d, pid, kind, kw)
                break
            except Exception as e:
                if self.stop_flags.get(pid):            # it broke because it was being stopped
                    self._stopped(d, pid, kind, kw)
                    break
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

    def _stopped(self, d, pid, kind, kw):
        if self.pause_flags.pop(pid, None):
            write_status(d, state="paused", detail="Paused. What's done is kept: Resume carries on from here.",
                         resume={"kind": kind, "kw": kw})
        else:
            write_status(d, state="stopped", detail="Stopped. Start again to carry on where it left off.")

    def _run(self, d, pid, kind, kw, progress):
        write_status(d, state="running", job=kind, progress=0, detail="Starting", error=None)
        _, p, _ = load_project(self.cfg, pid)
        if p.get("tidy_pending"):                  # the writing is cleaned up first, before anything is cast or read
            run_tidy(self.cfg, pid, lambda f, t: progress(f, t) if kind == "tidy" else progress(0, t))
        if kind == "tidy":
            pass
        elif kind == "cast":
            run_casting(self.cfg, pid, progress, kw.get("only"))
        elif kind == "sounds":
            run_sounds(self.cfg, pid, progress, kw.get("redo", False))
        elif kind == "record":
            run_record(self.cfg, pid, progress, kw.get("chapters"))
        elif kind == "video":
            run_video(self.cfg, pid, progress)
        elif kind == "all":
            _, p, _ = load_project(self.cfg, pid)
            if not p.get("cast_done"):
                run_casting(self.cfg, pid, lambda f, t: progress(f * 0.1, t))
            sounds = self.cfg["SOUNDSCAPES"].lower() == "on" and p.get("soundscapes", "on") == "on"
            if not p.get("sounds_done") and sounds:
                run_sounds(self.cfg, pid, lambda f, t: progress(0.1 + f * 0.05, t))
            if kw.get("video"):
                run_record(self.cfg, pid, lambda f, t, **x: progress(0.15 + f * 0.6, t, **x))
                write_status(d, state="running", job="video", progress=75, detail="Drawing the pictures", error=None)
                run_video(self.cfg, pid, lambda f, t, **x: progress(0.75 + f * 0.25, t))
            else:
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
    rcfg = book_cfg(cfg, p)
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
                                  [engine, cfg.get("SOUNDSCAPES"), p.get("soundscapes", "on"), cfg.get("VOICE_POLISH"),
                                   cfg.get("AMBIENCE_VOLUME"),
                                   cfg.get("EFFECTS_VOLUME"), p.get("music", "on"), cfg.get("MUSIC_VOLUME"),
                                   cfg.get("MUSIC_MODE"), "bed3"], sort_keys=True).encode()).hexdigest()[:12]
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
    bed, credits = None, []
    music = None
    if p.get("music", "on") == "on":       # the same track for the whole book, chosen by its mood
        try:
            music = sm.pick_music(rcfg, p.get("genre", ""), p.get("music_choice"), p["id"], p["level"])
        except Exception as e:
            log(f"No music ({str(e)[:100]})")
    with_sounds = cfg["SOUNDSCAPES"].lower() == "on" and p.get("soundscapes", "on") == "on"
    if with_sounds or music:
        bed, credits = sm.build_bed(rcfg, story, timeline, job, p["level"], music, with_sounds=with_sounds)
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
                                                         "visual_style": "", "picture_style": sm.picture_style(p),
                                                         "characters": (p.get("look") or {}).get("characters", "")},
                          out, p["level"])
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
    rcfg = book_cfg(cfg, p)
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
            text = ch["text"]
            if p["level"] == "explicit":
                text = sm.adult_wording(text)[0]
                sm.check_adult_only(text, where="that line")
            seg["text"] = sm.normalize_quotes(text.strip()).strip("“”")
    count_lines(p, s)
    save_project(d, p, s)


def update_sounds(cfg, pid, sounds, wishes=None):
    d, p, s = load_project(cfg, pid)
    by_id = {sec["id"]: sec for ch in p["chapters"] for sec in ch["sections"]}
    for sec_id, plan in (sounds or {}).items():
        sec = by_id.get(sec_id)
        if not sec or not isinstance(plan, dict):
            continue
        sec["sound"] = sm.clean_sound_plan(plan, sec.get("sound"))
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
             (out / "cover.jpg", "Cover.jpg"), (out / "sound-credits.txt", "Sound credits.txt"),
             (out / "story.mp4", f"{name} (video).mp4"), (out / "story.srt", f"{name} (video).srt"),
             (out / "picture-book.pdf", f"{name} (picture book).pdf")]
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


def book_timeline(d, p, work):
    """The whole recorded book as one sound file and one timeline with a segment per section, in StoryMaker's
    shape: {"segments": [{"kind": "title"|"scene", "index", "start", "end", "lines"}], "total"}. A chapter's
    heading belongs to its first section (the book's first heading to the title). None if not all recorded."""
    chs = recorded_chapters(d, p)
    if len(chs) != len(p["chapters"]):
        return None, None
    work.mkdir(parents=True, exist_ok=True)
    full = work / "book.wav"
    (work / "list.txt").write_text("".join(f"file '{c['flac']}'\n" for c in chs))
    sm.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"),
            "-ar", "44100", "-ac", "2", str(full)])
    segs, k = [], 0
    shift = lambda lns, off: [dict(ln, start=round(off + ln["start"], 3), end=round(off + ln["end"], 3)) for ln in lns]
    for c in chs:
        tl = json.loads(c["timeline"].read_text())
        off = c["start"]
        head = next((g for g in tl["segments"] if g["kind"] == "title"), None)
        scenes = [g for g in tl["segments"] if g["kind"] == "scene"]
        for j, g in enumerate(scenes):
            start, lines = off + g["start"], shift(g["lines"], off)
            if j == 0 and head:
                if not segs:
                    segs.append({"kind": "title", "index": None, "start": round(off, 3), "end": round(start, 3),
                                 "lines": shift(head["lines"], off)})
                else:
                    start, lines = off, shift(head["lines"], off) + lines
            segs.append({"kind": "scene", "index": k, "start": round(start, 3), "end": round(off + g["end"], 3),
                         "lines": lines})
            k += 1
        if segs:
            segs[-1]["end"] = round(off + c["length"], 3)        # the pause at the chapter's end, too
    if not segs or segs[0]["kind"] != "title":
        segs.insert(0, {"kind": "title", "index": None, "start": 0.0, "end": 0.0, "lines": []})
    return full, {"segments": segs, "total": round(sum(c["length"] for c in chs), 3)}


def book_scenes(p):
    return [sec for ch in p["chapters"] for sec in ch["sections"]]


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
    video = ""
    if result.get("video_file") and (folder / "story.mp4").exists():
        video = remake_storymaker_video(cfg, d, p, folder)
    p["sent_to_storymaker"] = result["studio_sent"]
    save_project(d, p)
    return {"ok": True, "story": src["id"], "had_video": bool(result.get("video_file")), "video": video}


def remake_storymaker_video(cfg, d, p, folder):
    """The StoryMaker story's video made again around this recording: the same pictures (or clips), each scene's
    pictures lasting exactly as long as that scene is read now, and subtitles to match. Nothing is drawn again."""
    story = json.loads((folder / "story.json").read_text())
    secs = book_scenes(p)
    if len(secs) != len(story.get("scenes") or []):
        return ("not remade: the book here has a different number of sections than the story in StoryMaker, so the "
                "pictures can't be matched to them")
    for sc, sec in zip(story["scenes"], secs):            # captions and subtitles say what's read now
        sc["narration"] = sec["text"]
    work = d / "work" / "sm-video"
    try:
        full, timeline = book_timeline(d, p, work)
        if not timeline:
            return "not remade: record every chapter first"
        sm.rebuild_video(writer_cfg(cfg, p["level"]), folder, story, full, timeline, work / "job")
        result = json.loads((folder / "result.json").read_text())
        result["video_remade_by_studio"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (folder / "result.json").write_text(json.dumps(result, indent=2))
        return "remade"
    except Exception as e:
        log(f"Couldn't remake the StoryMaker video ({e})")
        return f"not remade ({str(e)[:120]})"
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ───────────────────────────── pictures and a video for any book ─────────────────────────────

LOOK_PROMPT = """You are the art director for an illustrated audiobook of "{title}".
Here is how it begins:
---
{opening}
---
Characters: {cast}
Decide one consistent look for every picture. Reply with ONLY JSON:
{{"visual_style": "one line: medium, palette, lighting, mood", "characters": "each main character's fixed look in
a few words (age as an adult or child, hair, build, clothes), separated by semicolons"}}"""

SHOTS_PROMPT = """You are the illustrator of "{title}" ({style}). For each numbered section below, describe ONE illustration
of its most dramatic or telling moment: which characters are in it (by name), exactly what each is doing (a clear
action, mid-movement), their expressions, where they are, and the light, in one sentence of 25 to 45 words. Only
things a picture can show; no words or signs in the picture. {extra}
Reply with ONLY JSON: {{"pictures": ["...", "..."]}} with exactly {n} entries, in order.

{sections}"""


def plan_pictures(cfg, pid, progress=lambda f, t: None):
    """A look for the whole book and a picture idea for every section (asked once, then kept). A book from
    StoryMaker keeps StoryMaker's own look and picture ideas."""
    d, p, s = load_project(cfg, pid)
    wc = writer_cfg(cfg, p["level"])
    secs = book_scenes(p)
    src = p.get("source") or {}
    sm_story = None
    if src.get("type") == "storymaker":
        f = Path(cfg["SM_LIBRARY"]) / src["id"] / "story.json"
        if f.exists():
            sm_story = json.loads(f.read_text())
            if len(sm_story.get("scenes") or []) != len(secs):
                sm_story = None
    if sm_story and not p.get("look"):
        p["look"] = {"visual_style": sm_story.get("visual_style", ""), "characters": sm_story.get("characters", "")}
        for sec, sc in zip(secs, sm_story["scenes"]):
            sec.setdefault("image_prompt", sc.get("image_prompt") or "")
        save_project(d, p)
    if not p.get("look"):
        progress(0.01, "Choosing the look of the pictures")
        opening = " ".join(sec["text"] for sec in secs[:3])[:3500]
        cast = "; ".join(f"{c['name']} ({c.get('gender', '')}, {c.get('age', '')})" for c in (p.get("cast") or [])[:12])
        try:
            look = ask_patiently(wc, LOOK_PROMPT.format(title=p["title"], opening=opening, cast=cast or "unknown"))
        except Exception as e:
            log(f"The writer couldn't choose a look ({str(e)[:100]})")
            look = {}
        p["look"] = {"visual_style": str(look.get("visual_style") or sm.DEFAULT_LOOK)[:300],
                     "characters": str(look.get("characters") or "")[:700]}
        save_project(d, p)
    extra = ("Grown-up story: everyone is an adult and fully clothed; never anything sexual." if p["level"] in
             ("mature", "explicit") else "")
    todo = [i for i, sec in enumerate(secs) if not sec.get("image_prompt")]
    for n0 in range(0, len(todo), 8):                       # eight sections per question
        batch = todo[n0:n0 + 8]
        progress(0.02 + 0.08 * n0 / max(1, len(todo)), f"Planning the pictures: {n0 + len(batch)} of {len(todo)} sections")
        listed = "\n\n".join(f"{k + 1}. {secs[i]['text'][:900]}" for k, i in enumerate(batch))
        try:
            got = ask_patiently(wc, SHOTS_PROMPT.format(title=p["title"], style=p["look"]["visual_style"], extra=extra,
                                                        n=len(batch), sections=listed)).get("pictures") or []
        except Exception as e:
            log(f"Picture ideas skipped for a few sections ({str(e)[:100]})")
            got = []
        d, p, s = load_project(cfg, pid)
        secs = book_scenes(p)
        for k, i in enumerate(batch):
            idea = str(got[k]).strip() if k < len(got) else ""
            if not idea:                                       # the writer missed one: a calm sentence from the text
                calm = [x for x in sm.split_sentences(secs[i]["text"]) if "“" not in x and len(x.split()) >= 6]
                idea = calm[0] if calm else secs[i]["text"][:200]
            secs[i]["image_prompt"] = idea[:400]
        save_project(d, p)
    return p


def run_video(cfg, pid, progress=lambda f, t: None):
    """Pictures for every section (drawn by the picture app on this Mac, or OpenAI or Gemini if set up) and a video
    of the whole audiobook with them: each section's pictures last exactly as long as it's read, long sections get
    more than one, and the video has subtitles. Also a picture book (PDF). A StoryMaker book reuses its pictures."""
    d, p, s = load_project(cfg, pid)
    rcfg = writer_cfg(cfg, p["level"])
    provider = sm.pick_image_provider(rcfg)
    if provider == "cards":
        raise ValueError("Pictures need a picture app: open Draw Things, turn on Settings → API Server (HTTP, port "
                         "7860) and set LOCAL_IMAGE_URL in storymaker.env (or an OpenAI or Gemini key).")
    if len(recorded_chapters(d, p)) != len(p["chapters"]):
        raise ValueError("Record the whole book first: the pictures are timed to the reading.")
    p = plan_pictures(cfg, pid, progress)
    secs = book_scenes(p)
    style = sm.picture_style(p)
    story = {"title": p["title"], "level": p["level"], "logline": p.get("logline", ""), "picture_style": style,
             "visual_style": p["look"]["visual_style"], "characters": p["look"]["characters"],
             "user_style": p.get("user_style") or {}, "cast": [{"name": c["name"]} for c in p.get("cast") or []],
             "scenes": [{"heading": next(ch["title"] for ch in p["chapters"] if sec in ch["sections"]),
                         "narration": sec["text"], "image_prompt": sec.get("image_prompt", ""), "shot_planned": True}
                        for sec in secs]}
    pics_dir = d / "pictures"
    if p.get("pictures_style", "storybook") != style:         # a new look: every picture is drawn again
        shutil.rmtree(pics_dir, ignore_errors=True)
        (d / "out" / "cover.jpg").unlink(missing_ok=True)
    pics_dir.mkdir(exist_ok=True)
    src = p.get("source") or {}
    sm_style = "storybook"
    if src.get("type") == "storymaker":
        try:
            sm_style = sm.picture_style(json.loads((Path(cfg["SM_LIBRARY"]) / src["id"] / "story.json").read_text()))
        except (OSError, ValueError):
            pass
    if src.get("type") == "storymaker" and sm_style == style:   # StoryMaker's own pictures, if they're in this look
        folder = Path(cfg["SM_LIBRARY"]) / src["id"]
        for f_ in folder.glob("scene_*.png"):
            if not (pics_dir / f_.name.replace("scene_", "pic_")).exists():
                shutil.copy(f_, pics_dir / f_.name.replace("scene_", "pic_"))
    work = d / "work" / "video"
    shutil.rmtree(work, ignore_errors=True)
    old_report = sm.report

    def report(step, frac=0.0, detail=None, image=None, **extra):
        if detail:
            progress(0.1 + 0.85 * max(0.0, min(1.0, frac)), detail)
    sm.report = report                                     # StoryMaker's progress lines show in the studio
    try:
        full, timeline = book_timeline(d, p, work)
        slices = sm.slice_segments(full, timeline, work)
        tmp = work / "build"
        tmp.mkdir(parents=True, exist_ok=True)
        _, pics, seg_lens = sm.make_video(rcfg, story, slices, pics_dir, tmp, 1920, 1080, False,
                                          tmp / "story.mp4", "slideshow", None)
        out = d / "out"
        shutil.copy(tmp / "story.mp4", out / "story.mp4")
        texts, spans = sm.subtitle_lines(timeline, seg_lens, p["title"])   # line by line, on the video's clock
        if texts:
            sm.write_subtitles(texts, spans, out / "story.srt", out / "story.vtt")
            sm.burn_subtitles(rcfg, out / "story.mp4", out / "story.srt")
        progress(0.97, "Laying out the picture book")
        try:
            imgs = [pics.get(i) or pics_dir / f"pic_{i + 1:02d}.png" for i in range(len(secs))]
            sm.make_book(story, imgs, out / "cover.jpg" if (out / "cover.jpg").exists() else None,
                         out / "picture-book.pdf")
        except Exception as e:
            log(f"Picture book skipped ({e})")
    finally:
        sm.report = old_report
        shutil.rmtree(work, ignore_errors=True)
    d, p, s = load_project(cfg, pid)
    p["video_made"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p["pictures_style"] = style
    save_project(d, p)
