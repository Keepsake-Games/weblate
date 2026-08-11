FROM docker.io/weblate/weblate:2026.7.1.1
USER root
ADD src.tar /src/
RUN /app/venv/bin/python -m ensurepip \
 && /app/venv/bin/python -m pip install --no-deps /src \
 && rm -rf /src
USER 1000