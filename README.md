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
    command: sh -c "chown -R 568:568 /config"
    volumes:
      - /mnt/apps-pool/media-organizer/config:/config

  media-organizer:
    image: ghcr.io/ziloxaoki/media-organizer/media-organizer:latest
    user: "568:568"
    group_add:
      - "999"
    container_name: media-organizer
    restart: unless-stopped

    environment:
      - TMDB_API_KEY=${TMDB_API_KEY}
      - INPUT_DIR=${INPUT_DIR}
      - MOVIES_DIR=${MOVIES_DIR}
      - TV_DIR=${TV_DIR}
      - SCAN_INTERVAL=${SCAN_INTERVAL}
      - PYTHONUNBUFFERED=1
      - DRY_RUN=false

    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /mnt/tank/media/downloads:/downloads
      - /mnt/tank/media/movies:/movies
      - /mnt/tank/media/tv:/tv
      - /mnt/tank/media/config:/config

  tinymediamanager:
    image: romancin/tinymediamanager
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

  tmm-scheduler:
    image: alpine:latest
    container_name: tmm-scheduler
    depends_on:
      - tinymediamanager
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - TZ=Australia/Sydney
      - SLEEP_INTERVAL=3600
    entrypoint: >
      sh -c "INTERVAL=${SLEEP_INTERVAL:-3600};
      while true; do
        echo 'Running TMM update...';
        docker exec tinymediamanager tmm -update;
        sleep $INTERVAL;
      done"