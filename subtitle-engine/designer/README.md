# Subtitle Designer

Consumes transcription JSON without changing or importing the transcription
engine. It creates measured, centered one- or two-line cues, plus ASS karaoke
and conventional SRT. Each karaoke event keeps the corresponding word's source
start and end time. The base text is white, the active word is turquoise, and
the black outline remains inside the width limit.

## Generate subtitles for Burnei

From the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'subtitle-engine'); from designer import DesignerConfig, export_ass, export_srt, load_transcription, mark_highlights, segment_transcription, validate_cues; config = DesignerConfig.for_video(1080, 1920); transcript = mark_highlights(load_transcription('output/burnei.json'), config); cues = segment_transcription(transcript, config); validate_cues(cues, config); export_ass(cues, 'output/burnei_designer.ass', config); export_srt(cues, 'output/burnei_designer.srt'); print(f'Created {len(cues)} cues')"
```

Font family, fallback families, bold, size, margins, width ratio (maximum 85%),
vertical position, safe margins, watermark rectangle, fades, and grouping limits
are configurable in `DesignerConfig`. Windows GDI measures the installed font
when Pillow is absent; Pillow metrics are used when available. If the requested
family is missing, the resolver selects the first installed configured
fallback. The chosen family is written into ASS so rendering uses the same
resolved face.

`DesignerConfig.for_video(width, height)` sets the script resolution and an
orientation-aware vertical position for portrait or landscape output. The
default watermark reservation
and vertical position suit the 1080x1920 Burnei preview. Adjust
`watermark_box=(left, top, right, bottom)` and `vertical_position` using
normalized 0–1 coordinates for another composition. Layout validation rejects
lines wider than 85%, blocks with more than two lines, and blocks overlapping
the reserved area. SRT carries the same line breaks and cue times; styling and
karaoke color are available in ASS only.

## Render a separate preview with FFmpeg

After generating the subtitle files:

```powershell
ffmpeg -y -i input/burnei.mp4 -vf "subtitles=filename='output/burnei_designer.ass'" -c:v libx264 -crf 18 -preset medium -c:a copy output/burnei_preview_corrected.mp4
```

The command writes a new preview and leaves `output/burnei_preview.mp4`
untouched.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s subtitle-engine/designer/tests -v
```

Designer tests use `output/burnei.json` where available and do not call FFmpeg
or load a speech model.
