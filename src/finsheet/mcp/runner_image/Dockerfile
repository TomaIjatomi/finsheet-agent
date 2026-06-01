# Sandbox image for the Computation Agent.
#
# Build:
#   docker build -t finsheet-sandbox:latest src/finsheet/mcp/runner_image
#
# This image is invoked once per execute_python MCP tool call with strict
# security flags (see src/finsheet/mcp/sandbox.py::DockerSandbox).
# It loads parquet DataFrames from /data, exec's the supplied code in
# a restricted namespace, and prints a JSON result payload on its last
# stdout line.

FROM python:3.12-slim

# Pin versions for reproducibility. These also need to match what the
# server side writes (parquet format compatibility).
RUN pip install --no-cache-dir \
        pandas==2.2.3 \
        numpy==2.1.3 \
        pyarrow==18.1.0

WORKDIR /sandbox
COPY runner.py /sandbox/runner.py

# Non-root user (matches --user=sandbox in DockerSandbox flags)
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox

# No bytecode writing (we run with --read-only)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/sandbox/runner.py"]
