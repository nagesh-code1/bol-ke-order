"""Make synthetic demo voice notes (Edge TTS -> WhatsApp-style .ogg/opus).

These are computer voices, cleaner than real customers: use them for smoke tests
only, and test with real recorded voice notes before any demo claim.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import edge_tts

OUT = Path(__file__).resolve().parents[1] / "data" / "samples"
SAMPLES = [
    ("sharma_hinglish", "hi-IN-SwaraNeural",
     "Bhaiya, ek kilo Aashirvaad atta, do packet Parle-G, aadha kilo cheeni, aur woh wala tel jo pichhli baar liya tha. Shaam ko bhej dena."),
    ("ramesh_hindi", "hi-IN-MadhurNeural",
     "राम राम भैया, दो दूध, एक ब्रेड और अमूल बटर भेज दो। पैसे एक तारीख को दूंगा, लिख लेना।"),
    ("lakshmi_tamil", "ta-IN-PallaviNeural",
     "அண்ணா, அஞ்சு கிலோ அரிசி, ஒரு கிலோ துவரம் பருப்பு, அப்புறம் எண்ணெய் ஒன்னு அனுப்புங்க."),
    ("imran_correction", "hi-IN-MadhurNeural",
     "Bhai do packet Maggi bhejna, nahi nahi, teen packet kar do. Aur ek Surf Excel bhi."),
]


async def make(name: str, voice: str, text: str) -> Path:
    mp3 = OUT / f"{name}.mp3"
    ogg = OUT / f"{name}.ogg"
    await edge_tts.Communicate(text, voice).save(str(mp3))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-c:a", "libopus", "-b:a", "24k",
                    "-ac", "1", "-ar", "16000", str(ogg)], check=True)
    mp3.unlink()
    return ogg


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, voice, text in SAMPLES:
        path = await make(name, voice, text)
        sys.stdout.write(f"{path.name} {path.stat().st_size} bytes\n")


if __name__ == "__main__":
    asyncio.run(main())
