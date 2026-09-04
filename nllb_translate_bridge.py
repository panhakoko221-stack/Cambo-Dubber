import argparse
import json
import os
import re
import sys


def detect_source_lang(text):
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zho_Hans"
    return "eng_Latn"


def load_payload(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_results(path, results):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def translate_group(model, tokenizer, texts, src_lang, tgt_lang, device):
    tokenizer.src_lang = src_lang
    encoded = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=256,
    )
    encoded = {key: value.to(device) for key, value in encoded.items()}
    forced_bos_token_id = tokenizer.convert_tokens_to_ids(tgt_lang)
    generated = model.generate(
        **encoded,
        forced_bos_token_id=forced_bos_token_id,
        max_new_tokens=128,
        num_beams=4,
    )
    return tokenizer.batch_decode(generated, skip_special_tokens=True)


def run(batch_path, output_path):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    payload = load_payload(batch_path)
    model_id = payload.get("model_id") or "facebook/nllb-200-distilled-600M"
    tgt_lang = payload.get("target_lang") or "khm_Khmr"
    tasks = payload.get("tasks", [])

    if not tasks:
        save_results(output_path, [])
        return

    use_device_map = torch.cuda.is_available()
    model_revision = payload.get("model_revision") or "refs/pr/39"
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=model_revision)
    model_kwargs = {
        "revision": model_revision,
        "use_safetensors": True,
        "low_cpu_mem_usage": False,
    }
    if use_device_map:
        model_kwargs["device_map"] = "auto"
        model_kwargs["torch_dtype"] = torch.float16
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id, **model_kwargs)
    if use_device_map:
        device = next(model.parameters()).device
    else:
        device = "cpu"
        model.to(device)
    model.eval()

    grouped = {}
    for item in tasks:
        text = (item.get("text") or "").strip()
        src_lang = item.get("source_lang") or detect_source_lang(text)
        grouped.setdefault(src_lang, []).append(item)

    results_by_id = {}
    with torch.no_grad():
        for src_lang, group_items in grouped.items():
            texts = [(item.get("text") or "").strip() for item in group_items]
            translations = translate_group(model, tokenizer, texts, src_lang, tgt_lang, device)
            for item, translated in zip(group_items, translations):
                results_by_id[str(item.get("id"))] = translated.strip()

    ordered = []
    for item in tasks:
        id_val = str(item.get("id"))
        ordered.append({
            "id": id_val,
            "text": results_by_id.get(id_val, (item.get("text") or "").strip()),
        })
    save_results(output_path, ordered)


def main():
    parser = argparse.ArgumentParser(description="Local NLLB subtitle translator bridge")
    parser.add_argument("--batch", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.batch, args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc).encode("unicode_escape").decode("ascii"), file=sys.stderr)
        raise
