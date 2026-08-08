"""Archive organization and deduplication."""

import glob
import hashlib
import os
import re
import shutil
from typing import Callable, Dict, List, Optional, Tuple

from promptstudio.config import IMAGE_EXTENSIONS, METADATA_SUFFIX, SAVED_DIR

LogFn = Optional[Callable[[str], None]]


def organize_root_images(base_dir: str = SAVED_DIR, log: LogFn = None) -> int:
    """Move loose images in archive root into creator subfolders."""
    log = log or print
    base_dir = os.path.expanduser(base_dir)
    os.makedirs(base_dir, exist_ok=True)

    # Remove stray instaloader metadata (keep prompts_cache.json etc.)
    for pattern in ("**/*.txt",):
        for txt_file in glob.glob(os.path.join(base_dir, pattern), recursive=True):
            if "prompts_cache" in txt_file or "following_list" in txt_file:
                continue
            try:
                os.remove(txt_file)
            except OSError:
                pass

    image_files = []
    for ext in ("*.jpg", "*.webp", "*.png"):
        for f in glob.glob(os.path.join(base_dir, ext)):
            if os.path.isfile(f):
                image_files.append(f)

    moved = 0
    for img_path in image_files:
        filename = os.path.basename(img_path)
        match = re.match(r"^(.+?)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC", filename)
        if not match:
            match = re.match(r"^(.+?)_\d{4}-\d{2}-\d{2}_UTC", filename)
        username = match.group(1) if match else filename.split("_")[0]

        user_folder = os.path.join(base_dir, username)
        os.makedirs(user_folder, exist_ok=True)
        dest = os.path.join(user_folder, filename)
        if os.path.abspath(img_path) != os.path.abspath(dest):
            shutil.move(img_path, dest)
            moved += 1

    if moved:
        log(f"Organized {moved} loose images into creator folders")
    return moved


def _file_md5(file_path: str) -> Optional[str]:
    hasher = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _sidecar_identity(image_path: str) -> Tuple[str, int]:
    """Return (shortcode_or_post_id, carousel_index) from sidecar if present."""
    try:
        from promptstudio.storage.metadata import load_post_metadata

        meta = load_post_metadata(image_path) or {}
        key = str(meta.get("shortcode") or meta.get("post_id") or "")
        idx = int(meta.get("carousel_index") or 0)
        return key, idx
    except Exception:
        return "", 0


def _remove_image_and_sidecar(img_path: str, log: LogFn, base_dir: str) -> bool:
    try:
        os.remove(img_path)
        meta = img_path + METADATA_SUFFIX
        if os.path.isfile(meta):
            try:
                os.remove(meta)
            except OSError:
                pass
        try:
            from promptstudio.storage.db import ArchiveIndex, normalize_rel_path

            rel = normalize_rel_path(os.path.relpath(img_path, base_dir))
            ArchiveIndex.get().delete_photo(rel)
        except Exception:
            pass
        return True
    except OSError as e:
        log(f"Failed to remove {img_path}: {e}")
        return False


def deduplicate_archive(base_dir: str = SAVED_DIR, log: LogFn = None) -> int:
    """Remove duplicate images by filename, MD5 hash, and Instagram shortcode+slide."""
    log = log or print
    base_dir = os.path.expanduser(base_dir)
    seen_hashes: dict = {}
    seen_filenames: dict = {}
    # key: (shortcode_or_post_id, carousel_index) -> first path kept
    seen_identity: Dict[Tuple[str, int], str] = {}
    deleted = 0

    all_images: List[str] = []
    for ext in IMAGE_EXTENSIONS:
        all_images.extend(glob.glob(os.path.join(base_dir, "**", f"*{ext}"), recursive=True))

    for img_path in all_images:
        filename = os.path.basename(img_path)
        if filename in seen_filenames:
            if _remove_image_and_sidecar(img_path, log, base_dir):
                deleted += 1
                log(f"[dup filename] {filename}")
            continue
        seen_filenames[filename] = img_path

        ident_key, carousel_idx = _sidecar_identity(img_path)
        if ident_key:
            slot = (ident_key, carousel_idx)
            if slot in seen_identity:
                if _remove_image_and_sidecar(img_path, log, base_dir):
                    deleted += 1
                    log(f"[dup shortcode {ident_key}#{carousel_idx}] {filename}")
                continue
            seen_identity[slot] = img_path

        file_hash = _file_md5(img_path)
        if file_hash:
            if file_hash in seen_hashes:
                if _remove_image_and_sidecar(img_path, log, base_dir):
                    deleted += 1
                    log(f"[dup hash] {filename}")
            else:
                seen_hashes[file_hash] = img_path

    if deleted:
        log(f"Deduplication removed {deleted} files")
    return deleted
