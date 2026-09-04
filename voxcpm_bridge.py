import argparse
import hashlib
import inspect
import json
import os
import random
import subprocess
import sys
import tempfile


def _to_float(value, default):
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def _convert_reference_audio(path):
    if not path:
        return ""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Reference audio not found: {path}")

    reference_id = hashlib.sha1(os.path.abspath(path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    temp_wav = os.path.join(tempfile.gettempdir(), f"voxcpm_reference_{reference_id}.wav")
    cmd = [
        "ffmpeg", "-y", "-hwaccel", "none", "-i", path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        temp_wav
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return temp_wav


def _load_model(model_id):
    import torch
    from voxcpm import VoxCPM

    device = "cuda" if torch.cuda.is_available() else None
    _log(f"status\tUsing VoxCPM device: {device or 'auto/cpu'}")
    try:
        return VoxCPM.from_pretrained(model_id, load_denoiser=False, device=device)
    except TypeError:
        return VoxCPM.from_pretrained(model_id, device=device)


def _write_audio(output_path, wav, model):
    import soundfile as sf

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    sample_rate = getattr(getattr(model, "tts_model", None), "sample_rate", 48000)
    sf.write(output_path, wav, sample_rate)


def _log(message):
    print(message, flush=True)


def _apply_seed(seed):
    if seed is None:
        return

    try:
        seed = int(seed)
    except Exception:
        return

    random.seed(seed)

    try:
        import numpy as np
        np.random.seed(seed % (2 ** 32 - 1))
    except Exception:
        pass

    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass


def _supported_generate_kwargs(model):
    generate_impl = getattr(model, "_generate", None) or getattr(model, "generate", None)
    if generate_impl is None:
        return None

    try:
        signature = inspect.signature(generate_impl)
    except (TypeError, ValueError):
        return None

    parameters = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return None

    return set(parameters)


def _filter_generate_kwargs(model, kwargs):
    supported_kwargs = _supported_generate_kwargs(model)
    if supported_kwargs is None:
        return kwargs

    return {key: value for key, value in kwargs.items() if key in supported_kwargs}


def _generate(model, text, reference_wav, prompt_text, style, cfg_value, steps, seed):
    styled_text = f"({style}){text}" if style and not text.strip().startswith("(") else text
    _apply_seed(seed)
    base_args = {
        "cfg_value": cfg_value,
        "inference_timesteps": steps,
        "seed": seed,
    }

    attempts = []
    if reference_wav and prompt_text:
        attempts.append({
            "text": styled_text,
            "prompt_wav_path": reference_wav,
            "prompt_text": prompt_text,
            "reference_wav_path": reference_wav,
            **base_args,
        })
        attempts.append({
            "text": styled_text,
            "prompt_wav_path": reference_wav,
            "prompt_text": prompt_text,
            **base_args,
        })
    elif reference_wav:
        attempts.append({
            "text": styled_text,
            "reference_wav_path": reference_wav,
            **base_args,
        })
        attempts.append({
            "text": styled_text,
            "prompt_wav_path": reference_wav,
            "prompt_text": prompt_text,
            **base_args,
        })
    else:
        attempts.append({"text": styled_text, **base_args})

    last_error = None
    for kwargs in attempts:
        try:
            return model.generate(**_filter_generate_kwargs(model, kwargs))
        except TypeError as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise RuntimeError("VoxCPM generation failed.")


def run_batch(batch_path):
    with open(batch_path, "r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    model_id = payload.get("model_id") or "openbmb/VoxCPM2"
    _log("status\tPreparing VoxCPM reference audio")
    default_reference_audio = payload.get("reference_audio", "")
    reference_cache = {}
    prompt_text = payload.get("prompt_text", "")
    style = payload.get("style", "")
    cfg_value = _to_float(payload.get("cfg_value"), 2.0)
    steps = _to_int(payload.get("inference_timesteps"), 10)
    seed = _to_int(payload.get("seed"), 42)
    tasks = payload.get("tasks", [])

    _log(f"status\tDownloading/loading VoxCPM model: {model_id}")
    model = _load_model(model_id)

    total = max(len(tasks), 1)
    for index, item in enumerate(tasks, start=1):
        text = item.get("text", "").strip()
        output = item.get("output", "").strip()
        row_idx = item.get("row_idx", index - 1)
        if not text or not output:
            continue

        reference_audio = item.get("reference_audio") or default_reference_audio
        if reference_audio not in reference_cache:
            reference_cache[reference_audio] = _convert_reference_audio(reference_audio)
        reference_wav = reference_cache[reference_audio]

        _log(f"progress\t{index}\t{total}\tGenerating VoxCPM line {index}/{total}")
        wav = _generate(model, text, reference_wav, prompt_text, style, cfg_value, steps, seed)
        _write_audio(output, wav, model)
        _log(f"saved\t{row_idx}\t{output}")


def main():
    parser = argparse.ArgumentParser(description="Cambo Dubber VoxCPM bridge")
    parser.add_argument("--batch", required=True, help="Path to VoxCPM batch JSON")
    args = parser.parse_args()
    run_batch(args.batch)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
