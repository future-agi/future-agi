FROM futureagi/future-agi:v1.8.19_base

COPY . .

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
