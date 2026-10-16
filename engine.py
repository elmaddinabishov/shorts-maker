"""
Video engine: script text -> YouTube video (Shorts 1080x1920 or Long 1920x1080).
Voiceover: edge-tts (free). Footage: Pixabay API (free key). Montage: MoviePy + bundled FFmpeg.
"""
import asyncio
import re
import tempfile
from pathlib import Path

import requests

FPS = 30
SHORTS_MAX_SEC = 59.0
WORDS_PER_SEC = 2.6  # typical narration pace at +0%

FORMATS = {
    "shorts": dict(size=(1080, 1920), font_size=95, stroke=7, cap_y=0.62, words=3, upper=True,
                   margin=160, max_sec=SHORTS_MAX_SEC),
    "long": dict(size=(1920, 1080), font_size=62, stroke=5, cap_y=0.80, words=6, upper=False,
                 margin=320, max_sec=None),
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

VOICES = {
    "Andrew (US, kişi)": "en-US-AndrewNeural",
    "Guy (US, kişi)": "en-US-GuyNeural",
    "Christopher (US, kişi)": "en-US-ChristopherNeural",
    "Jenny (US, qadın)": "en-US-JennyNeural",
    "Aria (US, qadın)": "en-US-AriaNeural",
    "Ryan (UK, kişi)": "en-GB-RyanNeural",
    "Sonia (UK, qadın)": "en-GB-SoniaNeural",
}
RATES = ["-10%", "+0%", "+5%", "+10%", "+20%"]

STOPWORDS = set("""
a an the and or but if of to in on at for with from by as is are was were be been being
it its this that these those you your we our they their them he she his her i me my
do does did have has had not no so than then there here what which who whom how why when
where can could will would should may might just also very more most some any all each
about into over after before up down out off again only own same too now know like
didn while because even ever every still much many make makes made let lets get gets
follow part instead rest through
""".split())


class ScriptTooLong(RuntimeError):
    pass


def find_font():
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    raise RuntimeError("Şrift tapılmadı (Arial Bold).")


# ---------- Script -> scenes ----------
def auto_keywords(text):
    words = re.findall(r"[a-z]+", text.lower())
    picked = []
    for w in words:
        if w not in STOPWORDS and len(w) > 3 and w not in picked:
            picked.append(w)
    best = sorted(picked, key=len, reverse=True)[:2]
    best.sort(key=picked.index)
    return " ".join(best) or "abstract background"


def parse_scenes(raw):
    """Paragraphs separated by blank lines. A paragraph may start with [keywords]."""
    scenes = []
    for part in re.split(r"\n\s*\n", raw):
        part = re.sub(r"\s+", " ", part).strip()
        if not part:
            continue
        m = re.match(r"\[([^\]]+)\]\s*(.*)", part)
        text, kw = (m.group(2).strip(), m.group(1).strip()) if m else (part, auto_keywords(part))
        if text:
            scenes.append((text, kw))
    return scenes


def word_count(raw):
    return sum(len(t.split()) for t, _ in parse_scenes(raw))


def estimate_seconds(raw, rate="+0%"):
    try:
        factor = 1 + int(rate.strip("%")) / 100
    except ValueError:
        factor = 1
    return word_count(raw) / (WORDS_PER_SEC * factor)


# ---------- Voiceover ----------
async def _tts(text, mp3_path, voice, rate):
    import edge_tts

    try:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, voice, rate=rate)
    words = []
    with open(mp3_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
    return words


def tts(text, mp3_path, voice, rate):
    return asyncio.run(_tts(text, mp3_path, voice, rate))


# ---------- Stock video (Pixabay) ----------
def _download(url, dest):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for part in r.iter_content(1 << 16):
                f.write(part)


def _pick_file(hit):
    vids = hit.get("videos", {})
    for size in ("medium", "large", "small"):
        f = vids.get(size) or {}
        if f.get("url"):
            return f
    return None


def fetch_video(query, dest, used_ids, api_key, vertical):
    words = query.split()
    for q in dict.fromkeys([query, " ".join(words[:2]), words[0] if words else query]):
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": api_key, "q": q[:100], "per_page": 20, "safesearch": "true"},
            timeout=30,
        )
        if r.status_code in (400, 401, 403) and "key" in r.text.lower():
            raise RuntimeError("Pixabay açarı yanlışdır. Ayarlarda açarı yoxlayın.")
        if r.status_code == 429:
            raise RuntimeError("Pixabay limiti doldu. Bir dəqiqə sonra yenidən cəhd edin.")
        r.raise_for_status()
        hits = []
        for h in r.json().get("hits", []):
            f = _pick_file(h)
            if f and h.get("id") not in used_ids:
                is_vertical = f.get("height", 0) >= f.get("width", 1)
                hits.append((0 if is_vertical == vertical else 1, h, f))
        hits.sort(key=lambda x: x[0])  # matching orientation first
        if hits:
            _, h, f = hits[0]
            used_ids.add(h["id"])
            _download(f["url"], dest)
            return True
    return False


# ---------- Captions + montage ----------
def _fit(clip, size):
    w, h = size
    scale = max(w / clip.w, h / clip.h)
    clip = clip.resized(scale)
    return clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=w, height=h)


def _even_timings(text, dur):
    ws = text.split()
    step = dur / max(len(ws), 1)
    return [(i * step, (i + 1) * step, w) for i, w in enumerate(ws)]


