FROM futureagi/future-agi-base:v1.0.6

ENV NLTK_DATA=/usr/local/share/nltk_data

# Mirrors the root Dockerfile until a future-agi-base rebuilt from
# requirements.txt replaces v1.0.6: granian>=2.7.1 (granian#798),
# channels-redis and the asgiref it needs, plus the upgrades the base does not
# carry yet: django, litellm, pyjwt and the openai that litellm 1.100.x
# requires, and mcp, nltk and the document/image parsing set (pdfplumber and
# pypdfium2 move with pdfminer.six, which pdfplumber pins exactly). nltk lands
# before the corpus install below, so install_nltk_data.py verifies the data
# against the upgraded nltk.
# litellm 1.100.x needs openai>=2.20.0, boto3>=1.43.1, pydantic-settings>=2.14.1
# and aiohttp>=3.14.2. The base's boto3 1.42.28 and pydantic-settings 2.12.0
# are older, so boto3, botocore, s3transfer, pydantic-settings and the
# python-dotenv it needs are pinned at the futureagi/uv.lock versions rather
# than whatever is newest at build time. aiohttp and urllib3 are pinned at the
# lock versions too, which meet the futureagi/pyproject.toml floors, with the
# yarl that aiohttp 3.14.5 needs.
# Installed before the source copy, so a source change reuses this layer.
RUN pip install --no-cache-dir \
    "granian[uvloop,reload]==2.8.3" \
    "channels-redis==4.3.0" \
    "asgiref==3.11.0" \
    "django==5.1.15" \
    "litellm==1.100.4" \
    "boto3==1.44.3" \
    "botocore==1.44.3" \
    "s3transfer==0.19.3" \
    "pydantic-settings==2.16.3" \
    "python-dotenv==1.2.4" \
    "aiohttp==3.14.5" \
    "yarl==1.25.1" \
    "urllib3==2.8.0" \
    "pyjwt==2.15.1" \
    "openai==2.54.0" \
    "mcp==1.30.0" \
    "nltk==3.10.4" \
    "pillow==13.0.0" \
    "pypdf==6.21.4" \
    "pdfplumber==0.11.10" \
    "pdfminer.six==20260107" \
    "pypdfium2==5.14.0" \
    "lxml==6.1.3"

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
