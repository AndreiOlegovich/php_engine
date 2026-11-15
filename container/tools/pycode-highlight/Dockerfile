# stdlib-only: no dependencies to install.
FROM python:3.12-slim

COPY highlight_pycode.py /app/highlight_pycode.py
RUN python3 -c "import sys; sys.path.insert(0, '/app'); import highlight_pycode; print('pycode-highlight', highlight_pycode.VERSION)"
WORKDIR /work

# Mount the tree to scan at /work, e.g.:
#   docker build -t pycode-highlight -f Dockerfile .
#   docker run --rm -v "$PWD/../..:/work:ro" pycode-highlight --check /work/src
#   docker run --rm -v "$PWD/../..:/work" pycode-highlight --write /work/src/dev/python
ENTRYPOINT ["python3", "/app/highlight_pycode.py"]
