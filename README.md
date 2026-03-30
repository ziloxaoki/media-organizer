# 📦 Media Organizer

A lightweight automated media organizer that:

- Detects video files from a downloads folder
- Identifies whether content is a movie or TV show
- Renames files using proper naming conventions
- Moves files into `/movies` and `/tv`
- Runs continuously in a Docker container
- Optionally integrates with TMDb for metadata enrichment

---

# 🚀 Features

- 🔍 Automatic file detection
- 🎬 Movie vs TV show classification
- 🧠 Filename parsing using `guessit`
- 🌐 Metadata lookup via TMDb API
- 📁 Automatic file organization
- 🔁 Continuous scanning loop
- 🐳 Docker + Docker Compose ready
- ⚙️ Configurable via environment variables

---

# 📁 Project Structure

media-organizer/
├── docker-compose.yml
├── Dockerfile
├── organizer.py
├── .env
└── .gitignore


---

# ⚙️ Requirements

- Docker
- Docker Compose
- TMDb API key

---

# 🔐 Environment Variables

Create a `.env` file:

TMDB_API_KEY=your_tmdb_api_key
INPUT_DIR=/downloads
MOVIES_DIR=/movies
TV_DIR=/tv
SCAN_INTERVAL=300


---

# 🐳 Docker Setup

## 1. Build and run

```bash
0. Build & Deploy local
docker compose up --build -d
1. Deploy
docker compose up -d
2. Stop
docker compose down
3. View logs
docker logs -f media-organizer
📦 docker-compose.yml Example
version: "3.9"

services:
  fix-permissions:
    image: busybox
    command: sh -c "chown -R 568:568 /downloads /movies /tv /config"
    volumes:
      - /mnt/MainPool/Downloads:/downloads
      - /mnt/MainPool/Videos/Movies:/movies
      - /mnt/MainPool/Videos/Series:/tv
      - /mnt/apps-pool/media-organizer/config:/config

  media-organizer:
    image: ghcr.io/ziloxaoki/media-organizer/media-organizer:latest
    user: "568:568"
    group_add:
      - "999"
    container_name: media-organizer
    restart: unless-stopped

    environment:
      - TZ=Australia/Sydney
      - TMDB_API_KEY=910e0b20ecdca4cd80b9c33a6e4d3904
      - INPUT_DIR=/downloads
      - MOVIES_DIR=/movies
      - TV_DIR=/tv    
      - SCAN_INTERVAL=300
      - SLEEP_INTERVAL=3600
      - PYTHONUNBUFFERED=1
      - DRY_RUN=true
      - TMM_CONTAINER=tmm
      - FORCE_REPROCESS=true

    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /mnt/tank/media/downloads:/downloads
      - /mnt/tank/media/movies:/movies
      - /mnt/tank/media/tv:/tv
      - /mnt/tank/media/config:/config

  tinymediamanager:
    image: romancin/tinymediamanager:latest
    container_name: tmm
    environment:
      - TZ=Australia/Sydney
    volumes:
      - /mnt/tank/media/movies:/media/movies
      - /mnt/tank/media/tv:/media/tv
      - /mnt/tank/tmm-config:/config
    ports:
      - "4000:4000"
      - "5800:5800"
      - "5900:5900"
```

## 2. qBittorrent configuration
All changes are performed within the /downloads folder so it is necessary that the files are not locked by qBittorrent.
✅ Auto-remove torrent after completion (simple)
In qBittorrent settings:
Go to Tools → Options → BitTorrent
Enable:
“When ratio reaches”
Set:
Ratio = 0 (or very low, like 0.01)
OR time-based removal
then "Remove torrent"

👉 Result:

Torrent stops almost immediately after finishing
Files are unlocked
Your script can move/delete safely

🧰 CLI Mode (Rename Files In-Place)

In addition to automatic background scanning, Media Organizer can be used as a command-line tool to rename media files directly within any folder — without moving them.

🔵 Use Case

This mode is useful when you want to:

Clean up existing libraries
Standardize filenames
Rename files inside any directory (not just /downloads)
Preview changes safely with dry-run (docker-compose.yml)
🚀 Usage
python organizer.py --path /your/folder --rename-only

✔ Shows what would be renamed
❌ Does not modify files

📂 What It Does
Recursively scans the provided folder
Detects media files using guessit
Fetches metadata from TMDB
Renames files using clean naming:
🎬 Movies
Old: Frankenstein.2025.2160p.mkv
New: Frankenstein (2025).mkv

📺 TV Shows
Old: show.s01e01.mkv
New: Show Name S01E01.mkv

⚙️ Supported Options
Option	Description
--path	Target folder to process (optional)
--rename-only	Enables CLI rename mode
⚠️ Notes
Only video files are processed (.mkv, .mp4, .avi, .mov)
Files are renamed in place (no moving)
TMDB API key must still be configured
Cache is not used in CLI mode
🟢 Default Mode (Daemon)

If no CLI arguments are provided, the app runs in its default mode:

python organizer.py


✔ Continuously scans /downloads
✔ Moves and organizes media
✔ Triggers TinyMediaManager updates

💡 Tip

Always run with --dry-run first to verify results before applying changes.

Since your script is inside a container, you don’t run:

python organizer.py ...


👉 You run it through the container.

✅ Option 1 — Run CLI via docker exec (simplest)

If your container is already running:

docker exec -it media-organizer python /app/organizer.py --path /downloads/test --rename-only

🧠 Important
/app/organizer.py → path inside container
/downloads/test → must be a mounted volume path, not host path

Example mapping:

- /mnt/tank/Downloads:/downloads


So:

Host path	Container path
/mnt/tank/downloads/test	/downloads/test
✅ Option 2 — One-off run (cleaner for CLI)

Run a temporary container just for CLI:

docker run --rm \
-v /mnt/tank/downloads:/downloads \
-v /mnt/apps-pool/media-organizer/config:/config \
-e TMDB_API_KEY=your_key \
ghcr.io/ziloxaoki/media-organizer:latest \
python /app/organizer.py --path /downloads/test --rename-only

🧪 With dry-run:
docker run --rm \
-v /mnt/tank/downloads:/downloads \
-e TMDB_API_KEY=your_key \
ghcr.io/ziloxaoki/media-organizer:latest \
python /app/organizer.py --path /downloads/test --rename-only

✅ Option 3 — Add CLI service to docker-compose (BEST UX)

Add this to your docker-compose.yml:

media-organizer-cli:
image: ghcr.io/ziloxaoki/media-organizer:latest
profiles: ["cli"]
volumes:
- /mnt/tank/downloads:/downloads
- /mnt/apps-pool/media-organizer/config:/config
environment:
- TMDB_API_KEY=111111111
entrypoint: ["python", "/app/organizer.py"]

🚀 Then run:
docker compose run --rm media-organizer-cli --path /downloads/test --rename-only

🧠 Why Option 3 is best

✔ No need to exec into running container
✔ Clean separation (daemon vs CLI)
✔ Reproducible
✔ Easier to document
