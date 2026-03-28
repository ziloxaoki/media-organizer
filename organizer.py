import os
import shutil
import time
import json
import subprocess
import argparse
import re
from guessit import guessit
from tmdbv3api import TMDb, Movie, TV
from concurrent.futures import ThreadPoolExecutor

# =========================
# CONFIG (ENV VARS)
# =========================

INPUT_DIR = os.getenv("INPUT_DIR", "/downloads")
MOVIES_DIR = os.getenv("MOVIES_DIR", "/movies")
TV_DIR = os.getenv("TV_DIR", "/tv")
CACHE_FILE = os.getenv("CACHE_FILE", "/config/cache.json")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))
API_KEY = os.getenv("TMDB_API_KEY")

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
TRIGGER_TMM = os.getenv("TRIGGER_TMM", "true").lower() == "true"
TMM_CONTAINER = os.getenv("TMM_CONTAINER", "tinymediamanager")
DEBOUNCE_SECONDS = int(os.getenv("DEBOUNCE_SECONDS", "10"))

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov")
EXCLUDED_DIRS = {"tmp", ".tmp", "incomplete"}
WINDOWS_ILLEGAL = r'[<>:"/\\|?*\n\r\t\uFF1A]'

# Global cache for resolved TV folders (per show+season)
tv_folder_cache = {}

# =========================
# TMDB SETUP
# =========================

tmdb = TMDb()
tmdb.api_key = API_KEY
tmdb.language = "en"

movie_api = Movie()
tv_api = TV()

# =========================
# CACHE
# =========================

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            print("⚠️ Cache file corrupted. Resetting to empty cache.")
            try:
                with open(CACHE_FILE, "w") as f:
                    json.dump({}, f)
            except Exception as e:
                print(f"❌ Failed to reset cache file: {e}")
            return {}
    return {}

cache = load_cache()

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def get_cached_tmdb(title):
    key = f"tmdb::{title.lower()}"
    return cache.get(key)

def set_cached_tmdb(title, data):
    key = f"tmdb::{title.lower()}"
    cache[key] = data
    save_cache()

# =========================
# HELPERS
# =========================

def is_video(file):
    return file.lower().endswith(VIDEO_EXTENSIONS)

def is_incomplete(file):
    return file.endswith(".part") or file.endswith(".!qb")

def already_processed(path):
    return path in cache

def mark_processed(path):
    cache[path] = True
    save_cache()

# =========================
# TMM TRIGGER
# =========================

def trigger_tmm():
    if not TRIGGER_TMM:
        return
    try:
        print("🚀 Triggering TinyMediaManager...")
        subprocess.run(["docker", "exec", TMM_CONTAINER, "tmm", "-update"], check=True)
        print("✅ TMM triggered successfully")
    except Exception as e:
        print(f"❌ Failed to trigger TMM: {e}")

# =========================
# FILENAME SANITIZER
# =========================

def truncate_name(name, max_length=100):
    if len(name) <= max_length:
        return name
    truncated = name[:max_length]
    if ' ' in truncated:
        truncated = truncated.rsplit(' ', 1)[0]
    return truncated.rstrip()

def sanitize_windows_name(name, fallback="Unknown", max_length=100):
    name = re.sub(WINDOWS_ILLEGAL, '', name)
    name = name.replace(":", " -").replace("：", " -")
    name = re.sub(r'\s+', ' ', name).strip()
    if not name:
        name = fallback
    return truncate_name(name, max_length)

# =========================
# FIND BEST MATCH
# =========================

def find_best_match(results, title, year=None):
    def normalize(s):
        return re.sub(r'[^a-z0-9]', '', s.lower())
    target = normalize(title)
    # Prefer exact year match
    if year:
        for m in results:
            if getattr(m, "release_date", None) and m.release_date.startswith(str(year)):
                return m
            if getattr(m, "first_air_date", None) and m.first_air_date.startswith(str(year)):
                return m
    # Title similarity
    for m in results:
        name = getattr(m, "title", None) or getattr(m, "name", "")
        if target in normalize(name):
            return m
    return results[0]

# =========================
# PROCESS MOVIE
# =========================

def process_movie(filepath, info):
    title = info.get("title")
    year = info.get("year")
    if not title:
        print(f"❌ No title detected for: {filepath}")
        return False

    cached = get_cached_tmdb(title)
    results = cached if cached else movie_api.search(title)
    if not cached:
        set_cached_tmdb(title, results)

    if not results:
        print(f"Movie not found: {title} ({year})")
        return False

    movie = find_best_match(results, title, year)
    movie_year = movie.release_date[:4] if movie.release_date else "Unknown"
    ext = os.path.splitext(filepath)[1]

    folder_name = sanitize_windows_name(f"{movie.title} ({movie_year})", fallback=f"Movie_{movie_year}")
    new_filename = sanitize_windows_name(f"{movie.title} ({movie_year}){ext}", fallback=f"{movie.title}_{movie_year}{ext}")
    dest_folder = os.path.join(MOVIES_DIR, folder_name)
    dest_path = os.path.join(dest_folder, new_filename)

    if os.path.exists(dest_path):
        print(f"⚠️ Already exists, skipping: {dest_path}")
        return False

    if DRY_RUN:
        print(f"[DRY RUN] Would move: {filepath} -> {dest_path}")
        return False

    os.makedirs(dest_folder, exist_ok=True)
    try:
        os.rename(filepath, dest_path)
        print(f"📀 Moved instantly: {dest_path}")
    except OSError:
        shutil.copy(filepath, dest_path)
        os.remove(filepath)
        print(f"📀 Copied + removed: {dest_path}")

    return True

