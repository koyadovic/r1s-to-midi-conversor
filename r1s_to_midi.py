#!/usr/bin/env python3
"""Convert Yamaha RM1x .R1S song files into Standard MIDI Files (format 1).

The event encoding and record offsets were inferred from the local R1S corpus.
The RM1x manual confirms the 480 PPQN resolution and which settings are stored
in a song, but does not document the binary format.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PPQN = 480
HEADER_SIZE = 0x80
PAGE_SIZE = 128
TRACK_COUNT = 16


@dataclass
class MidiEvent:
    tick: int
    priority: int
    data: bytes
    note_id: int | None = None


@dataclass
class ParseWarning:
    track_index: int
    offset: int
    message: str


@dataclass
class SongRecord:
    offset: int
    name: str
    bpm: float
    data: bytes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert reverse-engineered RM1x R1S song files into MIDI."
    )
    parser.add_argument("inputs", nargs="+", help="Input .R1S files")
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="Directory where converted .mid files will be written",
    )
    parser.add_argument(
        "--bpm",
        type=float,
        default=120.0,
        help="Fallback tempo to write into the MIDI file (default: 120)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-track details while converting",
    )
    parser.add_argument(
        "--gm-drums",
        action="store_true",
        help="GM listening copy: send RM1x drum-bank notes to channel 10 using a standard kit",
    )
    return parser.parse_args()


def read_track_blocks(data: bytes) -> list[tuple[int, bytes]]:
    if len(data) < HEADER_SIZE or data[:16] != b"YQ5SNG     V1.00":
        raise ValueError("unsupported header, expected RM1x song signature")

    sizes = [
        int.from_bytes(data[0x20 + 2 * i : 0x22 + 2 * i], "big") * PAGE_SIZE
        for i in range(TRACK_COUNT)
    ]
    if HEADER_SIZE + sum(sizes) > len(data):
        raise ValueError("track sizes extend beyond end of file")
    offset = HEADER_SIZE
    blocks: list[tuple[int, bytes]] = []
    for track_index, size in enumerate(sizes, start=1):
        block = data[offset : offset + size]
        offset += size
        if size:
            blocks.append((track_index, block))
    return blocks


def tracks_end(data: bytes) -> int:
    return HEADER_SIZE + sum(
        int.from_bytes(data[0x20 + 2 * i : 0x22 + 2 * i], "big") * PAGE_SIZE
        for i in range(TRACK_COUNT)
    )


def find_song_record(data: bytes, tracks_end: int) -> SongRecord | None:
    # Old records may precede the current record. The name need not match the
    # filename because renaming a copied file must still work.
    records = []
    for offset in range(tracks_end, len(data) - 0x200 + 1, PAGE_SIZE):
        if data[offset + 8 : offset + 10] != b"\xFE\xFE":
            continue
        raw_name = data[offset + 10 : offset + 18]
        if not all(32 <= value < 127 for value in raw_name):
            continue
        bpm = int.from_bytes(data[offset : offset + 2], "big") / 10
        if 25 <= bpm <= 300:
            records.append(SongRecord(offset, raw_name.decode("ascii").rstrip(), bpm, data[offset:]))
    return records[-1] if records else None


def parse_track_block(track_index: int, block: bytes) -> tuple[list[MidiEvent], list[ParseWarning], bool, int]:
    warnings: list[ParseWarning] = []
    events: list[MidiEvent] = []
    channel = (track_index - 1) & 0x0F
    notes = 0

    if len(block) < 2 or block[:2] != b"\xF0\x00":
        warnings.append(ParseWarning(track_index, 0, "track block does not start with F0 00"))
        return events, warnings, False, notes

    tick = 0
    i = 2
    complete = False

    while i < len(block):
        cmd = block[i]

        if cmd == 0xF2:
            complete = True
            break

        if cmd == 0x00:
            tail = block[i:]
            if all(b in {0x00, 0x01, 0x03, 0x20, 0x40, 0x64, 0x7F} for b in tail):
                complete = True
                break
            warnings.append(ParseWarning(track_index, i, "unexpected 0x00 inside event stream"))
            break

        if 0x80 <= cmd <= 0x9F:
            tick += cmd - 0x80
            i += 1
            continue

        if 0xA0 <= cmd <= 0xBF:
            if i + 1 >= len(block):
                warnings.append(ParseWarning(track_index, i, "truncated long wait event"))
                break
            tick += ((cmd & 0x1F) << 7) | block[i + 1]
            i += 2
            continue

        if 0xC0 <= cmd <= 0xEF:
            size = 3 if cmd < 0xD0 else 4 if cmd < 0xE0 else 5
            if i + size > len(block):
                warnings.append(ParseWarning(track_index, i, "truncated note event"))
                break
            if cmd < 0xD0:
                gate = cmd & 0x0F
            elif cmd < 0xE0:
                gate = ((cmd & 0x0F) << 7) | block[i + 1]
            else:
                gate = ((cmd & 0x0F) << 14) | (block[i + 1] << 7) | block[i + 2]
            pitch, velocity = block[i + size - 2 : i + size]
            if pitch > 127 or velocity > 127:
                warnings.append(ParseWarning(track_index, i, "note outside MIDI data range"))
                break
            notes += 1
            events.append(MidiEvent(tick, 2, bytes([0x90 | channel, pitch, max(1, velocity)]), notes))
            events.append(MidiEvent(tick + max(1, gate), 0, bytes([0x80 | channel, pitch, 0]), notes))
            i += size
            continue

        if cmd == 0xFB:
            if i + 2 >= len(block):
                warnings.append(ParseWarning(track_index, i, "truncated control-change event"))
                break
            controller = block[i + 1]
            value = block[i + 2]
            if controller > 127 or value > 127:
                warnings.append(ParseWarning(track_index, i, "control change outside MIDI data range"))
                break
            events.append(MidiEvent(tick, 1, bytes([0xB0 | channel, controller, value])))
            i += 3
            continue

        if cmd == 0xFE:
            if i + 2 >= len(block):
                warnings.append(ParseWarning(track_index, i, "truncated pitch-bend event"))
                break
            lsb = block[i + 1]
            msb = block[i + 2]
            if lsb > 127 or msb > 127:
                warnings.append(ParseWarning(track_index, i, "pitch bend outside MIDI data range"))
                break
            events.append(MidiEvent(tick, 1, bytes([0xE0 | channel, lsb, msb])))
            i += 3
            continue

        if cmd == 0xFC:
            if i + 4 >= len(block):
                warnings.append(ParseWarning(track_index, i, "truncated bank/program event"))
                break
            flag, msb, lsb, program = block[i + 1 : i + 5]
            if flag not in (0, 1) or max(msb, lsb, program) > 127:
                warnings.append(ParseWarning(track_index, i, "invalid bank/program event"))
                break
            events.extend([
                MidiEvent(tick, 1, bytes([0xB0 | channel, 0, msb])),
                MidiEvent(tick, 1, bytes([0xB0 | channel, 32, lsb])),
                MidiEvent(tick, 1, bytes([0xC0 | channel, program])),
            ])
            i += 5
            continue

        if cmd == 0xF3:
            if i + 5 >= len(block):
                warnings.append(ParseWarning(track_index, i, "truncated F3 event"))
                break
            # Its meaning is unknown. Preserve the bytes as a MIDI
            # sequencer-specific meta event rather than discarding them.
            events.append(MidiEvent(tick, 1, meta_event(0x7F, b"RM1x R1S F3\0" + block[i : i + 6])))
            i += 6
            continue

        if cmd == 0xF6:
            end = block.find(b"\xF7", i + 1)
            if end < 0:
                warnings.append(ParseWarning(track_index, i, "unterminated SysEx"))
                break
            payload = block[i + 1 : end + 1]
            if any(value > 127 for value in payload[:-1]):
                warnings.append(ParseWarning(track_index, i, "invalid SysEx data"))
                break
            events.append(MidiEvent(tick, 1, b"\xF0" + write_varlen(len(payload)) + payload))
            i = end + 1
            continue

        warnings.append(ParseWarning(track_index, i, f"unsupported command 0x{cmd:02X}"))
        break

    return events, warnings, complete, notes


def tempo_event(bpm: float) -> bytes:
    if not 0 < bpm <= 1000:
        raise ValueError(f"invalid BPM: {bpm}")
    return meta_event(0x51, round(60_000_000 / bpm).to_bytes(3, "big"))


def parse_conductor(block: bytes) -> tuple[list[MidiEvent], list[ParseWarning]]:
    # The BPM and meter streams can occupy separate 128-byte-padded regions.
    # Each stream starts with F0 00 at a page boundary and has its own clock.
    events: list[MidiEvent] = []
    warnings: list[ParseWarning] = []
    starts = [offset for offset in range(0, len(block), PAGE_SIZE)
              if block[offset:offset + 2] == b"\xF0\x00"]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(block)
        stream_events, stream_warnings = _parse_conductor_stream(block[start:end], start)
        events.extend(stream_events)
        warnings.extend(stream_warnings)
    return events, warnings


def _parse_conductor_stream(block: bytes, base_offset: int) -> tuple[list[MidiEvent], list[ParseWarning]]:
    events: list[MidiEvent] = []
    warnings: list[ParseWarning] = []
    i, tick, measure_ticks = 2, 0, 4 * PPQN
    while i < len(block):
        cmd = block[i]
        if cmd in (0xF2, 0):
            break
        if 0x80 <= cmd <= 0x9F:
            tick += cmd & 0x1F
            i += 1
        elif 0xA0 <= cmd <= 0xBF and i + 2 <= len(block):
            tick += ((cmd & 0x1F) << 7) | block[i + 1]
            i += 2
        elif 0xC0 <= cmd <= 0xCF and i + 2 <= len(block):
            tick += (((cmd & 0x0F) << 7) | block[i + 1]) * measure_ticks
            i += 2
        elif cmd == 0xF5 and i + 3 <= len(block):
            bpm = (block[i + 1] | (block[i + 2] << 7)) / 10
            if not 25 <= bpm <= 300:
                warnings.append(ParseWarning(0, base_offset + i, f"invalid tempo {bpm:g}"))
                break
            events.append(MidiEvent(tick, 1, tempo_event(bpm)))
            i += 3
        elif cmd == 0xF7 and i + 2 <= len(block):
            code = block[i + 1]
            # In these R1S songs the low three bits are 4, and
            # 0C/14/1C/24/2C encode 2/4, 3/4, 4/4, 5/4, 6/4. They agree with
            # the following conductor events and the musical track endings.
            # Other denominator codes have not been established by this corpus.
            if (code & 7) != 4 or not 1 <= (code >> 3) + 1 <= 8:
                warnings.append(ParseWarning(0, base_offset + i, f"unknown meter code 0x{code:02X}"))
                break
            numerator = (code >> 3) + 1
            measure_ticks = numerator * PPQN
            events.append(MidiEvent(tick, 1, meta_event(0x58, bytes([numerator, 2, 24, 8]))))
            i += 2
        else:
            warnings.append(ParseWarning(0, base_offset + i,
                                         f"unknown conductor event 0x{cmd:02X}"))
            break
    return events, warnings


def initial_voice_events(record: SongRecord, track_index: int) -> list[MidiEvent]:
    data = record.data
    channel = track_index - 1
    i = track_index - 1
    if len(data) < 0xC0:
        return []
    msb, lsb, program = (data[offset + i] for offset in (0x20, 0x30, 0x40))
    # The 16-byte arrays at these offsets match the initial CC/PC events in
    # the factory demos. 0x90 is chorus, 0xA0 reverb and 0xB0 variation send.
    controls = [(0, msb), (32, lsb), (7, data[0x60 + i]),
                (10, data[0x70 + i]), (11, data[0x80 + i]),
                (93, data[0x90 + i]), (91, data[0xA0 + i]),
                (94, data[0xB0 + i])]
    events = [MidiEvent(0, -2, bytes([0xB0 | channel, cc, value]))
              for cc, value in controls]
    events.insert(2, MidiEvent(0, -2, bytes([0xC0 | channel, program])))
    return events


def initial_effect_events(record: SongRecord) -> list[MidiEvent]:
    # These three pairs match the documented XG effect type MSB/LSB values
    # throughout the corpus: e.g. 04 00 = Plate reverb, 41 01 = Chorus 2,
    # 05 00 = Delay L,C,R variation. The other parameters need more decoding.
    settings = ((0x1A0, 0x00), (0x1A7, 0x20), (0x1AF, 0x40))
    events = []
    for offset, address in settings:
        if len(record.data) < offset + 2:
            continue
        effect_type = record.data[offset:offset + 2]
        if any(value > 127 for value in effect_type):
            continue
        payload = bytes.fromhex("43 10 4c 02 01") + bytes([address]) + effect_type + b"\xF7"
        events.append(MidiEvent(0, -2, b"\xF0" + write_varlen(len(payload)) + payload))
    return events


def on_channel(event: MidiEvent, channel: int, pitch: int | None = None) -> MidiEvent:
    status = event.data[0]
    if 0x80 <= status <= 0xEF:
        data = bytes([(status & 0xF0) | channel]) + event.data[1:]
        if pitch is not None:
            data = bytes([data[0], pitch, data[2]])
        return MidiEvent(event.tick, event.priority,
                         data, event.note_id)
    return event


def gm_drum_pitch(bank: int, pitch: int) -> int:
    # Yamaha's RM1x drum table places a second kick/snare/tom/hi-hat row at
    # notes 23..34, one octave below the corresponding GM instruments at
    # 35..46. Bank 127 is already a GM drum bank and must keep its notes.
    return pitch + 12 if bank == 126 and 23 <= pitch <= 34 else pitch


# RM1x bank 63/5 also contains individual, non-pitched percussion voices.
# Values are 1-based RM1x programs -> GM percussion notes. Some substitutions
# are approximate; pitched instruments in that bank stay on melodic channels.
# See the Yamaha RM1x Voice and GM Drum Voice lists.
GM_PERCUSSION_VOICES = {
    41: 52, 42: 52,              # Gong, Thai gong -> Chinese cymbal
    44: 35,                      # Taiko drum -> acoustic bass drum
    48: 64,                      # Udu -> low conga
    52: 87,                      # Surdo CO -> open surdo
    53: 54, 54: 54,              # Tambourine, analog tambourine
    55: 56, 56: 56, 57: 56,     # Cowbells
    58: 58, 59: 58, 60: 58,     # Vibraslaps
    61: 60,                      # Bongo -> high bongo
    67: 69, 68: 70,              # Cabasa, maracas
    69: 71,                      # Short whistle
    70: 73, 71: 73,              # Guiros
    72: 75, 73: 75,              # Claves
    74: 76,                      # Woodblock
    75: 78,                      # Cuica
    76: 81,                      # Triangle
    77: 82, 78: 82,              # Shakers
    79: 83, 80: 84,              # Jingle bell, bell tree
    81: 84, 82: 84,              # Wind chimes -> closest GM sound: bell tree
    83: 85,                      # Castanet
    86: 39, 87: 39, 88: 39, 89: 39,  # Hand claps
    103: 38,                     # Reverse roll -> acoustic snare (rough substitute)
}


def gm_percussion_pitch(bank: int, subbank: int, program: int, pitch: int) -> int | None:
    if bank in (126, 127):
        return gm_drum_pitch(bank, pitch)
    if bank == 63 and subbank == 5:
        return GM_PERCUSSION_VOICES.get(program)
    return None


def split_gm_track(record: SongRecord, track_index: int, events: list[MidiEvent],
                   melody_channel: int) -> tuple[list[MidiEvent], list[MidiEvent]]:
    """Route each note according to the voice active at its onset.

    Each note's end follows its onset even when the bank changes during the
    note. Drum setup/controls are omitted because all drums share GM channel
    10; melodic setup and controls remain on their individual channel.
    """
    source_channel = track_index - 1
    bank = record.data[0x20 + source_channel]
    subbank = record.data[0x30 + source_channel]
    program = record.data[0x40 + source_channel] + 1
    note_routes: dict[int, tuple[bool, int]] = {}
    drums: list[MidiEvent] = []
    melody: list[MidiEvent] = []
    for event in sorted(events, key=lambda item: (item.tick, item.priority)):
        data = event.data
        status = data[0]
        if len(data) == 3 and data[:2] == bytes([0xB0 | source_channel, 0]):
            bank = data[2]
        elif len(data) == 3 and data[:2] == bytes([0xB0 | source_channel, 32]):
            subbank = data[2]
        elif len(data) == 2 and status == (0xC0 | source_channel):
            program = data[1] + 1
        if status == (0x90 | source_channel):
            drum_pitch = gm_percussion_pitch(bank, subbank, program, data[1])
            is_drum = drum_pitch is not None
            pitch = drum_pitch if is_drum else data[1]
            if event.note_id is not None:
                note_routes[event.note_id] = (is_drum, pitch)
        elif status == (0x80 | source_channel):
            drum_pitch = gm_percussion_pitch(bank, subbank, program, data[1])
            is_drum, pitch = note_routes.pop(event.note_id,
                                             (drum_pitch is not None,
                                              drum_pitch if drum_pitch is not None else data[1]))
        else:
            is_drum = gm_percussion_pitch(bank, subbank, program, 60) is not None
        if status in (0x80 | source_channel, 0x90 | source_channel):
            (drums if is_drum else melody).append(
                on_channel(event, 9 if is_drum else melody_channel, pitch)
            )
        elif not is_drum:
            melody.append(on_channel(event, melody_channel))
    return drums, melody


def write_varlen(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative delta time is invalid")
    buffer = [value & 0x7F]
    value >>= 7
    while value:
        buffer.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(buffer))


def meta_event(meta_type: int, payload: bytes) -> bytes:
    return b"\xFF" + bytes([meta_type]) + write_varlen(len(payload)) + payload


def build_track_chunk(name: str, events: Iterable[MidiEvent]) -> bytes:
    body = bytearray()
    body += write_varlen(0)
    body += meta_event(0x03, name.encode("ascii", "replace")[:127])

    sorted_events = sorted(events, key=lambda event: (event.tick, event.priority))
    current_tick = 0
    for event in sorted_events:
        body += write_varlen(event.tick - current_tick)
        body += event.data
        current_tick = event.tick

    body += write_varlen(0)
    body += meta_event(0x2F, b"")
    return b"MTrk" + len(body).to_bytes(4, "big") + body


def build_midi(song_name: str, tracks: list[tuple[str, list[MidiEvent]]],
               bpm: float, conductor: list[MidiEvent]) -> bytes:
    if not any(event.tick == 0 and event.data.startswith(b"\xFF\x51") for event in conductor):
        conductor.insert(0, MidiEvent(0, -1, tempo_event(bpm)))
    if not any(event.tick == 0 and event.data.startswith(b"\xFF\x58") for event in conductor):
        conductor.insert(0, MidiEvent(0, -1, meta_event(0x58, bytes([4, 2, 24, 8]))))
    chunks = [build_track_chunk(song_name, conductor)]
    for label, events in tracks:
        chunks.append(build_track_chunk(label, events))

    header = b"MThd" + (6).to_bytes(4, "big")
    header += (1).to_bytes(2, "big")
    header += len(chunks).to_bytes(2, "big")
    header += PPQN.to_bytes(2, "big")
    return header + b"".join(chunks)


def note_on_count(events: list[MidiEvent]) -> int:
    return sum(len(event.data) == 3 and (event.data[0] & 0xF0) == 0x90
               and event.data[2] > 0 for event in events)


def convert_file(path: Path, output_dir: Path, bpm: float, verbose: bool,
                 gm_drums: bool = False) -> tuple[Path, bool, list[ParseWarning], float]:
    data = path.read_bytes()
    tracks: list[tuple[int, list[MidiEvent]]] = []
    warnings: list[ParseWarning] = []
    all_complete = True
    blocks = read_track_blocks(data)
    end = tracks_end(data)
    record = find_song_record(data, end)
    inferred_bpm = record.bpm if record else bpm
    if record is None:
        warnings.append(ParseWarning(0, end, "song settings record not found; using fallback tempo"))
    conductor, conductor_warnings = parse_conductor(data[end:record.offset] if record else b"")
    warnings.extend(conductor_warnings)
    if record:
        conductor[:0] = initial_effect_events(record)
    for track_index, block in blocks:
        events, track_warnings, complete, notes = parse_track_block(track_index, block)
        warnings.extend(track_warnings)
        if not complete and not track_warnings:
            warnings.append(ParseWarning(track_index, len(block), "track did not end cleanly"))
        if not events:
            all_complete &= complete
            continue
        if record:
            events = initial_voice_events(record, track_index) + events
        if events:
            tracks.append((track_index, events))
        all_complete &= complete
        if verbose:
            print(
                f"{path.name}: track {track_index:02d}: {notes} notes, complete={complete}",
                file=sys.stderr,
            )

    if not tracks:
        raise ValueError("no musical tracks could be parsed")

    output_tracks: list[tuple[str, list[MidiEvent]]] = []
    if record:
        split = {number: split_gm_track(record, number, events, number - 1)
                 for number, events in tracks}
        has_drums = any(note_on_count(drum) for drum, _ in split.values())
        if gm_drums and has_drums:
            # A melodic RM1x track 10 vacates GM's drum channel. Prefer a
            # channel released by a track containing only drum notes.
            spare = next((number - 1 for number, (drum, melody) in split.items()
                          if number != 10 and note_on_count(drum) and not note_on_count(melody)), None)
            if spare is None:
                used = {number - 1 for number, _ in tracks}
                spare = next((channel for channel in range(16)
                              if channel != 9 and channel not in used), None)
            if 10 in split and note_on_count(split[10][1]) and spare is None:
                raise ValueError("no free channel for melodic material in track 10")
            for number, events in tracks:
                melody_channel = spare if number == 10 and spare is not None else number - 1
                drum, melody = split_gm_track(record, number, events, melody_channel)
                if note_on_count(drum):
                    output_tracks.append((f"GM drums {number:02d} (RM1x source)", drum))
                if melody and (note_on_count(melody) or not drum):
                    output_tracks.append((f"RM1x voice {number:02d}", melody))
        else:
            for number, events in tracks:
                drum, melody = split[number]
                drums = note_on_count(drum)
                voices = note_on_count(melody)
                program = record.data[0x40 + number - 1] + 1
                if drums and voices:
                    label = f"RM1x mixed {number:02d} (drums + voice)"
                elif drums:
                    bank = record.data[0x20 + number - 1]
                    if bank in (126, 127):
                        label = f"RM1x drums {number:02d} (kit {program})"
                    else:
                        label = f"RM1x percussion {number:02d} (voice {program})"
                else:
                    label = f"RM1x voice {number:02d} (program {program})"
                output_tracks.append((label, events))
    else:
        output_tracks = [(f"RM1x track {number:02d}", events) for number, events in tracks]

    midi_data = build_midi(record.name if record else path.stem,
                           output_tracks, inferred_bpm, conductor)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{path.stem}.mid"
    out_path.write_bytes(midi_data)
    return out_path, all_complete, warnings, inferred_bpm


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    exit_code = 0

    for raw_input in args.inputs:
        path = Path(raw_input)
        try:
            out_path, complete, warnings, used_bpm = convert_file(
                path, output_dir, args.bpm, args.verbose, args.gm_drums
            )
        except Exception as exc:  # pragma: no cover - CLI surface
            print(f"{path}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        status = "complete" if complete else "partial"
        print(f"{path.name} -> {out_path} [{status}, bpm={used_bpm:g}]")
        for warning in warnings:
            print(
                f"  warning track {warning.track_index:02d} @ 0x{warning.offset:04X}: {warning.message}",
                file=sys.stderr,
            )
        if warnings or not complete:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
