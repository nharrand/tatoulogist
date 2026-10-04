FROM python:3.12-slim

# git is needed because one dependency (rmap) is installed from a git URL.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

ENV TATOU_DATA_DIR=/data \
    TATOU_REPORTS_DIR=/reports \
    TATOU_CONTROL_DIR=/control
EXPOSE 8000

COPY flag_1 /app/flag_1.template
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command is the webapp. The rmap-runner service overrides it.
# One worker: the rerun lock is in-process, so multiple workers would break
# the "one run at a time" guarantee for the local (non-RMAP) lane.
CMD ["tatou-web", "--host", "0.0.0.0", "--port", "8000"]
