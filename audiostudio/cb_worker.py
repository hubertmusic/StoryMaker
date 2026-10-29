"""
Chatterbox in its own process. The studio starts it, sends it one line at a time, and replaces it with a fresh
one now and then: on a Mac, PyTorch keeps graphics memory for every new sentence length it sees and never gives
it back, so a long book would otherwise fill the whole Mac. A fresh process starts at zero.

Talks over its stdin/stdout, one JSON object per line. Anything else it prints goes to the studio's log.
"""
import gc
import json
import os
import sys
import traceback

proto = os.fdopen(os.dup(1), "w", buffering=1)      # the studio reads replies from here…
os.dup2(2, 1)                                       # …and anything printed by the libraries goes to the log
sys.stdout = sys.stderr


def reply(obj):
    proto.write(json.dumps(obj) + "\n")
    proto.flush()


def main():
    import numpy as np
    import soundfile as sf
    import torch

    init = json.loads(sys.stdin.readline() or "{}")
    dev = init.get("device", "auto")
    if dev == "auto":
        dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    turbo = init.get("model") == "turbo"
    try:
        import perth
        if getattr(perth, "PerthImplicitWatermarker", None) is None:
            raise ImportError
    except ImportError:
        reply({"ok": False, "error": "Chatterbox is missing a helper (setuptools). In Terminal run:  "
                                     "~/StoryMaker/audiostudio/venv/bin/pip install \"setuptools<81\"  "
                                     "then press the button again."})
        return
    if turbo:
        from chatterbox.tts_turbo import ChatterboxTurboTTS as M
    else:
        from chatterbox.tts import ChatterboxTTS as M
    model = M.from_pretrained(device=dev)
    sr = model.sr
    conds, current = {}, None

    def release():
        gc.collect()
        try:
            if dev == "mps":
                torch.mps.empty_cache()
            elif dev == "cuda":
                torch.cuda.empty_cache()
        except Exception:
            pass

    def used():
        try:
            return int(torch.mps.driver_allocated_memory()) if dev == "mps" else 0
        except Exception:
            return 0

    reply({"ok": True, "ready": True, "device": dev, "sr": sr})
    for line in sys.stdin:
        if not line.strip():
            continue
        job = json.loads(line)
        try:
            ref = job["ref"]
            if ref != current:
                if ref in conds:
                    model.conds = conds[ref]
                else:
                    if turbo:
                        model.prepare_conditionals(ref)
                    else:
                        model.prepare_conditionals(ref, exaggeration=job["ex"])
                    conds[ref] = model.conds
                current = ref
            pieces = []
            for chunk in job["chunks"]:
                with torch.inference_mode():
                    if turbo:
                        wav = model.generate(chunk, temperature=job["temp"])
                    else:
                        wav = model.generate(chunk, exaggeration=job["ex"], cfg_weight=job["cw"], temperature=job["temp"])
                    pieces.append(wav.squeeze(0).float().cpu().numpy())
                del wav
                release()
                pieces.append(np.zeros(int(sr * 0.12), dtype=np.float32))
            audio = np.concatenate(pieces[:-1]) if pieces else np.zeros(int(sr * 0.3), dtype=np.float32)
            sf.write(job["out"], audio, sr)
            reply({"ok": True, "mem": used()})
        except Exception as e:
            release()
            msg = str(e)
            traceback.print_exc()
            reply({"ok": False, "error": msg[:600], "oom": "out of memory" in msg.lower(), "mem": used()})


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        try:
            reply({"ok": False, "error": str(e)[:600], "fatal": True})
        except Exception:
            pass
