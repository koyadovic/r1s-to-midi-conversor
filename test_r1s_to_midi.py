# SPDX-License-Identifier: GPL-1.0-only
"""Format-level regression tests for the RM1x R1S to MIDI converter.

Synthetic streams verify byte encodings. Every available R1S file also joins
the integration checks automatically, without selecting files by song name.
"""

import tempfile
import unittest
from collections import Counter
from pathlib import Path

try:
    import mido
except ImportError:  # The converter itself has no third-party dependencies.
    mido = None

from r1s_to_midi import (
    MidiEvent,
    SongRecord,
    convert_file,
    find_song_record,
    gm_drum_pitch,
    gm_percussion_pitch,
    initial_effect_events,
    meta_event,
    note_on_count,
    parse_conductor,
    parse_track_block,
    read_track_blocks,
    split_gm_track,
    tracks_end,
)


def make_record(bank=0, subbank=0, program=1):
    """Build a synthetic settings record; program is 1-based."""
    data = bytearray(0x200)
    data[0x20] = bank
    data[0x30] = subbank
    data[0x40] = program - 1
    return SongRecord(0, "", 120, bytes(data))


class R1SDecoderTests(unittest.TestCase):
    def test_waits_and_note_gate_encodings(self):
        block = bytes.fromhex(
            "f0 00 bf 7f c5 3c 64 d1 58 3d 64 e0 35 2b 3e 64 "
            "e2 18 46 3f 64 f2"
        )
        events, warnings, complete, notes = parse_track_block(1, block)
        self.assertTrue(complete)
        self.assertEqual(warnings, [])
        self.assertEqual(notes, 4)
        starts = [event for event in events if event.data[0] == 0x90]
        ends = [event for event in events if event.data[0] == 0x80]
        self.assertEqual([event.tick for event in starts], [4095] * 4)
        self.assertEqual([event.tick for event in ends],
                         [4100, 4095 + 216, 4095 + (0x35 << 7) + 0x2B,
                          4095 + (2 << 14) + (0x18 << 7) + 0x46])

    def test_meter_codes_and_measure_waits(self):
        block = bytes.fromhex(
            "f0 00 f7 0c c0 01 f7 14 c0 02 f7 1c c0 01 "
            "f7 24 c0 01 f7 2c f2"
        )
        events, warnings = parse_conductor(block)
        self.assertEqual(warnings, [])
        self.assertEqual([(event.tick, event.data) for event in events], [
            (0, meta_event(0x58, bytes([2, 2, 24, 8]))),
            (960, meta_event(0x58, bytes([3, 2, 24, 8]))),
            (3840, meta_event(0x58, bytes([4, 2, 24, 8]))),
            (5760, meta_event(0x58, bytes([5, 2, 24, 8]))),
            (8160, meta_event(0x58, bytes([6, 2, 24, 8]))),
        ])

    def test_unknown_meter_is_reported(self):
        events, warnings = parse_conductor(bytes.fromhex("f0 00 f7 15 f2"))
        self.assertEqual(events, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown meter code", warnings[0].message)

    def test_separate_tempo_and_meter_streams(self):
        tempo = bytes.fromhex("f0 00 f5 30 09 c0 02 f5 14 05 f2")
        meter = bytes.fromhex("f0 00 f7 1c c0 05 f7 14 f2")
        block = tempo.ljust(256, b"\x00") + meter.ljust(128, b"\x00")
        events, warnings = parse_conductor(block)
        self.assertEqual(warnings, [])
        self.assertEqual([(event.tick, event.data[3]) for event in events
                          if event.data.startswith(b"\xff\x58")],
                         [(0, 4), (9600, 3)])
        self.assertEqual([event.tick for event in events
                          if event.data.startswith(b"\xff\x51")], [0, 3840])

    def test_initial_effect_types(self):
        data = bytearray(make_record().data)
        data[0x1A0:0x1A2] = bytes.fromhex("04 00")
        data[0x1A7:0x1A9] = bytes.fromhex("41 01")
        data[0x1AF:0x1B1] = bytes.fromhex("05 00")
        events = initial_effect_events(SongRecord(0, "", 120, bytes(data)))
        self.assertEqual([event.data for event in events], [
            bytes.fromhex("f0 09 43 10 4c 02 01 00 04 00 f7"),
            bytes.fromhex("f0 09 43 10 4c 02 01 20 41 01 f7"),
            bytes.fromhex("f0 09 43 10 4c 02 01 40 05 00 f7"),
        ])

    def test_drum_bank_pitch_and_note_end(self):
        events = [MidiEvent(0, 2, bytes([0x90, 30, 100]), 1),
                  MidiEvent(40, 0, bytes([0x80, 30, 0]), 1)]
        drums, melody = split_gm_track(make_record(bank=126), 1, events, 0)
        self.assertEqual(melody, [])
        self.assertEqual([event.data for event in drums],
                         [bytes([0x99, 42, 100]), bytes([0x89, 42, 0])])
        self.assertEqual(gm_drum_pitch(127, 30), 30)

    def test_voice_identity_and_program_change_control_routing(self):
        self.assertEqual(gm_percussion_pitch(63, 5, 41, 60), 52)  # gong
        self.assertEqual(gm_percussion_pitch(63, 5, 44, 60), 35)  # taiko
        self.assertEqual(gm_percussion_pitch(63, 5, 48, 60), 64)  # udu
        self.assertEqual(gm_percussion_pitch(63, 5, 52, 60), 87)  # surdo
        self.assertEqual(gm_percussion_pitch(63, 5, 61, 60), 60)  # bongo
        self.assertEqual(gm_percussion_pitch(63, 5, 103, 60), 38)  # reverse roll
        self.assertEqual(gm_percussion_pitch(63, 5, 53, 60), 54)  # tambourine
        self.assertEqual(gm_percussion_pitch(63, 5, 81, 53), 84)  # wind chime
        self.assertIsNone(gm_percussion_pitch(63, 5, 16, 60))
        self.assertIsNone(gm_percussion_pitch(63, 5, 19, 60))  # pitched drone
        events = [MidiEvent(0, 2, bytes([0x90, 60, 100]), 1),
                  MidiEvent(20, 1, bytes([0xC0, 15])),
                  MidiEvent(40, 0, bytes([0x80, 60, 0]), 1),
                  MidiEvent(50, 2, bytes([0x90, 60, 100]), 2),
                  MidiEvent(80, 0, bytes([0x80, 60, 0]), 2)]
        drums, melody = split_gm_track(
            make_record(bank=63, subbank=5, program=68), 1, events, 0
        )
        self.assertEqual([event.data for event in drums],
                         [bytes([0x99, 70, 100]), bytes([0x89, 70, 0])])
        self.assertEqual([event.data for event in melody],
                         [bytes([0xC0, 15]), bytes([0x90, 60, 100]),
                          bytes([0x80, 60, 0])])

    def test_pitched_drone_preserves_pitch_and_gate(self):
        events = [MidiEvent(0, 2, bytes([0x90, 60, 90]), 1),
                  MidiEvent(7200, 0, bytes([0x80, 60, 0]), 1)]
        drums, melody = split_gm_track(
            make_record(bank=63, subbank=5, program=19), 1, events, 0
        )
        self.assertEqual(drums, [])
        self.assertEqual([(event.tick, event.data) for event in melody],
                         [(0, bytes([0x90, 60, 90])),
                          (7200, bytes([0x80, 60, 0]))])

    def test_bank_change_keeps_note_end_on_original_channel(self):
        events = [MidiEvent(0, 2, bytes([0x90, 36, 100]), 1),
                  MidiEvent(20, 1, bytes([0xB0, 0, 63])),
                  MidiEvent(40, 0, bytes([0x80, 36, 0]), 1),
                  MidiEvent(50, 2, bytes([0x90, 60, 100]), 2),
                  MidiEvent(80, 0, bytes([0x80, 60, 0]), 2)]
        drums, melody = split_gm_track(make_record(bank=126), 1, events, 4)
        self.assertEqual([event.data for event in drums],
                         [bytes([0x99, 36, 100]), bytes([0x89, 36, 0])])
        self.assertEqual([event.data for event in melody],
                         [bytes([0xB4, 0, 63]), bytes([0x94, 60, 100]),
                          bytes([0x84, 60, 0])])

    def test_every_available_song_converts_without_losing_notes(self):
        sources = sorted(Path(__file__).parent.glob("*.R1S"))
        if not sources:
            self.skipTest("place R1S files beside the tests for corpus checks")
        with tempfile.TemporaryDirectory() as temporary:
            for source in sources:
                with self.subTest(source=source.name):
                    data = source.read_bytes()
                    blocks = read_track_blocks(data)
                    record = find_song_record(data, tracks_end(data))
                    self.assertIsNotNone(record)
                    conductor, warnings = parse_conductor(
                        data[tracks_end(data):record.offset]
                    )
                    self.assertEqual(warnings, [])
                    source_notes = 0
                    expected_xg = Counter()
                    expected_gm = Counter()
                    for number, block in blocks:
                        events, warnings, complete, notes = parse_track_block(number, block)
                        self.assertEqual(warnings, [])
                        self.assertTrue(complete)
                        self.assertEqual(note_on_count(events), notes)
                        drums, melody = split_gm_track(record, number, events, number - 1)
                        self.assertEqual(note_on_count(drums) + note_on_count(melody), notes)
                        expected_xg.update(
                            (event.tick, event.data[0] & 0x0F, event.data[1], event.data[2])
                            for event in events if (event.data[0] & 0xF0) == 0x90
                        )
                        expected_gm.update(
                            (event.tick, event.data[1], event.data[2])
                            for event in drums + melody
                            if (event.data[0] & 0xF0) == 0x90
                        )
                        source_notes += notes
                    for gm_drums in (False, True):
                        output, complete, warnings, _ = convert_file(
                            source, Path(temporary) / ("gm" if gm_drums else "xg"),
                            120, False, gm_drums
                        )
                        self.assertTrue(complete)
                        self.assertEqual(warnings, [])
                        self.assertTrue(output.read_bytes().startswith(b"MThd"))
                        if mido is not None:
                            midi = mido.MidiFile(output)
                            self.assertEqual(midi.type, 1)
                            self.assertEqual(midi.ticks_per_beat, 480)
                            on = off = 0
                            actual_xg = Counter()
                            actual_gm = Counter()
                            actual_meters = []
                            actual_tempos = []
                            for track in midi.tracks:
                                tick = 0
                                for message in track:
                                    tick += message.time
                                    if message.type == "note_on" and message.velocity > 0:
                                        on += 1
                                        actual_xg[(tick, message.channel, message.note,
                                                   message.velocity)] += 1
                                        actual_gm[(tick, message.note, message.velocity)] += 1
                                    elif message.type == "note_off" or (
                                        message.type == "note_on" and message.velocity == 0
                                    ):
                                        off += 1
                                    elif message.type == "time_signature":
                                        actual_meters.append((tick, message.numerator,
                                                              message.denominator))
                                    elif message.type == "set_tempo":
                                        actual_tempos.append((tick, message.tempo))
                            self.assertEqual((on, off), (source_notes, source_notes))
                            if gm_drums:
                                self.assertEqual(actual_gm, expected_gm)
                            else:
                                self.assertEqual(actual_xg, expected_xg)
                            for event in conductor:
                                if event.data.startswith(b"\xff\x58"):
                                    self.assertIn((event.tick, event.data[3],
                                                   1 << event.data[4]), actual_meters)
                                elif event.data.startswith(b"\xff\x51"):
                                    self.assertIn((event.tick,
                                                   int.from_bytes(event.data[-3:], "big")),
                                                  actual_tempos)

    def test_input_filename_does_not_select_conversion_rules(self):
        source = next(iter(Path(__file__).parent.glob("*.R1S")), None)
        if source is None:
            self.skipTest("place an R1S file beside the tests for this check")
        with tempfile.TemporaryDirectory() as temporary:
            renamed = Path(temporary) / "input.R1S"
            renamed.write_bytes(source.read_bytes())
            output, complete, warnings, _ = convert_file(
                renamed, Path(temporary) / "output", 120, False, True
            )
            self.assertTrue(complete)
            self.assertEqual(warnings, [])
            self.assertEqual(output.name, "input.mid")


if __name__ == "__main__":
    unittest.main()
