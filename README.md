# audio2go

**audio2go** is a local-only GNOME app that syncs a music library to USB drives, SD cards, and portable players. It can copy your files as they are, or transcode lossless tracks to MP3, AAC, or Opus so the device stays small and compatible.

Settings for each drive live on the drive itself, so a player keeps its name, color, format, and copy options when you plug it into another computer.

## Features

- Detects removable USB drives and SD cards as they are mounted
- Syncs one selected device, or every connected device in parallel
- Copies lossy files as-is; optionally transcodes lossless files
- Optional EBU R128 loudness normalization while transcoding
- Optional ReplayGain tags on copy and transcode (skips files that already have them)
- Optional cover-art embedding from tags or folder images
- Optional copy of lyric sidecars and other non-audio files
- Per-device preferences stored at `audio2go/device.json` on the volume
- Safe eject from the device row

## How it works

1. Plug in a USB drive, SD card, or portable player.
2. Choose your music folder under **Library** (defaults to the XDG Music directory, usually `~/Music`).
3. Select the device in the list. Open the gear for per-device options: name, destination folder, color, format, quality, tags, artwork, ReplayGain, and what extra files to copy.
4. Press **Sync**. Lossless tracks are converted when a transcode format is chosen; everything else is copied. Files that already exist on the device can be skipped.
5. Eject from the device row when you are done.

A toast confirms when a device config is loaded or saved. If the config file cannot be written (for example a read-only live USB), audio2go warns that the device may need to be formatted.

## Supported formats

### Convert from (library)

audio2go walks the library folder recursively and treats these extensions as audio. Hidden paths (any `.folder`) and the on-device `audio2go/` directory are ignored.

**Lossless** — copied in **Copy original** mode, transcoded when the device format is MP3, AAC, or Opus:

| Format | Extensions |
| --- | --- |
| FLAC | `.flac` |
| Wave | `.wav` |
| AIFF | `.aiff`, `.aif` |
| WavPack | `.wv` |
| Monkey's Audio | `.ape` |
| True Audio | `.tta` |
| DSD | `.dsf`, `.dff` |
| Apple Lossless | `.alac`, and `.m4a` / `.mp4` / `.m4b` when the codec is ALAC, PCM, or FLAC |

Transcoding is audio-only (the first audio stream). ffmpeg must be able to decode the source; unusual DSD or WavPack builds can fail if the local ffmpeg lacks that decoder.

**Lossy and other compressed audio** — always copied with the original filename and container. They are never re-encoded:

| Format | Extensions |
| --- | --- |
| MP3 | `.mp3` |
| Ogg Vorbis | `.ogg`, `.oga` |
| Opus | `.opus` |
| AAC | `.aac` |
| Windows Media Audio | `.wma` |
| Musepack | `.mpc` |
| MPEG-4 audio | `.m4a`, `.mp4`, `.m4b` when the codec is not lossless |

### Convert to (device)

Chosen per device under **Format**:

| Device format | What is written | Notes |
| --- | --- | --- |
| **Copy original** | Bit-for-bit copies of library files | No ffmpeg required unless ReplayGain is on |
| **MP3** | `.mp3` via LAME (`libmp3lame`) | VBR V0–V5 (default V2) or CBR 128–320 kbps |
| **AAC (M4A)** | `.m4a` | Uses `libfdk_aac` when ffmpeg has it, otherwise the native `aac` encoder. VBR 1–5 or CBR 128–320 kbps (default 192 kbps CBR) |
| **Opus** | `.opus` via `libopus` | VBR, default 160 kbps (also 96, 128, 192, 256) |

Only lossless library files are transcoded. A FLAC album becomes MP3/AAC/Opus on the device; MP3s and existing AAC files in the same album are copied unchanged.

### Lyrics and other files

**Lyric files** (optional **Copy lyric files**): `.lrc`, `.lrcx`, `.lrc64`, `.txt`, `.srt`, `.vtt`, `.krc`, `.qrc`, `.ttml`.

**Other non-audio files** (optional **Copy other files**): anything else in the library tree, such as `cover.jpg`, `.cue`, playlists, or booklets.

### Cover art

When **Embed cover art** is on, audio2go embeds a front cover if the destination file does not already have one. It prefers artwork already in the source tags, then a folder image named `cover`, `folder`, `front`, or `artwork` (`.jpg`, `.jpeg`, `.png`, `.webp`). Images can be scaled to 300–1200 px, or left at original size.

### Volume tags

- **Normalize volume** applies ffmpeg `loudnorm` (I = −16 LUFS) while transcoding, not while copying.
- **Calculate ReplayGain** measures track gain with ffmpeg and writes tags for MP3, M4A, FLAC, Ogg, and Opus. Files that already have ReplayGain or R128 tags are left alone. ffmpeg is required for this step even in copy mode.

## Requirements

- Linux with GTK 4 and libadwaita 1
- Python 3.11 or newer
- PyGObject
- [mutagen](https://mutagen.readthedocs.io/) and [Pillow](https://pillow.readthedocs.io/)
- [FFmpeg](https://ffmpeg.org/) for transcoding, loudness normalization, and ReplayGain
- `glib-compile-schemas` (from GLib) so preferences work when running from a git checkout

On Fedora:

```bash
sudo dnf install python3 gtk4 libadwaita python3-gobject \
    python3-mutagen python3-pillow ffmpeg glib2
```

## Install (AppImage)

Download the latest `audio2go-*-x86_64.AppImage` from [Releases](https://github.com/gnostiko/audio2go/releases). Mark it executable and run it:

```bash
chmod +x audio2go-*-x86_64.AppImage
./audio2go-*-x86_64.AppImage
```

FFmpeg is bundled. The image embeds GitHub update info for [Gear Lever](https://github.com/mijorus/gearlever) / AppImageUpdate.

Each `v*` tag on `main` builds the AppImage on GitHub Actions (Ubuntu 24.04, which has GTK 4.14 and libadwaita 1.5) and attaches it to the release. The tag must match `APPLICATION_VERSION` in `src/audio2go/config.py` (for example `v0.1.2`). The image needs a host glibc recent enough for Ubuntu 24.04 (GNOME 46 / Fedora 40 and newer).

To build locally:

```bash
./appimage/build-appimage.sh
```

Output lands in `dist/`.

## Run from source

```bash
git clone https://github.com/gnostiko/audio2go.git
cd audio2go
python3 -m pip install mutagen pillow
./run
```

`./run` sets `PYTHONPATH` and starts `python3 -m audio2go.main`. The GSettings schema in `data/` is compiled into a user cache on first launch.

The app is single-instance: starting it again focuses the existing window.

## Device configuration

Each volume stores preferences at:

```text
<mount>/audio2go/device.json
```

That file travels with the device (name, color, output format, quality, skip/copy flags, artwork, ReplayGain). A legacy `.audio2go/device.json` is still read if present.

The library folder on *this computer* is stored in GSettings (`org.audio2go.Audio2Go`), not on the device.

## Keyboard shortcuts

| Action | Shortcut |
| --- | --- |
| Sync selected device | Ctrl+S |
| Sync all devices | Shift+Ctrl+S |
| Stop | Escape |
| Device settings | Ctrl+D |
| Eject | Ctrl+E |
| Main menu | F10 |
| Keyboard shortcuts | Ctrl+? |
| Close window | Ctrl+W |
| Quit | Ctrl+Q |

## License

[GNU General Public License v3.0 or later](LICENSE).
