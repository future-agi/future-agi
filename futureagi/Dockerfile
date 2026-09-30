FROM futureagi/future-agi-base:v1.0.4

ENV NLTK_DATA=/usr/local/share/nltk_data

# Mirrors the root Dockerfile until a future-agi-base rebuilt from
# requirements.txt (v1.0.5) replaces v1.0.4: granian>=2.7.1 (granian#798),
# channels-redis and the asgiref it needs. Installed before the source copy,
# so a source change reuses this layer.
RUN pip install --no-cache-dir \
    "granian[uvloop,reload]==2.8.3" \
    "channels-redis==4.3.0" \
    "asgiref==3.11.0"

COPY . .

# The gRPC import path loads the EE trace scanner, which requires these corpora.
# Pin both the nltk_data revision and archive checksums for reproducible images.
RUN python bin/install_nltk_data.py

# Install Node.js for sandboxed JavaScript eval execution
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose ports for different services
# 8000 - Backend (gunicorn/django)
# 5555 - Flower (Celery monitoring)
# 50051 - gRPC server
EXPOSE 8000
EXPOSE 5555
EXPOSE 50051

# not running makemigrations, that should be done during development time only
ENTRYPOINT ["bash", "./entrypoint.sh"]
