import re


def normalize_eval_search_text(search_text):
    return re.sub(r"\s+", "_", str(search_text or "").strip().lower())
