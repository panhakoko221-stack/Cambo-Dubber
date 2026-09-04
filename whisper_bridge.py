"""Run local Whisper in the configured CUDA-capable model environment."""

import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="base")
    args = parser.parse_args()

    import torch
    import whisper

    if not torch.cuda.is_available():
        raise RuntimeError(
            "The configured local-model Python cannot access CUDA. "
            "Install a CUDA-enabled PyTorch build or choose another Python environment."
        )

    device = "cuda"
    print(f"Using Whisper device: {torch.cuda.get_device_name(0)}", flush=True)
    model = whisper.load_model(args.model, device=device)
    result = model.transcribe(args.audio, fp16=True)
    segments = [
        {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": (segment.get("text") or "").strip(),
        }
        for segment in result.get("segments", [])
    ]
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(segments, handle, ensure_ascii=False)


if __name__ == "__main__":
    main()
