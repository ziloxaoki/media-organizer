import os
import time
import json
import subprocess
import argparse
import re
from guessit import guessit
from tmdbv3api import TMDb, Movie, TV

# =========================
# CONFIG
# =========================

INPUT_DIR = os.getenv("INPUT_DIR", "/downloads")
MOVIES_DIR = os.getenv("MOVIES_DIR", "/movies")
TV_DIR = os.getenv("TV_DIR", "/tv")
CACHE_FILE = os.getenv("CACHE_FILE", "/config/cache.json")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))
DEBOUNCE_SECONDS = int(os.getenv("DEBOUNCE_SECONDS", "10"))
FORCE_REPROCESS = os.getenv("FORCE_REPROCESS", "false").lower() == "true"

API_KEY = os.getenv("TMDB_API_KEY")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
TRIGGER_TMM = os.getenv("TRIGGER_TMM", "true").lower() == "true"
TMM_CONTAINER = os.getenv("TMM_CONTAINER", "tinymediamanager")

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov")
EXCLUDED_DIRS = {"tmp", ".tmp", "incomplete", "featurettes", "extras", "bonus"}
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
            print("⚠️ Cache corrupted, resetting...")
    return {}

def save_cache():
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"❌ Failed saving cache: {e}")

cache = load_cache()

# =========================
# HELPERS
# =========================

def is_video(f):
    return f.lower().endswith(VIDEO_EXTENSIONS)

def sanitize(name):
    name = re.sub(WINDOWS_ILLEGAL, "", str(name))
    name = name.replace(":", " -")
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Unknown"

def normalize(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

def find_best_match(results, title, year=None):
    results = list(results or [])
    if not results:
        return None
    target = normalize(title)

    if year:
        for r in results:
            date = getattr(r, "release_date", "") or getattr(r, "first_air_date", "")
            if str(date).startswith(str(year)):
                return r

    for r in results:
        name = getattr(r, "title", None) or getattr(r, "name", None)
        if target in normalize(name):
            return r

    return results[0]

def safe_move(src, dst):
    """Move a file or folder reliably, even across filesystems."""
    if DRY_RUN:
        print(f"[DRY RUN] Would move: {src} -> {dst}")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
        print(f"⚡ Moved: {dst}")
    except OSError:
        subprocess.run(["mv", src, dst], check=True)
        print(f"🚀 Moved via system mv: {dst}")

def trigger_tmm():
    if not TRIGGER_TMM:
        return
    if DRY_RUN:
        print(f"[DRY RUN] Would trigger TMM in container: {TMM_CONTAINER}")
        return
    try:
        subprocess.run([
            "docker", "exec", TMM_CONTAINER,
            "/app/tinyMediaManager", "--update"
        ], check=True)
        print("✅ TMM triggered")
    except Exception as e:
        print(f"❌ TMM failed: {e}")

def is_valid_season_folder(name):
    return re.match(r"(season\s?\d+|s\d{1,2})", name.lower())

def already_processed(path):
    return False if FORCE_REPROCESS else path in cache

def mark_processed(path):
    if not FORCE_REPROCESS:
        cache[path] = True
        save_cache()

def find_video_folder(root_folder):
    """Return the folder that actually contains videos (the one with most video files)."""
    best_folder = None
    max_count = 0
    for dirpath, _, files in os.walk(root_folder):
        count = sum(1 for f in files if is_video(f))
        if count > max_count:
            best_folder = dirpath
            max_count = count
    return best_folder

# =========================
# PROCESSING LOGIC
# =========================

def process_movie(folder):
    files = [f for f in os.listdir(folder) if is_video(f)]
    if not files:
        return False

    info = guessit(files[0])
    title = info.get("title")
    year = info.get("year")
    if not title:
        return False

    results = movie_api.search(title)
    movie = find_best_match(results, title, year)
    if not movie:
        return False

    movie_title = sanitize(getattr(movie, "title", title))
    movie_year = (getattr(movie, "release_date", "") or "")[:4] or "Unknown"
    dest_folder = os.path.join(MOVIES_DIR, f"{movie_title} ({movie_year})")

    for f in files:
        src = os.path.join(folder, f)
        new_name = f"{movie_title} ({movie_year}){os.path.splitext(f)[1]}"
        dest = os.path.join(dest_folder, sanitize(new_name))
        safe_move(src, dest)

    if not DRY_RUN:
        subprocess.run(["rm", "-rf", folder])
    else:
        print(f"[DRY RUN] Would remove original folder: {folder}")

    print(f"🎬 Movie processed: {movie_title}")
    return True

def process_tv_season(folder):
    """Move all episodes in a season, flatten subfolders, then delete empty folders."""
    files_to_move = []
    for root, _, files in os.walk(folder):
        for f in files:
            if is_video(f):
                files_to_move.append(os.path.join(root, f))

    if not files_to_move:
        return False

    info = guessit(os.path.basename(files_to_move[0]))
    title = info.get("title")
    season = info.get("season")
    if not title or season is None:
        return False

    results = tv_api.search(title)
    show = find_best_match(results, title)
    if not show:
        return False

    show_name = sanitize(getattr(show, "name", title))
    season_folder = os.path.join(TV_DIR, show_name, f"Season {season:02d}")
    if not DRY_RUN:
        os.makedirs(season_folder, exist_ok=True)
    else:
        print(f"[DRY RUN] Would create folder: {season_folder}")

    # Move/rename files
    for src in files_to_move:
        ep_info = guessit(os.path.basename(src))
        ep_season = ep_info.get("season")
        ep_episode = ep_info.get("episode")
        ext = os.path.splitext(src)[1]

        if ep_season is None or ep_episode is None:
            new_name = os.path.basename(src)
        else:
            new_name = f"{show_name} S{ep_season:02d}E{ep_episode:02d}{ext}"

        dest = os.path.join(season_folder, sanitize(new_name))
        safe_move(src, dest)

    # Delete empty subfolders
    for root, dirs, _ in os.walk(folder, topdown=False):
        for d in dirs:
            full_path = os.path.join(root, d)
            if os.path.exists(full_path):
                if DRY_RUN:
                    print(f"[DRY RUN] Would remove folder: {full_path}")
                else:
                    subprocess.run(["rm", "-rf", full_path])

    return True

def process_folder(folder):
    if already_processed(folder):
        print(f"⏭️ Skipping already processed: {folder}")
        return False

    name = os.path.basename(folder).lower()
    if name in EXCLUDED_DIRS:
        return False

    subfolders = [d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))]
    season_folders = [d for d in subfolders if is_valid_season_folder(d)]

    moved = False
    if season_folders:
        for s in season_folders:
            moved |= process_tv_season(os.path.join(folder, s))
    else:
        video_folder = find_video_folder(folder) or folder
        files = [f for f in os.listdir(video_folder) if is_video(f)]
        if not files:
            return False

        info = guessit(files[0])
        if info.get("type") == "movie":
            moved = process_movie(video_folder)
        elif info.get("type") == "episode":
            moved = process_tv_season(video_folder)

    if moved:
        mark_processed(folder)
    return moved

