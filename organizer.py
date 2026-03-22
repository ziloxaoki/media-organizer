import os
import shutil
import time
import json
import subprocess
from guessit import guessit
from tmdbv3api import TMDb, Movie, TV

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

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r") as f:
        cache = json.load(f)
else:
    cache = {}

def save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

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

def run_move(src, dest):
    if DRY_RUN:
        print(f"[DRY RUN] Would move: {src} -> {dest}")
    else:
        shutil.move(src, dest)

# =========================
# TMM TRIGGER
# =========================

def trigger_tmm():
    if not TRIGGER_TMM:
        return

    try:
        print("🚀 Triggering TinyMediaManager...")

        subprocess.run(
            ["docker", "exec", TMM_CONTAINER, "tmm", "-update"],
            check=True
        )

        print("✅ TMM triggered successfully")

    except Exception as e:
        print(f"❌ Failed to trigger TMM: {e}")

# =========================
# PROCESS MOVIE
# =========================

def process_movie(filepath, info):
    title = info.get("title")

    results = movie_api.search(title)
    if not results:
        print(f"Movie not found: {title}")
        return False

    movie = results[0]
    year = movie.release_date[:4] if movie.release_date else "Unknown"

    print(f"Movie details found: {movie.title} ({year}")

    folder_name = f"{movie.title} ({year})"
    new_filename = f"{movie.title} ({year}){os.path.splitext(filepath)[1]}"

    dest_folder = os.path.join(MOVIES_DIR, folder_name)
    os.makedirs(dest_folder, exist_ok=True)

    dest_path = os.path.join(dest_folder, new_filename)

    run_move(filepath, dest_path)
    print(f"🎬 Moved: {dest_path}")

    return True

# =========================
# PROCESS TV
# =========================

def process_tv(filepath, info):
    title = info.get("title")
    season = info.get("season")
    episode = info.get("episode")

    results = tv_api.search(title)
    if not results:
        print(f"TV Show not found: {title}")
        return False

    show = results[0]

    print(f"TV Show details found: {show.name}")

    folder = os.path.join(TV_DIR, show.name, f"Season {season:02d}")
    os.makedirs(folder, exist_ok=True)

    new_filename = f"{show.name} S{season:02d}E{episode:02d}{os.path.splitext(filepath)[1]}"
    dest_path = os.path.join(folder, new_filename)

    run_move(filepath, dest_path)
    print(f"📺 Moved: {dest_path}")

    return True

# =========================
# PROCESS FILE
# =========================

def process_file(filepath):
    if already_processed(filepath):
        return False

    print(f"Found file: {filepath}")

    filename = os.path.basename(filepath)

    if is_incomplete(filename):
        return False

    if not is_video(filename):
        return False

    info = guessit(filename)

    try:
        moved = False

        print(f"Processing file: {filepath}")

        if info.get("type") == "movie":
            moved = process_movie(filepath, info)

        elif info.get("type") == "episode":
            moved = process_tv(filepath, info)

        else:
            print(f"Ignored: {filename}")
            return False

        if moved:
            mark_processed(filepath)

        return moved

    except Exception as e:
        print(f"Error processing {filename}: {e}")
        return False

# =========================
# SCAN
# =========================

def scan_and_process():
    moved_any = False

    for root, dirs, files in os.walk(INPUT_DIR):
        # Remove excluded directories from traversal
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

        for file in files:
            full_path = os.path.join(root, file)

            if process_file(full_path):
                moved_any = True

    return moved_any

# =========================
# MAIN LOOP
# =========================

if __name__ == "__main__":
    print("🟢 Media Organizer started")

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
