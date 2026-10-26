# Yamaha RM1x R1S → MIDI

Convert Yamaha RM1x sequencer `.R1S` song files to Standard MIDI Files (type 1). The converter uses Python's standard library and has no runtime dependencies. The R1S binary layout was inferred from real files: Yamaha documents the instrument's voices and features, but not the on-disk song format.

## Usage

Python 3.10 or newer is required.

```bash
python r1s_to_midi.py -o converted_mid_improved /path/to/songs/*.R1S
```

This output retains the RM1x bank and program selections, note pitches, track-derived channels, and the XG settings that the converter can recover. Playing the RM1x-specific voices faithfully requires a compatible sound generator.

For a version intended for a General MIDI player:

```bash
python r1s_to_midi.py --gm-drums -o converted_mid_gm /path/to/songs/*.R1S
```

`--gm-drums` routes recognized drum kits and individual percussion voices to MIDI channel 10, using approximate GM drum notes. It is meant for listening: sounds such as gongs, taiko drums, udus, wind chimes, and reverse rolls have no exact GM counterpart. Routing uses the bank, subbank, and program active when each note starts. It never depends on the filename or on whether a note repeats. Pitched voices in the same RM1x bank remain on melodic tracks.

Each input produces a `.mid` file with the same stem in the directory given by `-o`. Use `--verbose` to show note counts by track. Use `--bpm` to set a fallback tempo if the song settings record cannot be found.

## Recovered data

- Up to 16 musical tracks, including recorded notes, note lengths, velocities, MIDI controllers, pitch bend, bank and program changes, and SysEx messages.
- Tempo and time signatures, including changes within a song, at 480 ticks per quarter note. Time-signature codes verified in the available R1S files correspond to 2/4, 3/4, 4/4, 5/4, and 6/4.
- Initial bank and program, volume, pan, expression, and reverb, chorus, and variation sends for each track.
- Initial reverb, chorus, and variation effect types as Yamaha XG messages.

R1S `F3` events have not yet been decoded. The converter preserves them as sequencer-specific MIDI metadata.

## Known limitations

The converter does not yet apply Play FX or Out Channel settings, voice edits, or all detailed effect parameters. It assigns MIDI channels by track number, so songs with special RM1x output routing may sound different. Unverified time-signature denominator codes produce a warning instead of being guessed.

The GM version substitutes approximate sounds for RM1x-specific timbres. Mapping a pitched percussion voice to a single GM drum note can lose pitch differences; the XG-oriented output retains the original note pitches. There is not yet a comparison against an SMF exported by the RM1x from the same song, so identical playback cannot be guaranteed.

References: [RM1x Owner's Manual](https://jp.yamaha.com/files/download/other_assets/2/319182/RM1xE1.pdf) and [Voice List and MIDI Implementation](https://jp.yamaha.com/files/download/other_assets/3/317923/RM1xE2.pdf).

## Tests

```bash
python -m unittest -v test_r1s_to_midi.py
```

The synthetic tests run without song files. If you place `.R1S` files in the repository root, the suite also checks their conversion in both modes. With `mido` installed, these additional checks inspect the generated MIDI structure and events. Song files are not included in this repository.

## License

This project is licensed under the GNU General Public License, version 2 only (`GPL-2.0-only`). See [LICENSE](LICENSE) for the full text.
