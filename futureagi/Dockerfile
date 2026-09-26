FROM futureagi/future-agi-base:v1.0.4

ENV NLTK_DATA=/usr/local/share/nltk_data

COPY . .

# Mirrors the root Dockerfile until future-agi-base is rebuilt from
# requirements.txt: granian>=2.7.1 (granian#798), channels-redis and the
# asgiref it needs.
RUN pip install --no-cache-dir \
    "granian[uvloop,reload]==2.8.3" \
    "channels-redis==4.3.0" \
    "asgiref==3.11.0"

# The gRPC import path loads the EE trace scanner, which requires these corpora.
# Pin both the nltk_data revision and archive checksums for reproducible images.
# A layer of its own, so the pip layer above can go once the base is rebuilt.
# hadolint ignore=DL3059
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
