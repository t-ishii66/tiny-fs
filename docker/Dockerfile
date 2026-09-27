FROM python:3.11-slim

# fusepy loads libfuse.so.2, so libfuse2 is the one that matters.
# fuse3 is here for the fusermount3 command used to unmount.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fuse3 libfuse2 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fusepy pytest

# The repository is bind-mounted here, so plain "pytest" finds the package
# without installing it.
ENV PYTHONPATH=/work
WORKDIR /work
