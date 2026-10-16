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
    for v, t in [("tr-TR-AhmetNeural", TEXT), ("tr-TR-EmelNeural", TEXT), ("az-AZ-BabekNeural", AZ),
                 ("az-AZ-BanuNeural", AZ), ("en-US-AndrewNeural", "Hello world test.")]:
        for rate in ["+5%", "+0%"]:
            for b in ["WordBoundary", None]:
                lines.append(f"{v} rate={rate} boundary={b}: {await run(v, t, rate, b)}")
    print("::warning title=tts-diag::" + "%0A".join(lines))
asyncio.run(main())
