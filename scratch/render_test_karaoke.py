import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import subprocess
from video_understanding.sampling import get_ffmpeg_executable

ass_content = """[Script Info]
Title: Dynamic Pro Viral Captions
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,50,&H00FFFFFF,&H0000FFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.35,Default,,0,0,0,,{\\c&H00FFFF&}{\\t(0,50,\\fscx112\\fscy112)\\t(50,110,\\fscx100,\\fscy100)}VISWAM{\\c&HFFFFFF&} PRARAMBHANNI CHOODAGALIGE
Dialogue: 0,0:00:00.35,0:00:00.85,Default,,0,0,0,,VISWAM {\\c&H00D7FF&}{\\t(0,50,\\fscx112\\fscy112)\\t(50,110,\\fscx100,\\fscy100)}PRARAMBHANNI{\\c&HFFFFFF&} CHOODAGALIGE
Dialogue: 0,0:00:00.85,0:00:01.30,Default,,0,0,0,,VISWAM PRARAMBHANNI {\\c&H00FFFF&}{\\t(0,50,\\fscx112\\fscy112)\\t(50,110,\\fscx100,\\fscy100)}CHOODAGALIGE{\\c&HFFFFFF&}
"""

test_ass = Path("scratch/test_karaoke.ass")
test_ass.write_text(ass_content, encoding="utf-8")

ffmpeg = get_ffmpeg_executable()
ass_escaped = str(test_ass.resolve().as_posix()).replace(":", "\\:")
out_vid = Path("scratch/test_karaoke.mp4")

cmd = [
    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=0x0B3856:s=1080x1920:r=30:d=2.0",
    "-vf", f"subtitles=filename='{ass_escaped}'",
    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
    str(out_vid),
]

subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
print("Rendered test karaoke video successfully:", out_vid.stat().st_size, "bytes")

# Extract snapshot at 0.5s (word 2 active) and 1.0s (word 3 active)
frame1 = Path("scratch/karaoke_frame_w2.png")
cmd1 = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.55", "-i", str(out_vid), "-vframes", "1", str(frame1)]
subprocess.run(cmd1, check=True)

frame2 = Path("scratch/karaoke_frame_w3.png")
cmd2 = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1.0", "-i", str(out_vid), "-vframes", "1", str(frame2)]
subprocess.run(cmd2, check=True)
print("Extracted snapshot frames!")
