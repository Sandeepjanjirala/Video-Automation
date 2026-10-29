import re
from typing import List, Dict, Any

def format_ass_timestamp(seconds: float) -> str:
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"

KEYWORD_PATTERNS = [
    r"^\d+",
    r"^(TELESCOPE|JAMES|WEBB|SPACE|INFRARED|GALAXY|NEBULA|STARS|STAR|LIGHT|UNIVERSE|COSMIC|DISCOVERY|FACT|MYSTERY|SECRET|TRUTH)$",
    r"^(SAKTHISHALI|PRARAMBHANNI|KAANTHINI|NIDARSANAM|RAHASYAALANU|CHEDHISTHUNNA|KOTLA|ADBHUTHA|GURTHISTHUNDI|CHOODAGALIGE)$",
]

def is_accent_keyword(word: str) -> bool:
    stripped = re.sub(r"[^\w]", "", word).upper()
    for pat in KEYWORD_PATTERNS:
        if re.search(pat, stripped):
            return True
    return False

def generate_karaoke_dialogue_events(
    cue: Dict[str, Any],
    style_name: str,
    highlight_color: str = "00FFFF",  # BGR hex for yellow
    primary_color: str = "FFFFFF",    # BGR hex for white
    accent_color: str = "00D7FF",     # BGR hex for gold
    kinetic_pop: bool = True,
    uppercase: bool = True,
) -> List[str]:
    raw_text = cue.get("text", "").strip()
    if not raw_text:
        return []

    if uppercase:
        raw_text = raw_text.upper()

    words = raw_text.split()
    start_t = float(cue.get("start", 0.0))
    end_t = float(cue.get("end", 0.0))
    cue_dur = max(0.15, end_t - start_t)

    if len(words) <= 1:
        start_ts = format_ass_timestamp(start_t)
        end_ts = format_ass_timestamp(end_t)
        pop_tag = r"{\t(0,50,\fscx110\fscy110)\t(50,110,\fscx100\fscy100)}" if kinetic_pop else ""
        col = accent_color if is_accent_keyword(raw_text) else highlight_color
        txt = r"{\c&H" + col + r"&}" + pop_tag + raw_text + r"{\c&H" + primary_color + r"&}"
        return [f"Dialogue: 0,{start_ts},{end_ts},{style_name},,0,0,0,,{txt}"]

    # Calculate proportional durations based on character lengths
    lengths = [max(1, len(re.sub(r"[^\w]", "", w))) for w in words]
    total_len = sum(lengths)

    events: List[str] = []
    curr_t = start_t

    for i, w in enumerate(words):
        if i == len(words) - 1:
            w_end = end_t
        else:
            dur_share = (lengths[i] / total_len) * cue_dur
            dur_share = max(0.20, dur_share)
            w_end = min(end_t - 0.15 * (len(words) - i - 1), curr_t + dur_share)

        start_ts = format_ass_timestamp(curr_t)
        end_ts = format_ass_timestamp(w_end)

        is_kw = is_accent_keyword(w)
        active_col = accent_color if is_kw else highlight_color
        pop_tag = r"{\t(0,50,\fscx112\fscy112)\t(50,110,\fscx100\fscy100)}" if kinetic_pop else ""

        formatted_words = []
        for j, ow in enumerate(words):
            if j == i:
                formatted_words.append(r"{\c&H" + active_col + r"&}" + pop_tag + ow + r"{\c&H" + primary_color + r"&}")
            else:
                formatted_words.append(ow)

        line_text = " ".join(formatted_words)
        events.append(f"Dialogue: 0,{start_ts},{end_ts},{style_name},,0,0,0,,{line_text}")
        curr_t = w_end

    return events

# Test with 3 sample cues
cues = [
    {"start": 0.0, "end": 1.2, "text": "Viswam prarambhanni choodagalige", "placement": "bottom"},
    {"start": 1.2, "end": 2.2, "text": "space telescope. Idi", "placement": "bottom"},
]

for c in cues:
    evs = generate_karaoke_dialogue_events(c, "Default")
    for e in evs:
        print(e)
