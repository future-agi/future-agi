FROM futureagi/future-agi-base:v1.0.2

COPY futureagi/ .

# The application source can advance independently of the shared base image.
# Keep small import-critical additions explicit here so every service built
# from this Dockerfile (backend and queue workers alike) has the same runtime.
RUN pip install --no-cache-dir \
    "disposable-email-domains==0.0.239" \
    "daytona==0.207.0" \
    "httpx-ws==0.7.2" \
    "urllib3>=2.1"

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
