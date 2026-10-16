import asyncio, edge_tts
TEXT = "Ahtapotların üç kalbi var. Kanları mavidir çünkü bakır kullanırlar."
AZ = "Ahtapotun üç ürəyi var. Qanları mavidir, çünki mis istifadə edirlər."
async def run(voice, text, rate, boundary):
    kw = {"rate": rate}
    if boundary: kw["boundary"] = boundary
    n, w = 0, 0
    try:
        async for ch in edge_tts.Communicate(text, voice, **kw).stream():
            if ch["type"] == "audio": n += len(ch["data"])
            elif ch["type"].endswith("Boundary"): w += 1
        return f"OK bytes={n} bounds={w}"
    except Exception as e:
        return f"FAIL {type(e).__name__}: {e}"
async def main():
    lines = [f"edge-tts {edge_tts.__version__ if hasattr(edge_tts,'__version__') else '?'}"]
    for v in ["tr-TR-AhmetNeural", "en-US-AndrewMultilingualNeural", "en-US-BrianMultilingualNeural",
              "de-DE-FlorianMultilingualNeural", "fr-FR-RemyMultilingualNeural", "it-IT-GiuseppeMultilingualNeural"]:
        lines.append(f"{v}: {await run(v, TEXT, '+0%', 'WordBoundary')}")
    lines.append("voices tr: " + ", ".join(x["ShortName"] for x in await edge_tts.list_voices() if x["Locale"].startswith("tr")))
    print("::warning title=tts-diag::" + "%0A".join(lines))
asyncio.run(main())
