import os
import shutil
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
CACHE_FILE = os.getenv("CACHE_FILE", "/config/cache.json")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))
API_KEY = os.getenv("TMDB_API_KEY")

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
TRIGGER_TMM = os.getenv("TRIGGER_TMM", "true").lower() == "true"
TMM_CONTAINER = os.getenv("TMM_CONTAINER", "tinymediamanager")
DEBOUNCE_SECONDS = int(os.getenv("DEBOUNCE_SECONDS", "10"))

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov")
EXCLUDED_DIRS = {"tmp", ".tmp", "incomplete"}
# Replace invalid Windows filename characters
WINDOWS_ILLEGAL = r'[<>:"/\\|?*\n\r\t\uFF1A]'

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
    else:
        return {}

cache = load_cache()

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
# FILENAME SANITIZER
# =========================
def truncate_name(name, max_length=100):
    if len(name) <= max_length:
        return name

    truncated = name[:max_length]

    # cut at last space to avoid breaking words
    if ' ' in truncated:
        truncated = truncated.rsplit(' ', 1)[0]

    return truncated.rstrip()

def sanitize_windows_name(name, fallback="Unknown", max_length=100):
    # Remove illegal Windows chars (including fullwidth colon)
    name = re.sub(WINDOWS_ILLEGAL, '', name)

    # Optional: replace colon with nicer separator instead of removing
    name = name.replace(":", " -").replace("：", " -")

    # Collapse spaces
    name = re.sub(r'\s+', ' ', name).strip()

    if not name:
        name = fallback

    return truncate_name(name, max_length)

# =========================
# FIND BEST MOVIE MATCH
# =========================
def find_best_match(results, title, year=None):
    def normalize(s):
        return re.sub(r'[^a-z0-9]', '', s.lower())

    target = normalize(title)

    # 1. Prefer exact year match
    if year:
        for m in results:
            if getattr(m, "release_date", None):
                if m.release_date.startswith(str(year)):
                    return m

            if getattr(m, "first_air_date", None):
                if m.first_air_date.startswith(str(year)):
                    return m

    # 2. Title similarity
    for m in results:
        name = getattr(m, "title", None) or getattr(m, "name", "")
        if target in normalize(name):
            return m

    # 3. fallback
    return results[0]


# =========================
# PROCESS MOVIE
# =========================

def process_movie(filepath, info):
    title = info.get("title")
    year = info.get("year")  # from guessit

    if not title:
        print(f"❌ No title detected for: {filepath}")
        return False

    results = movie_api.search(title)

    if not results:
        print(f"Movie not found: {title} ({year})")
        return False

    # ✅ Use proper matching instead of results[0]
    movie = find_best_match(results, title, year)

    movie_year = movie.release_date[:4] if movie.release_date else "Unknown"
    ext = os.path.splitext(filepath)[1]

    # ✅ Build names
    folder_name = sanitize_windows_name(
        f"{movie.title} ({movie_year})",
        fallback=f"Movie_{movie_year}"
    )

    new_filename = sanitize_windows_name(
        f"{movie.title} ({movie_year}){ext}",
        fallback=f"{movie.title}_{movie_year}{ext}"
    )

    dest_folder = os.path.join(MOVIES_DIR, folder_name)
    dest_path = os.path.join(dest_folder, new_filename)

    if DRY_RUN:
        print(f"[DRY RUN] Would move: {filepath} -> {dest_path}")
        return False

    os.makedirs(dest_folder, exist_ok=True)

    # Copy + remove (cross-filesystem safe)
    shutil.copy2(filepath, dest_path)
    os.remove(filepath)

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

    season_folder = f"Season {season:02d}"
    season_folder = sanitize_windows_name(season_folder)
    show_name = sanitize_windows_name(show.name, fallback="UnknownShow")
    folder = os.path.join(TV_DIR, show_name, season_folder)

    new_filename = sanitize_windows_name(
        f"{show.name} S{season:02d}E{episode:02d}{os.path.splitext(filepath)[1]}",
        fallback=f"{show.name}_S{season:02d}E{episode:02d}.mkv"
    )
    dest_path = os.path.join(folder, new_filename)

    if DRY_RUN:
        print(f"[DRY RUN] Would move: {filepath} -> {dest_path}")
        return False

    os.makedirs(folder, exist_ok=True)

    # Copy + remove for cross-filesystem support
    shutil.copy2(filepath, dest_path)
    os.remove(filepath)

    print(f"📺 Moved: {dest_path}")
    return True

