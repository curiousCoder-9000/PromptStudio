"""
Backward-compatible shim for prompt_engine.py imports.
All logic lives in promptstudio.prompts.*
"""

from promptstudio.config import (
    EROTIC_INTENSITY,
    MODEL_NAME,
    OLLAMA_URL,
    PROMPT_CACHE_FILE,
    REALISM_BIAS,
)
from promptstudio.prompts.cache import get_or_create_prompt_cache, save_prompt_cache
from promptstudio.prompts.engine import (
    analyze_with_ollama_vision,
    build_vision_prompt,
    clean_vision_output,
    encode_image_to_base64,
    generate_prompt_for_image,
    get_image_aspect_ratio,
    get_prompt_for_image,
)

__all__ = [
    "OLLAMA_URL",
    "MODEL_NAME",
    "PROMPT_CACHE_FILE",
    "EROTIC_INTENSITY",
    "REALISM_BIAS",
    "get_or_create_prompt_cache",
    "save_prompt_cache",
    "encode_image_to_base64",
    "clean_vision_output",
    "build_vision_prompt",
    "analyze_with_ollama_vision",
    "get_image_aspect_ratio",
    "generate_prompt_for_image",
    "get_prompt_for_image",
]

if __name__ == "__main__":
    import glob
    import json
    import os
    import sys

    if len(sys.argv) > 1:
        test_file = sys.argv[1]
    else:
        candidates = glob.glob(
            os.path.expanduser("~/Pictures/InstagramSaved/**/*.jpg"), recursive=True
        )
        test_file = candidates[0] if candidates else None

    if not test_file or not os.path.exists(test_file):
        print("No test image found. Pass a path: py prompt_engine.py /path/to/image.jpg")
        sys.exit(1)

    print(f"Testing Ollama Vision Prompt Engine on:\n{test_file}\n")
    res = generate_prompt_for_image(test_file, "test_creator")
    print("--- POSITIVE PROMPT ---")
    print(res["positive_prompt"])
