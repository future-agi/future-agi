FROM futureagi/future-agi-base:v1.0.6

ENV NLTK_DATA=/usr/local/share/nltk_data

# The application source can advance independently of the shared base image.
# Keep small import-critical additions explicit here so every service built
# from this Dockerfile (backend and queue workers alike) has the same runtime.
# Installed before the source copy, so a source change reuses this layer.
RUN pip install --no-cache-dir \
    "daytona==0.207.0" \
    "httpx-ws==0.7.2" \
    "urllib3==2.8.0" \
    "e2b==2.37.1" \
    "claude-agent-sdk==0.2.139" \
    "aiohttp==3.14.5" \
    "yarl==1.25.1" \
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
# daytona, httpx-ws and e2b mirror the `sandbox` extra of
# futureagi/pyproject.toml and claude-agent-sdk its `localizer` extra;
# aiohttp, granian, channels-redis and asgiref mirror futureagi/requirements.txt
# until a future-agi-base rebuilt from it replaces v1.0.6.
# django, litellm, pyjwt and openai are the critical-CVE upgrades of TH-8369
# (CVE-2025-64459, CVE-2026-35030, CVE-2026-49468, CVE-2026-48526); v1.0.5 still
# carries the vulnerable django 5.1.8 / litellm 1.81.11 / pyjwt 2.10.1, so they
# are reinstalled here until the base is rebuilt. openai>=2.20.0 is required by
# litellm 1.100.x, so the base's 2.15.0 has to move with it.
# litellm 1.100.x also needs boto3>=1.43.1, pydantic-settings>=2.14.1 and
# aiohttp>=3.14.2. The base's boto3 1.42.28 and pydantic-settings 2.12.0 are
# older, so boto3, botocore, s3transfer, pydantic-settings and the
# python-dotenv it needs are pinned at the futureagi/uv.lock versions rather
# than whatever is newest at build time. aiohttp and urllib3 are pinned at the
# lock versions too, which meet the futureagi/pyproject.toml floors, with the
# yarl that aiohttp 3.14.5 needs.
# mcp, nltk, pillow, pypdf, pdfminer.six and lxml are the high-CVE upgrades of
# TH-8370 (MCP session authorization and origin/host validation
# — CVE-2026-52869, CVE-2026-52870, CVE-2026-59950; NLTK remote code execution,
# unsafe pickle loading, path traversal and SSRF; and the document/image
# parsers every PDF, image and XML upload reaches). v1.0.5 still carries the
# flagged mcp 1.26.0 / nltk 3.9.2 / pillow 12.1.0 / pypdf 6.6.0 /
# pdfminer.six 20231228 / lxml 6.0.2. pdfplumber pins pdfminer.six to an exact
# release, so 0.11.10 has to move with it; it also wants pypdfium2>=5.9.0 over
# the base's 5.6.0, pinned so this layer stays reproducible instead of taking
# whatever is newest at build time.
# deploy/tests/test_image_standards.py compares the pins.
# granian>=2.7.1 fixes granian#798. channels-redis (CHANNEL_LAYER_BACKEND=redis)
# needs asgiref>=3.9.1; pinning it stops pip from replacing the base's 3.8.1
# with whatever is latest.

COPY futureagi/ .

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