def scan_and_process():
    moved_any = False
    for root, dirs, _ in os.walk(INPUT_DIR):
        dirs[:] = [d for d in dirs if d.lower() not in EXCLUDED_DIRS]
        for d in dirs:
            path = os.path.join(root, d)
            target = find_video_folder(path) or path
            if process_folder(target):
                moved_any = True
    return moved_any

# =========================
# CLI MODE
# =========================

def rename_file(path):
    info = guessit(os.path.basename(path))
    title = info.get("title")
    if not title:
        return

    ext = os.path.splitext(path)[1]
    if info.get("type") == "movie":
        results = movie_api.search(title)
        movie = find_best_match(results, title)
        if not movie:
            return
        name = sanitize(getattr(movie, "title", title))
        year = (getattr(movie, "release_date", "") or "")[:4] or "Unknown"
        new_name = f"{name} ({year}){ext}"

    elif info.get("type") == "episode":
        season = info.get("season")
        episode = info.get("episode")
        results = tv_api.search(title)
        show = find_best_match(results, title)
        if not show:
            return
        name = sanitize(getattr(show, "name", title))
        new_name = f"{name} S{season:02d}E{episode:02d}{ext}"
    else:
        return

    dest = os.path.join(os.path.dirname(path), sanitize(new_name))
    safe_move(path, dest)

def process_cli(path):
    for root, _, files in os.walk(path):
        for f in files:
            if is_video(f):
                rename_file(os.path.join(root, f))
    trigger_tmm()

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rename-only", action="store_true")
    parser.add_argument("--path", default=None)
    args = parser.parse_args()

    if args.rename_only:
        process_cli(args.path or INPUT_DIR)
    else:
        print("🟢 Media Organizer started")
        while True:
            print("🔄 Scanning...")
            moved = scan_and_process()
            if moved:
                time.sleep(DEBOUNCE_SECONDS)
                trigger_tmm()
            else:
                print("No changes")
            time.sleep(SCAN_INTERVAL)
