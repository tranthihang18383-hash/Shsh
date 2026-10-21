"""Read basic facts (note count, tempo, length) from a .mid file.

Pure Python, no extra packages.
"""
import struct


def _vlq(data, i):
    """Read a MIDI variable-length number; returns (value, next_index)."""
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def _read_track(trk):
    """Return (note_count, tempo_events, last_tick) for one track."""
    notes, tempos = 0, []
    i = tick = status = 0
    try:
        while i < len(trk):
            delta, i = _vlq(trk, i)
            tick += delta
            b = trk[i]
            if b == 0xFF:  # meta event
                typ = trk[i + 1]
                ln, j = _vlq(trk, i + 2)
                if typ == 0x51 and ln == 3:
                    tempos.append((tick, int.from_bytes(trk[j:j + 3], "big")))
                i = j + ln
                if typ == 0x2F:  # end of track
                    break
                continue
            if b in (0xF0, 0xF7):  # sysex
                ln, j = _vlq(trk, i + 1)
                i = j + ln
                continue
            if b & 0x80:
                status = b
                i += 1
            kind = status & 0xF0
            if kind in (0xC0, 0xD0):
                i += 1
            else:
                velocity = trk[i + 1]
                i += 2
                if kind == 0x90 and velocity > 0:
                    notes += 1
    except IndexError:
        pass  # a cut-off file: use what was read
    return notes, tempos, tick


def read_midi_info(path):
    """Return {'notes', 'bpm_min', 'bpm_max', 'seconds'} for a MIDI file."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hlen = struct.unpack(">I", data[4:8])[0]
    _fmt, ntrk, div = struct.unpack(">HHH", data[8:14])
    pos = 8 + hlen

    notes, tempos, end_tick = 0, [], 0
    for _ in range(ntrk):
        if data[pos:pos + 4] != b"MTrk":
            break
        ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        n, t, last = _read_track(data[pos + 8:pos + 8 + ln])
        notes += n
        tempos += t
        end_tick = max(end_tick, last)
        pos += 8 + ln

    tempos.sort(key=lambda x: x[0])
    if not tempos or tempos[0][0] > 0:
        tempos.insert(0, (0, 500000))  # MIDI default: 120 BPM

    if div & 0x8000:  # SMPTE time: ticks per frame
        fps = 256 - (div >> 8)
        seconds = end_tick / float(fps * (div & 0xFF) or 1)
    else:
        seconds, prev_tick, cur = 0.0, 0, tempos[0][1]
        for tk, us in tempos:
            if tk > end_tick:
                break
            seconds += (tk - prev_tick) * cur / 1e6 / div
            prev_tick, cur = tk, us
        seconds += (end_tick - prev_tick) * cur / 1e6 / div

    bpms = [60e6 / us for _, us in tempos if us > 0] or [120.0]
    return {"notes": notes, "bpm_min": min(bpms), "bpm_max": max(bpms),
            "seconds": seconds}


def format_tempo(info):
    lo, hi = round(info["bpm_min"]), round(info["bpm_max"])
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def format_length(seconds):
    s = int(round(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"