# =========================
# PROCESS FILE
# =========================

def process_file(filepath):
    print(f"DEBUG: checking {filepath}")

    if already_processed(filepath):
        print("SKIP: already processed")
        return False

    filename = os.path.basename(filepath)

    if is_incomplete(filename):
        print("SKIP: incomplete file")
        return False

    if not is_video(filename):
        print(f"SKIP: not a video ({filename})")
        return False

    print("PASS: file accepted")

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


    # Remove excluded directories from traversal
    for root, dirs, files in os.walk(INPUT_DIR):
        # Skip any path containing excluded dirs
        if any(excluded in root.lower() for excluded in EXCLUDED_DIRS):
            continue

        for file in files:
            full_path = os.path.join(root, file)

            if process_file(full_path):
                moved_any = True

    return moved_any


# =========================
# RENAME WITHOUT MOVING
# =========================
def rename_file(filepath, info):
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1]

    if info.get("type") == "movie":
        title = info.get("title")

        results = movie_api.search(title)
        if not results:
            print(f"Movie not found: {title}")
            return

        movie = results[0]
        year = movie.release_date[:4] if movie.release_date else "Unknown"

        new_name = sanitize_windows_name(
            f"{movie.title} ({year}){ext}",
            fallback=f"{movie.title}_{year}{ext}"
        )

    elif info.get("type") == "episode":
        title = info.get("title")
        season = info.get("season")
        episode = info.get("episode")

        results = tv_api.search(title)
        if not results:
            print(f"TV not found: {title}")
            return

        show = results[0]

        new_name = sanitize_windows_name(
            f"{show.name} S{season:02d}E{episode:02d}{ext}",
            fallback=f"{show.name}_S{season:02d}E{episode:02d}{ext}"
        )

    else:
        print(f"Ignored: {filename}")
        return

    new_path = os.path.join(os.path.dirname(filepath), new_name)

    if DRY_RUN:
        print(f"[DRY RUN] Would rename: {filepath} -> {new_path}")
        return

    os.rename(filepath, new_path)
    print(f"✏️ Renamed: {new_path}")


# =========================
# PROCESS CLI COMMAND
# =========================
def process_cli_path(path):
    print(f"🔵 CLI mode: processing {path}")

    for root, _, files in os.walk(path):
        for file in files:
            full_path = os.path.join(root, file)

            if not is_video(file):
                continue

            print(f"Processing: {full_path}")

            try:
                info = guessit(file)
                rename_file(full_path, info)
            except Exception as e:
                print(f"Error: {e}")


# =========================
# PARSE ARGUMENTS
# =========================
def parse_args():
    parser = argparse.ArgumentParser(description="Media Organizer")

    parser.add_argument(
        "--path",
        help="Path to process (CLI mode)",
        default=None
    )

    parser.add_argument(
        "--rename-only",
        action="store_true",
        help="Rename files in place (no moving)"
    )

    return parser.parse_args()

# =========================
# MAIN LOOP
# =========================

if __name__ == "__main__":
    args = parse_args()

    # CLI mode
    if args.rename_only:
        if args.path:
            print(f"🔵 CLI mode: using custom path -> {args.path}")
            process_cli_path(args.path)
        else:
            print("🔵 CLI mode: no path provided")
            print(f"➡️ Processing MOVIES_DIR: {MOVIES_DIR}")
            process_cli_path(MOVIES_DIR)

            print(f"➡️ Processing TV_DIR: {TV_DIR}")
            process_cli_path(TV_DIR)

    else:
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