def _captions(words, dur, font, fmt):
    from moviepy import TextClip

    w, h = fmt["size"]
    n = fmt["words"]
    clips = []
    groups = [words[i:i + n] for i in range(0, len(words), n)]
    for i, g in enumerate(groups):
        start = g[0][0]
        end = groups[i + 1][0][0] if i + 1 < len(groups) else dur
        text = " ".join(x[2] for x in g)
        tc = TextClip(
            font=font, text=text.upper() if fmt["upper"] else text, font_size=fmt["font_size"],
            color="white", stroke_color="black", stroke_width=fmt["stroke"],
            method="caption", size=(w - fmt["margin"], None), text_align="center", margin=(20, 40),
        )
        clips.append(
            tc.with_start(start).with_duration(max(0.05, end - start))
            .with_position(("center", int(h * fmt["cap_y"]) - tc.h // 2))
        )
    return clips


def make_scene(video_path, audio, words, text, font, fmt):
    from moviepy import ColorClip, CompositeVideoClip, VideoFileClip, vfx

    size = fmt["size"]
    dur = audio.duration + 0.15
    if video_path:
        bg = _fit(VideoFileClip(str(video_path), audio=False), size)
        bg = bg.with_effects([vfx.Loop(duration=dur)]) if bg.duration < dur else bg.subclipped(0, dur)
    else:
        bg = ColorClip(size, color=(20, 20, 20), duration=dur)
    caps = _captions(words or _even_timings(text, audio.duration), dur, font, fmt)
    return CompositeVideoClip([bg, *caps], size=size).with_duration(dur).with_audio(audio)


def _render_logger(progress, start, span):
    from proglog import ProgressBarLogger

    class _L(ProgressBarLogger):
        def bars_callback(self, bar, attr, value, old_value=None):
            if bar == "frame_index" and attr == "index":
                total = self.bars[bar].get("total") or 0
                if total:
                    progress("Video yığılır...", start + span * value / total)

    return _L()


def render(clips, out_path, progress, start=0.55, span=0.45):
    from moviepy import concatenate_videoclips

    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(
        str(out_path), fps=FPS, codec="libx264", audio_codec="aac",
        preset="medium", threads=4, logger=_render_logger(progress, start, span),
    )
    return final.duration


# ---------- Public entry ----------
def generate(script_text, api_key, voice, rate, out_path, fmt_name="shorts",
             progress=lambda msg, frac: None):
    from moviepy import AudioFileClip

    fmt = FORMATS[fmt_name]
    font = find_font()
    scenes = parse_scenes(script_text)
    if not scenes:
        raise RuntimeError("Ssenari boşdur.")
    if not api_key:
        raise RuntimeError("Pixabay açarını Ayarlarda daxil edin.")

    work = Path(tempfile.mkdtemp(prefix="shortsmaker_"))
    n = len(scenes)

    # 1) Voiceover for all scenes (0-15%)
    voiced = []
    for i, (text, kw) in enumerate(scenes, 1):
        progress(f"Səhnə {i}/{n} səsləndirilir...", 0.15 * (i - 1) / n)
        mp3 = work / f"scene{i}.mp3"
        words = tts(text, mp3, voice, rate)
        voiced.append((text, kw, words, AudioFileClip(str(mp3))))

    total = sum(a.duration + 0.15 for *_, a in voiced)
    if fmt["max_sec"] and total > fmt["max_sec"]:
        raise ScriptTooLong(
            f"Səsləndirmə {total:.0f} saniyə alındı, Shorts üçün maksimum {fmt['max_sec']:.0f} saniyədir. "
            "Ssenarini bir az qısaldın."
        )

    # 2) Stock footage (15-55%)
    used, clips = set(), []
    vertical = fmt["size"][1] > fmt["size"][0]
    for i, (text, kw, words, audio) in enumerate(voiced, 1):
        progress(f"Səhnə {i}/{n}: '{kw}' videosu yüklənir...", 0.15 + 0.40 * (i - 1) / n)
        mp4 = work / f"scene{i}.mp4"
        ok = fetch_video(kw, mp4, used, api_key, vertical)
        clips.append(make_scene(mp4 if ok else None, audio, words, text, font, fmt))

    # 3) Render (55-100%)
    progress("Video yığılır...", 0.55)
    dur = render(clips, out_path, progress)
    progress("Hazırdır!", 1.0)
    return dur


def selftest(out_dir):
    """Offline check that MoviePy, FFmpeg and fonts work in both formats (used by the build)."""
    import numpy as np
    from moviepy import AudioClip, AudioFileClip

    out_dir = Path(out_dir)
    work = Path(tempfile.mkdtemp(prefix="shortsmaker_test_"))
    wav = work / "a.wav"
    AudioClip(lambda t: np.sin(440 * 2 * np.pi * t) * 0.1, duration=2, fps=44100).write_audiofile(
        str(wav), logger=None
    )
    words = [(0, 0.6, "Self"), (0.6, 1.2, "test"), (1.2, 1.9, "works")]
    ok = True
    for name, fmt in FORMATS.items():
        out = out_dir / f"selftest_{name}.mp4"
        clip = make_scene(None, AudioFileClip(str(wav)), words, "Self test works", find_font(), fmt)
        render([clip], out, lambda m, f: None)
        ok = ok and out.exists() and out.stat().st_size > 0
    return ok
