# Use an official Python runtime as a parent image
FROM ghcr.io/astral-sh/uv:0.6.14-python3.12-alpine

# Copy the project files into the container
COPY . /app
# Sync the project into a new environment, using the frozen lockfile
WORKDIR /app
RUN uv sync --frozen

# Command to run the knx2mqtt script with the --config parameter
ENTRYPOINT ["uv", "run", "knx2mqtt", "--config", "/app/config.ini"]

# Example usage: Map config and log files when running the container
# docker run -v /path/to/config.ini:/app/config.ini -v /path/to/logfile.log:/app/logfile.log knx2mqtt