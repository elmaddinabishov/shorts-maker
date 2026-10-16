"""
Video engine: script text -> YouTube Shorts MP4 (1080x1920).
Voiceover: edge-tts (free). Footage: Pexels API (free key). Montage: MoviePy + bundled FFmpeg.
"""
import asyncio
import re
import tempfile
from pathlib import Path

import requests

W, H, FPS = 1080, 1920, 30
WORDS_PER_CAPTION = 3

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

STOPWORDS = set("""
a an the and or but if of to in on at for with from by as is are was were be been being
it its this that these those you your we our they their them he she his her i me my
do does did have has had not no so than then there here what which who whom how why when
where can could will would should may might just also very more most some any all each
about into over after before up down out off again only own same too now know like
didn while because even ever every still much many make makes made let lets get gets
follow part instead rest through
""".split())


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


# ---------- Stock video ----------
def _download(url, dest):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for part in r.iter_content(1 << 16):
                f.write(part)


def fetch_video(query, dest, used_ids, api_key):
    headers = {"Authorization": api_key}
    words = query.split()
    for q in dict.fromkeys([query, " ".join(words[:2]), words[0] if words else query]):
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": q, "orientation": "portrait", "per_page": 15},
            timeout=30,
        )
        if r.status_code in (401, 403):
            raise RuntimeError("Pexels açarı yanlışdır. Açarı yoxlayın.")
        r.raise_for_status()
        for v in r.json().get("videos", []):
            if v["id"] in used_ids:
                continue
            files = [
                f for f in v.get("video_files", [])
                if f.get("width") and f.get("height")
                and f["height"] >= f["width"]
                and f.get("file_type") == "video/mp4"
            ]
            if not files:
                continue
            best = min(files, key=lambda f: abs(f["height"] - H))
            used_ids.add(v["id"])
            _download(best["link"], dest)
            return True
    return False


# ---------- Captions + montage ----------
def _fit_vertical(clip):
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resized(scale)
    return clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=W, height=H)


def _even_timings(text, dur):
    ws = text.split()
    step = dur / max(len(ws), 1)
    return [(i * step, (i + 1) * step, w) for i, w in enumerate(ws)]


def _captions(words, dur, font):
    from moviepy import TextClip

    clips = []
    groups = [words[i:i + WORDS_PER_CAPTION] for i in range(0, len(words), WORDS_PER_CAPTION)]
    for i, g in enumerate(groups):
        start = g[0][0]
        end = groups[i + 1][0][0] if i + 1 < len(groups) else dur
        tc = TextClip(
            font=font, text=" ".join(w[2] for w in g).upper(), font_size=95,
            color="white", stroke_color="black", stroke_width=7,
            method="caption", size=(W - 160, None), text_align="center", margin=(20, 40),
        )
        clips.append(
            tc.with_start(start).with_duration(max(0.05, end - start))
            .with_position(("center", int(H * 0.62)))
        )
    return clips


def make_scene(video_path, audio_path, words, text, font):
    from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, VideoFileClip, vfx

    audio = AudioFileClip(str(audio_path))
    dur = audio.duration + 0.15
    if video_path:
        bg = _fit_vertical(VideoFileClip(str(video_path), audio=False))
        bg = bg.with_effects([vfx.Loop(duration=dur)]) if bg.duration < dur else bg.subclipped(0, dur)
    else:
        bg = ColorClip((W, H), color=(20, 20, 20), duration=dur)
    caps = _captions(words or _even_timings(text, audio.duration), dur, font)
    return CompositeVideoClip([bg, *caps], size=(W, H)).with_duration(dur).with_audio(audio)


def _render_logger(progress):
    from proglog import ProgressBarLogger

    class _L(ProgressBarLogger):
        def bars_callback(self, bar, attr, value, old_value=None):
            if bar == "frame_index" and attr == "index":
                total = self.bars[bar].get("total") or 0
                if total:
                    progress("Video yığılır...", 0.75 + 0.25 * value / total)

    return _L()


def render(clips, out_path, progress):
    from moviepy import concatenate_videoclips

    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(
        str(out_path), fps=FPS, codec="libx264", audio_codec="aac",
        preset="medium", threads=4, logger=_render_logger(progress),
    )
    return final.duration


# ---------- Public entry ----------
def generate(script_text, api_key, voice, rate, out_path, progress=lambda msg, frac: None):
    font = find_font()
    scenes = parse_scenes(script_text)
    if not scenes:
        raise RuntimeError("Ssenari boşdur.")
    if not api_key:
        raise RuntimeError("Pexels açarını daxil edin.")

    work = Path(tempfile.mkdtemp(prefix="shortsmaker_"))
    used, clips = set(), []
    n = len(scenes)
    for i, (text, kw) in enumerate(scenes, 1):
        progress(f"Səhnə {i}/{n}: səsləndirmə...", 0.75 * (i - 1) / n)
        mp3 = work / f"scene{i}.mp3"
        words = tts(text, mp3, voice, rate)
        progress(f"Səhnə {i}/{n}: '{kw}' videosu axtarılır...", 0.75 * (i - 0.5) / n)
        mp4 = work / f"scene{i}.mp4"
        ok = fetch_video(kw, mp4, used, api_key)
        clips.append(make_scene(mp4 if ok else None, mp3, words, text, font))

    progress("Video yığılır...", 0.75)
    dur = render(clips, out_path, progress)
    progress("Hazırdır!", 1.0)
    return dur


def selftest(out_path):
    """Offline check that MoviePy, FFmpeg and fonts work (used by the build)."""
    import numpy as np
    from moviepy import AudioClip

    work = Path(tempfile.mkdtemp(prefix="shortsmaker_test_"))
    wav = work / "a.wav"
    AudioClip(lambda t: np.sin(440 * 2 * np.pi * t) * 0.1, duration=2, fps=44100).write_audiofile(
        str(wav), logger=None
    )
    words = [(0, 0.6, "Self"), (0.6, 1.2, "test"), (1.2, 1.9, "works")]
    clip = make_scene(None, wav, words, "Self test works", find_font())
    render([clip], out_path, lambda m, f: None)
    return Path(out_path).stat().st_size > 0
