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
SUBTITLE_EXTENSIONS = (".srt", ".sub", ".ass", ".ssa", ".vtt")

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

def is_subtitle(f):
    return f.lower().endswith(SUBTITLE_EXTENSIONS)

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
    best_folder = None
    max_count = 0
    for dirpath, _, files in os.walk(root_folder):
        count = sum(1 for f in files if is_video(f))
        if count > max_count:
            best_folder = dirpath
            max_count = count
    return best_folder

def find_related_subtitles(folder, video_file):
    """
    Find subtitles matching a given video file.
    Example:
      Movie.Name.2024.mkv
      Movie.Name.2024.en.srt
      Movie.Name.2024.pt-BR.forced.srt
    """
    video_base = os.path.splitext(video_file)[0]
    related = []

    for f in os.listdir(folder):
        if not is_subtitle(f):
            continue

        sub_base = os.path.splitext(f)[0]
        if sub_base == video_base or sub_base.startswith(video_base + "."):
            related.append(f)

    return related

def build_subtitle_name(video_new_name, subtitle_file):
    """
    Preserve subtitle suffix:
      Movie (2024).en.srt
      Movie (2024).pt-BR.forced.srt
    """
    video_base = os.path.splitext(video_new_name)[0]
    sub_base, sub_ext = os.path.splitext(subtitle_file)

    suffix = ""
    parts = sub_base.split(".")
    if len(parts) > 1:
        suffix = "." + ".".join(parts[1:])

    return f"{video_base}{suffix}{sub_ext}"

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
    os.makedirs(dest_folder, exist_ok=True)

    for f in files:
        src = os.path.join(folder, f)
        ext = os.path.splitext(f)[1]
        new_name = f"{movie_title} ({movie_year}){ext}"
        dest = os.path.join(dest_folder, sanitize(new_name))
        safe_move(src, dest)

        for sub in find_related_subtitles(folder, f):
            sub_src = os.path.join(folder, sub)
            sub_new_name = build_subtitle_name(new_name, sub)
            sub_dest = os.path.join(dest_folder, sanitize(sub_new_name))
            safe_move(sub_src, sub_dest)

    return True

def process_tv_season(folder):
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
    os.makedirs(season_folder, exist_ok=True)

    for src in files_to_move:
        filename = os.path.basename(src)
        src_folder = os.path.dirname(src)

        ep_info = guessit(filename)
        ep_season = ep_info.get("season")
        ep_episode = ep_info.get("episode")
        ext = os.path.splitext(filename)[1]

        if ep_season is None or ep_episode is None:
            new_name = filename
        else:
            new_name = f"{show_name} S{ep_season:02d}E{ep_episode:02d}{ext}"

        dest = os.path.join(season_folder, sanitize(new_name))
        safe_move(src, dest)

        for sub in find_related_subtitles(src_folder, filename):
            sub_src = os.path.join(src_folder, sub)
            sub_new_name = build_subtitle_name(new_name, sub)
            sub_dest = os.path.join(season_folder, sanitize(sub_new_name))
            safe_move(sub_src, sub_dest)

    return True
