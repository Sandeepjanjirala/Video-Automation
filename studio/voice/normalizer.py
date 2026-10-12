"""Telugu Speech Normalization and Pronunciation Dictionary for Telugu Reel Studio (Phase 4).
Normalizes dates, numbers, abbreviations, and loanwords into natural spoken Telugu Unicode,
with strict separation between source text, spoken text, and Roman display captions.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# Cardinal Telugu number words
ONES = {
    0: "సున్నా", 1: "ఒకటి", 2: "రెండు", 3: "మూడు", 4: "నాలుగు",
    5: "ఐదు", 6: "ఆరు", 7: "ఏడు", 8: "ఎనిమిది", 9: "తొమ్మిది",
    10: "పది", 11: "పదకొండు", 12: "పన్నెండు", 13: "పదమూడు", 14: "పద్నాలుగు",
    15: "పదిహేను", 16: "పదహారు", 17: "పదిహేడు", 18: "పద్దెనిమిది", 19: "పందొమ్మిది"
}

TENS = {
    20: "ఇరవై", 30: "ముప్పై", 40: "నలభై", 50: "యాభై",
    60: "అరవై", 70: "డెబ్బై", 80: "ఎనభై", 90: "తొంబై"
}

MONTHS_TE = {
    1: "జనవరి", 2: "ఫిబ్రవరి", 3: "మార్చి", 4: "ఏప్రిల్",
    5: "మే", 6: "జూన్", 7: "జూలై", 8: "ఆగస్టు",
    9: "సెప్టెంబర్", 10: "అక్టోబర్", 11: "నవంబర్", 12: "డిసెంబర్"
}

# Common English acronyms / abbreviations pronounced in Telugu
ABBREVIATIONS_TE = {
    "AI": "ఏఐ",
    "API": "ఏపీఐ",
    "PM": "పీఎం",
    "CM": "సీఎం",
    "TV": "టీవీ",
    "URL": "యూఆర్ఎల్",
    "CPU": "సీపీయూ",
    "GPU": "జీపీయూ",
    "RAM": "ర్యామ్",
    "SMS": "ఎస్ఎంఎస్",
    "OTP": "ఓటీపీ",
    "ID": "ఐడీ",
    "OK": "ఓకే",
    "PDF": "పీడీఎఫ్",
    "USB": "యూఎస్బీ",
    "GPS": "జీపీఎస్",
    "SIM": "సిమ్",
    "ATM": "ఏటీఎం",
    "BGM": "బీజీఎం",
}

# Common English loanwords frequently used in Telugu conversational speech
COMMON_LOANWORDS_TE = {
    "room": "రూమ్",
    "hotel": "హోటల్",
    "mirror": "మిర్రర్",
    "phone": "ఫోన్",
    "reception": "రిసెప్షన్",
    "message": "మెసేజ్",
    "call": "కాల్",
    "time": "టైమ్",
    "minute": "నిమిషం",
    "second": "సెకన్",
    "photo": "ఫోటో",
    "video": "వీడియో",
    "camera": "కెమెరా",
    "door": "డోర్",
    "bus": "బస్సు",
    "car": "కారు",
    "train": "ట్రైన్",
    "doctor": "డాక్టర్",
    "hospital": "హాస్పిటల్",
    "police": "పోలీస్",
    "school": "స్కూల్",
    "college": "కాలేజ్",
    "office": "ఆఫీస్",
    "story": "స్టోరీ",
}


def number_to_telugu(num: int) -> str:
    """Convert an integer (0 to 999,999) into spoken Telugu words."""
    if num < 0:
        return f"మైనస్ {number_to_telugu(-num)}"
    if num < 20:
        return ONES[num]
    if num < 100:
        ten, rem = divmod(num, 10)
        base = TENS[ten * 10]
        if rem == 0:
            return base
        # Telugu combining forms: ఇరవై ఒకటి -> ఇరవై ఒకటి
        return f"{base} {ONES[rem]}"
    if num < 1000:
        hundred, rem = divmod(num, 100)
        h_word = "వంద" if hundred == 1 else f"{ONES[hundred]} వందలు"
        if rem == 0:
            return h_word
        return f"{h_word} {number_to_telugu(rem)}"
    if num < 100000:
        thousand, rem = divmod(num, 1000)
        t_word = "వెయ్యి" if thousand == 1 else f"{number_to_telugu(thousand)} వేలు"
        if rem == 0:
            return t_word
        return f"{t_word} {number_to_telugu(rem)}"
    if num < 10000000:
        lakh, rem = divmod(num, 100000)
        l_word = "లక్ష" if lakh == 1 else f"{number_to_telugu(lakh)} లక్షలు"
        if rem == 0:
            return l_word
        return f"{l_word} {number_to_telugu(rem)}"
    return str(num)


def normalize_percentages(text: str) -> str:
    """Normalize percentages (e.g., '100%' -> 'వంద శాతం')."""
    def repl(m):
        val = int(m.group(1))
        return f"{number_to_telugu(val)} శాతం"
    return re.sub(r'(\d+)\s*%', repl, text)


def normalize_dates(text: str) -> str:
    """Normalize numeric dates like '15-08-1947' or '12/10/2026'."""
    def repl_dmy(m):
        day = int(m.group(1))
        month = int(m.group(2))
        year = int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            m_name = MONTHS_TE.get(month, f"{month}వ నెల")
            return f"{m_name} {number_to_telugu(day)}, {number_to_telugu(year)}"
        return m.group(0)

    # DD-MM-YYYY or DD/MM/YYYY
    text = re.sub(r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b', repl_dmy, text)
    return text


def normalize_numbers_in_text(text: str) -> str:
    """Normalize isolated digits or numbers inside text into Telugu words."""
    def repl_num(m):
        val = int(m.group(0))
        if val <= 999999:
            return number_to_telugu(val)
        return m.group(0)

    # Match numbers that are not part of an identifier
    return re.sub(r'\b\d+\b', repl_num, text)


def normalize_abbreviations(text: str) -> str:
    """Normalize English abbreviations into phonetic Telugu words."""
    for abbr, spoken in ABBREVIATIONS_TE.items():
        text = re.sub(rf'\b{abbr}\b', spoken, text, flags=re.IGNORECASE)
    return text


def apply_pronunciation_dictionary(text: str, custom_dict: Dict[str, str]) -> Tuple[str, List[Dict[str, str]]]:
    """Apply user-defined pronunciation dictionary overrides with highest priority."""
    applied = []
    if not custom_dict:
        return text, applied

    # Sort keys by length descending to match multi-word phrases first
    for word in sorted(custom_dict.keys(), key=len, reverse=True):
        replacement = custom_dict[word]
        pattern = rf'\b{re.escape(word)}\b'
        if re.search(pattern, text, flags=re.IGNORECASE):
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            applied.append({"original": word, "spoken": replacement})
    return text, applied


@dataclass
class NormalizedNarration:
    source_text: str
    spoken_text: str
    roman_text: str
    overrides_applied: List[Dict[str, str]] = field(default_factory=list)


def normalize_narration(
    source_text: str,
    roman_display: str = "",
    pronunciation_dict: Optional[Dict[str, str]] = None
) -> NormalizedNarration:
    """Full normalization pipeline for Telugu narration.
    1. User pronunciation dictionary overrides (highest priority)
    2. Date normalization
    3. Percentage normalization
    4. Numeric normalization
    5. Abbreviation normalization
    6. Common loanword transliteration
    """
    text = source_text.strip()
    overrides = []

    # 1. Custom dictionary overrides
    if pronunciation_dict:
        text, overrides = apply_pronunciation_dictionary(text, pronunciation_dict)

    # 2. Date expressions
    text = normalize_dates(text)

    # 3. Percentages
    text = normalize_percentages(text)

    # 4. Numbers
    text = normalize_numbers_in_text(text)

    # 5. Abbreviations
    text = normalize_abbreviations(text)

    # 6. Loanwords (if written in English within Telugu text)
    for en_word, te_word in COMMON_LOANWORDS_TE.items():
        text = re.sub(rf'\b{en_word}\b', te_word, text, flags=re.IGNORECASE)

    # Normalize multiple whitespace
    spoken_text = re.sub(r'\s+', ' ', text).strip()

    # Roman display text defaults to provided roman, or source if roman was empty
    roman_text = roman_display.strip() or source_text.strip()

    return NormalizedNarration(
        source_text=source_text,
        spoken_text=spoken_text,
        roman_text=roman_text,
        overrides_applied=overrides
    )
