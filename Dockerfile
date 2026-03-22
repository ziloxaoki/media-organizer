FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y ffmpeg docker.io && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir guessit tmdbv3api requests

# Copy files
COPY organizer.py .

# Create user with UID 568 (apps)
RUN useradd -u 568 -m appuser

# Fix ownership
RUN chown -R 568:568 /app

# Switch to that user
USER 568:568

CMD ["python", "organizer.py"]
