FROM futureagi/future-agi-base:v1.0.1

# future-agi-base is published from main, so it lags requirements added on a
# feature branch. Install those here until the dependency lands on main and
# the base is republished.
COPY futureagi/requirements.txt /tmp/requirements.txt
RUN uv pip install --system -r /tmp/requirements.txt --no-cache --only-binary av

# AnnotationCorpusBuilder (model_hub/utils/utils.py) calls nltk.download() at
# module import, so a missing corpus blocks Django startup until
# raw.githubusercontent.com responds — minutes on a slow link, and repeated on
# every container recreate because ~/nltk_data is not persisted. Bake them in.
ENV NLTK_DATA=/usr/local/share/nltk_data
RUN python -m nltk.downloader -d $NLTK_DATA \
    punkt averaged_perceptron_tagger wordnet omw-1.4 stopwords

COPY futureagi/ .

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
