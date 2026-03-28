import os
import time
import json
import subprocess
import argparse
import re
from guessit import guessit
from tmdbv3api import TMDb, Movie, TV

# =========================
# CONFIG (ENV VARS)
# =========================

INPUT_DIR = os.getenv("INPUT_DIR", "/downloads")
MOVIES_DIR = os.getenv("MOVIES_DIR", "/movies")
TV_DIR = os.getenv("TV_DIR", "/tv")
MOVIES_HOST_PATH = os.getenv("MOVIES_HOST_PATH")
TV_HOST_PATH = os.getenv("TV_HOST_PATH")
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
            with open(CACHE_FILE, "w") as f:
                json.dump({}, f)
            return {}
    return {}

cache = load_cache()

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

def get_cached_tmdb(title):
    key = f"tmdb::{title.lower()}"
    data = cache.get(key)
    if not data:
        return None
    from tmdbv3api.tmdb import AsObj
    return [AsObj(item) for item in data]

def set_cached_tmdb(title, results):
    serializable = []
    for r in results:
        data = {}
        for attr in ["id", "title", "name", "release_date", "first_air_date"]:
            value = getattr(r, attr, None)
            if value is not None:
                data[attr] = value
        serializable.append(data)
    cache[f"tmdb::{title.lower()}"] = serializable
    save_cache()

# =========================
# HELPERS
# =========================

def is_video(file):
    return file.lower().endswith(VIDEO_EXTENSIONS)

def already_processed(path):
    return path in cache

def mark_processed(path):
    cache[path] = True
    save_cache()

def sanitize_windows_name(name, fallback="Unknown"):
    name = re.sub(WINDOWS_ILLEGAL, '', name)
    name = name.replace(":", " -").replace("：", " -")
    name = re.sub(r'\s+', ' ', name).strip()
    return name if name else fallback

def find_best_match(results, title, year=None):
    def normalize(s):
        return re.sub(r'[^a-z0-9]', '', s.lower())
    target = normalize(title)
    if year:
        for m in results:
            if getattr(m, "release_date", "").startswith(str(year)) or getattr(m, "first_air_date", "").startswith(str(year)):
                return m
    for m in results:
        name = getattr(m, "title", None) or getattr(m, "name", "")
        if target in normalize(name):
            return m
    return results[0]

def fast_move(src, dst):
    if DRY_RUN:
        print(f"[DRY RUN] Would move folder: {src} -> {dst}")
        return
    if os.path.exists(dst):
        print(f"⚠️ Destination exists, skipping move: {dst}")
        return
    subprocess.run(["mv", src, dst], check=True)
    mark_processed(dst)

def trigger_tmm():
    if not TRIGGER_TMM:
        return
    try:
        print("🚀 Triggering TinyMediaManager...")
        # pass dataset paths dynamically
        subprocess.run([
            "docker", "exec", TMM_CONTAINER,
            "tmm", "-update",
            "-movies", MOVIES_HOST_PATH,
            "-tv", TV_HOST_PATH
        ], check=True)
        print("✅ TMM triggered successfully")
    except Exception as e:
        print(f"❌ Failed to trigger TMM: {e}")

# =========================
# PROCESS FOLDERS
# =========================

def process_movie_folder(folder_path):
    files = [f for f in os.listdir(folder_path) if is_video(f)]
    if not files:
        return False
    info = guessit(files[0])
    title, year = info.get("title"), info.get("year")
    if not title:
        print(f"❌ No title detected for folder: {folder_path}")
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
    dest_folder = os.path.join(MOVIES_DIR, sanitize_windows_name(f"{movie.title} ({movie_year})"))
    fast_move(folder_path, dest_folder)
    print(f"🎬 Movie folder moved: {dest_folder}")
    return True

def process_tv_folder(folder_path):
    files = [f for f in os.listdir(folder_path) if is_video(f)]
    if not files:
        return False
    info = guessit(files[0])
    title, season = info.get("title"), info.get("season")
    if not title or season is None:
        print(f"❌ Invalid TV metadata for folder: {folder_path}")
        return False

    cached = get_cached_tmdb(title)
    results = cached if cached else tv_api.search(title)
    if not cached:
        set_cached_tmdb(title, results)
    show = results[0]

    season_folder_name = sanitize_windows_name(f"Season {season:02d}")
    show_name = sanitize_windows_name(show.name)
    dest_folder = os.path.join(TV_DIR, show_name, season_folder_name)
    fast_move(folder_path, dest_folder)
    print(f"📺 TV season folder moved: {dest_folder}")
    return True

def process_folder(folder_path):
    if already_processed(folder_path):
        return False
    # Try to detect if folder is movie or TV show
    files = [f for f in os.listdir(folder_path) if is_video(f)]
    if not files:
        return False
    info = guessit(files[0])
    moved = False
    if info.get("type") == "movie":
        moved = process_movie_folder(folder_path)
    elif info.get("type") == "episode":
        moved = process_tv_folder(folder_path)
    if moved:
        mark_processed(folder_path)
    return moved

# =========================
# SCAN AND PROCESS
# =========================

def scan_and_process():
    moved_any = False
    for root, dirs, _ in os.walk(INPUT_DIR):
        dirs[:] = [d for d in dirs if d.lower() not in EXCLUDED_DIRS]  # skip excluded
        for d in dirs:
            full_path = os.path.join(root, d)
            if process_folder(full_path):
                moved_any = True
    return moved_any

# =========================
# CLI MODE
# =========================

def rename_file(filepath, info):
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1]
    new_name = None

    if info.get("type") == "movie":
        title = info.get("title")
        results = movie_api.search(title)
        if not results: return
        movie = results[0]
        year = movie.release_date[:4] if movie.release_date else "Unknown"
        new_name = sanitize_windows_name(f"{movie.title} ({year}){ext}")
    elif info.get("type") == "episode":
        title = info.get("title")
        season, episode = info.get("season"), info.get("episode")
        results = tv_api.search(title)
        if not results: return
        show = results[0]
        new_name = sanitize_windows_name(f"{show.name} S{season:02d}E{episode:02d}{ext}")

    if new_name:
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
