# mkvq/parsers/makemkv_info.py
import re
from collections import defaultdict

def parse_label_from_info(output: str):
    m = re.search(r'CINFO:2,\d+,\d+,"([^"]+)"', output)
    return m.group(1) if m else None

def count_titles_from_info(output: str) -> int:
    titles = set()
    for line in output.splitlines():
        if line.startswith("TINFO:"):
            try:
                idx = int(line.split(":")[1].split(",")[0])
                titles.add(idx)
            except Exception:
                pass
    return len(titles)

def _format_channels(count_str: str | None) -> str | None:
    if not count_str or not count_str.isdigit():
        return None
    count = int(count_str)
    if count == 1: return "1.0 (Mono)"
    if count == 2: return "2.0 (Stereo)"
    if count == 6: return "5.1"
    if count == 8: return "7.1"
    return f"{count} Channels"

_LANG_NAMES = {
    "eng": "English", "spa": "Spanish", "fra": "French", "deu": "German",
    "ita": "Italian", "jpn": "Japanese", "zho": "Chinese", "kor": "Korean",
    "por": "Portuguese", "rus": "Russian",
}

def _pretty_lang_from_code(code: str):
    return _LANG_NAMES.get(code.lower(), code.title())

def _has(words, blob: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}\b", blob, re.IGNORECASE) for w in words)

def _codec_from_blob(kind: str, blob: str):
    if kind == "Video":
        if _has(["HEVC","H.265","H265"], blob): return "HEVC"
        if _has(["AVC","H.264","H264"], blob): return "H.264/AVC"
        if _has(["VC-1","VC1"], blob): return "VC-1"
        if _has(["MPEG-2","Mpeg2","MPEG2"], blob): return "MPEG-2"
        return "Video"
    if kind == "Audio":
        if _has(["TrueHD"], blob): return "Dolby TrueHD"
        if _has(["E-AC3","EAC3","DD+","Dolby Digital Plus"], blob): return "Dolby Digital Plus"
        if _has(["AC3","AC-3","Dolby Digital","DD "], blob): return "Dolby Digital"
        if _has(["DTS-HD MA","DTS HD MA"], blob): return "DTS-HD MA"
        if _has(["DTS-HD","DTS HD"], blob): return "DTS-HD"
        if _has(["DTS:X","DTSX"], blob): return "DTS:X"
        if _has(["DTS"], blob): return "DTS"
        if _has(["LPCM","PCM"], blob): return "PCM"
        if _has(["FLAC"], blob): return "FLAC"
        if _has(["AAC"], blob): return "AAC"
        return "Audio"
    if kind == "Subtitles":
        if _has(["PGS"], blob): return "PGS"
        if _has(["VobSub"], blob): return "VobSub"
        return "Subtitles"
    return None

def parse_info_details(output: str) -> dict:
    info = defaultdict(lambda: {"streams": []})
    tinfo_map = defaultdict(dict)
    sinfo_map = defaultdict(lambda: defaultdict(dict))

    for line in output.splitlines():
        line = line.strip()
        try:
            prefix, rest = line.split(":", 1)
            if prefix == "TINFO":
                t_str, c_str, _, val = rest.split(",", 3)
                tinfo_map[int(t_str)][int(c_str)] = val.strip('"')
            elif prefix == "SINFO":
                t_str, s_str, c_str, _, val = rest.split(",", 4)
                sinfo_map[int(t_str)][int(s_str)][int(c_str)] = val.strip('"')
        except Exception:
            continue

    for t_idx, codes in tinfo_map.items():
        d = info[t_idx]
        chapters_str = codes.get(9, '0')
        chapters_match = re.search(r'^\d+', chapters_str)
        d["chapters"] = int(chapters_match.group(0)) if chapters_match else 0
        d["source"] = codes.get(16)
        d["duration"] = codes.get(10)
        d["size"] = codes.get(11)

    for t_idx, streams in sinfo_map.items():
        for s_idx, codes in streams.items():
            kind = codes.get(1)
            # Use official codes for codec names now
            codec_blob = f"{codes.get(6, '')} {codes.get(7, '')} {codes.get(8, '')}"
            lang_code = codes.get(3)

            stream_info = {
                "kind": kind,
                "index": s_idx,
                "lang": _pretty_lang_from_code(lang_code) if lang_code else None,
                "codec": _codec_from_blob(kind, codec_blob),
                # Directly get rich data from their official codes
                "res": codes.get(19),
                "ar": codes.get(20),
                "fps": codes.get(21),
                "channels_count": codes.get(22),
                "channels_layout": codes.get(40),
                "sample_rate": codes.get(17),
                "raw": codes.get(8, ""),
            }
            info[t_idx]["streams"].append(stream_info)

    return dict(info)

def duration_to_seconds(d: str | None):
    if not d: return None
    parts = d.split(":")
    try:
        if len(parts) == 3:
            h, m, s = map(int, parts); return h*3600 + m*60 + s
        if len(parts) == 2:
            m, s = map(int, parts); return m*60 + s
    except Exception:
        return None
    return None
