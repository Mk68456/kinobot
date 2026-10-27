"""One-time download; all subsequent transcription uses the local model directory."""
from pathlib import Path
import argparse
from huggingface_hub import snapshot_download

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=('base', 'medium', 'large-v3-turbo'), default='large-v3-turbo')
    choice = parser.parse_args().model
    repository = ('mobiuslabsgmbh/faster-whisper-large-v3-turbo' if choice == 'large-v3-turbo'
                  else f'Systran/faster-whisper-{choice}')
    destination = Path(__file__).resolve().parent.parent / "runtime" / "clips" / "models" / f"whisper-{choice}"
    snapshot_download(repository, local_dir=destination,
                      allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*", "preprocessor_config.json"])
    print(f"Model ready: {destination}")
