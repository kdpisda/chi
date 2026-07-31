# chi-agent: the container image chi's Docker sandboxes run coder agents in.
#
# Built for both sandbox tiers (see docker/README.md and SECURITY.md):
#   sandbox: docker      — full jail (no network, no host auth)
#   sandbox: docker-cli  — vendor CLI coders (bridge network + read-only auth)
#
# Build:  docker build -t chi-agent -f docker/chi-agent.Dockerfile .
#
# node:20-slim is Debian (bookworm) based; Node 20+ is required because the
# claude CLI is npm-distributed.
FROM node:20-slim

# Minimal toolchain an agent needs to build/inspect a candidate:
# python3 (chi problems are Python), git, curl + ca-certificates (vendor API
# over TLS in docker-cli mode; harmless under --network none).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The claude CLI. codex/grok are vendor-distributed binaries — extend this
# image (FROM chi-agent) and add them if you run those coders sandboxed.
RUN npm install -g @anthropic-ai/claude-code

# Non-root user. /home/agent is the mount target for the docker-cli preset's
# read-only auth mounts (~/.claude -> /home/agent/.claude, etc.).
RUN useradd --create-home --shell /bin/bash agent
USER agent
ENV HOME=/home/agent

# Chi mounts the coder's workdir here (build_docker_command).
WORKDIR /workspace