# =========================
# PROCESS TV
# =========================

def process_tv(filepath, info):
    title = info.get("title")
    season = info.get("season")
    episode = info.get("episode")
    if not title or season is None or episode is None:
        print(f"❌ Invalid TV metadata for file: {filepath}")
        return False

    cached = get_cached_tmdb(title)
    results = cached if cached else tv_api.search(title)
    if not cached:
        set_cached_tmdb(title, results)

    if not results:
        print(f"TV Show not found: {title}")
        return False

    show = results[0]
    folder_key = f"{show.id}_S{season:02d}"
    if folder_key in tv_folder_cache:
        folder = tv_folder_cache[folder_key]
    else:
        season_folder = sanitize_windows_name(f"Season {season:02d}")
        show_name = sanitize_windows_name(show.name, fallback="UnknownShow")
        folder = os.path.join(TV_DIR, show_name, season_folder)
        tv_folder_cache[folder_key] = folder

    ext = os.path.splitext(filepath)[1]
    new_filename = sanitize_windows_name(f"{show.name} S{season:02d}E{episode:02d}{ext}",
                                         fallback=f"{show.name}_S{season:02d}E{episode:02d}{ext}")
    dest_path = os.path.join(folder, new_filename)

    if os.path.exists(dest_path):
        print(f"⚠️ Already exists, skipping: {dest_path}")
        return False

    if DRY_RUN:
        print(f"[DRY RUN] Would move: {filepath} -> {dest_path}")
        return False

    os.makedirs(folder, exist_ok=True)
    try:
        os.rename(filepath, dest_path)
        print(f"📺 Moved instantly: {dest_path}")
    except OSError:
        shutil.copy(filepath, dest_path)
        os.remove(filepath)
        print(f"📺 Copied + removed: {dest_path}")

    return True

# =========================
# PROCESS FILE
# =========================

def process_file(filepath):
    if already_processed(filepath):
        return False
    if is_incomplete(filepath) or not is_video(filepath):
        return False

    info = guessit(os.path.basename(filepath))
    moved = False
    if info.get("type") == "movie":
        moved = process_movie(filepath, info)
    elif info.get("type") == "episode":
        moved = process_tv(filepath, info)

    if moved:
        mark_processed(filepath)
    return moved

# =========================
# SCAN AND PROCESS
# =========================

def scan_and_process():
    moved_any = False
    for root, dirs, files in os.walk(INPUT_DIR):
        if any(excl in root.lower() for excl in EXCLUDED_DIRS):
            continue
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_file, os.path.join(root, f)) for f in files]
            for f in futures:
                if f.result():
                    moved_any = True
    return moved_any

# =========================
# CLI MODE
# =========================

def rename_file(filepath, info):
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1]
    if info.get("type") == "movie":
        title = info.get("title")
        results = movie_api.search(title)
        if not results: return
        movie = results[0]
        year = movie.release_date[:4] if movie.release_date else "Unknown"
        new_name = sanitize_windows_name(f"{movie.title} ({year}){ext}", fallback=f"{movie.title}_{year}{ext}")
    elif info.get("type") == "episode":
        title = info.get("title")
        season = info.get("season")
        episode = info.get("episode")
        results = tv_api.search(title)
        if not results: return
        show = results[0]
        new_name = sanitize_windows_name(f"{show.name} S{season:02d}E{episode:02d}{ext}",
                                         fallback=f"{show.name}_S{season:02d}E{episode:02d}{ext}")
    else:
        return
    new_path = os.path.join(os.path.dirname(filepath), new_name)
    if DRY_RUN:
        print(f"[DRY RUN] Would rename: {filepath} -> {new_path}")
        return
    os.rename(filepath, new_path)
    print(f"✏️ Renamed: {new_path}")

def process_cli_path(path):
    print(f"🔵 CLI mode: processing {path}")
    for root, _, files in os.walk(path):
        for f in files:
            full_path = os.path.join(root, f)
            if not is_video(f):
                continue
            info = guessit(f)
            rename_file(full_path, info)
    # Trigger TMM after all CLI renames
    print(f"⏳ CLI task completed, triggering TMM...")
    trigger_tmm()

# =========================
# ARGPARSE
# =========================

def parse_args():
    parser = argparse.ArgumentParser(description="Media Organizer")
    parser.add_argument("--path", help="Path to process (CLI mode)", default=None)
    parser.add_argument("--rename-only", action="store_true", help="Rename files in place (no moving)")
    return parser.parse_args()

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    args = parse_args()
    if args.rename_only:
        path = args.path or INPUT_DIR
        print(f"🔵 CLI mode (rename only): processing {path}")
        process_cli_path(path)
    else:
        print("🟢 Media Organizer started (watch mode)")
        while True:
            print("🔄 Scanning...")
            moved = scan_and_process()
            if moved:
                print(f"⏳ Waiting {DEBOUNCE_SECONDS}s before triggering TMM...")
                time.sleep(DEBOUNCE_SECONDS)
                trigger_tmm()
            else:
                print("No changes detected.")
            time.sleep(SCAN_INTERVAL)
