# Transcription engine

Local transcription is powered by Faster-Whisper, using CPU `int8` inference.
The default model is `small`; choose `base` for faster smoke runs or `tiny`,
`small`, and `medium` as needed. Models are downloaded by Faster-Whisper on
first use and cached by Hugging Face.

## Run a transcription

From the project root, install `requirements.txt` into the project virtual
environment if needed, then run:

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'subtitle-engine'); from transcription import TranscriptionConfig, TranscriptionEngine, export_json, export_srt; result = TranscriptionEngine(TranscriptionConfig(model_size='small')).transcribe('input/audio.mp3'); export_srt(result, 'output/audio.srt'); export_json(result, 'output/audio.json'); print('Language:', result.language, result.language_probability)"
```

Set `model_size='base'` for a quicker test, or `language='es'` to hint Spanish
explicitly. Leave `language=None` (the default) to detect the spoken language.
The engine requests both segment and word timestamps. JSON includes detected
language and probability, all segments, and their words, timestamps, and word
probabilities. SRT uses segment timestamps.

## Unit tests

From the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s subtitle-engine/transcription/tests -v
```

These tests use temporary placeholder files and a mocked model; they do not
download a model or require a real audio file.
