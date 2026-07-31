"""Sandbox: the DI seam for *where* an agent's command runs (item 2).

pi's insight: don't bake execution into the tool/adapter — inject it, so the
same agent can run locally or in an isolated container without touching adapter
code. Chi's dogfood proved the need: a coder agent with a host shell fired
popcorn submissions directly, bypassing chi's gate. The real fix is to run the
agent where it *cannot* reach host credentials or the host submission binary,
and let chi mediate every privileged action at the boundary.

- `LocalSandbox` — the current behavior (host subprocess with the shimmed env).
- `DockerSandbox` — runs the agent command in a container with only the workdir
  mounted, no host credentials, and network policy of your choice. Popcorn auth
  (file-based, in $HOME) is not mounted, so an agent in the container physically
  cannot submit — chi submits from the host, gated.

Both implement one `run(argv, cwd, env, timeout)` contract, so an adapter is
sandbox-agnostic. The Docker command is built by a pure function so it is
testable without Docker installed.
"""

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


class Sandbox(Protocol):
    """Runs an agent command somewhere; returns a CompletedProcess-like result."""

    def run(self, argv: list[str], cwd: Path, env: dict,
            timeout: int) -> subprocess.CompletedProcess: ...


@dataclass
class LocalSandbox:
    """Run on the host (current behavior). No isolation — see SECURITY.md."""

    runner: Callable | None = None

    def run(self, argv, cwd, env, timeout) -> subprocess.CompletedProcess:
        run = self.runner or subprocess.run
        return run(argv, cwd=cwd, capture_output=True, text=True, env=env,
                   timeout=timeout)


def build_docker_command(
    image: str,
    argv: list[str],
    cwd: Path,
    *,
    network: str = "none",
    extra_mounts: list[tuple[str, str]] | None = None,
    readonly_mounts: list[tuple[str, str]] | None = None,
    workdir_in_container: str = "/workspace",
) -> list[str]:
    """Construct a `docker run` argv that jails the agent to its workdir.

    - only `cwd` is mounted (read-write) at /workspace; $HOME (with popcorn/CLI
      auth) is NOT mounted, so the agent can't submit or read host secrets;
    - `--network none` by default (no exfiltration / no direct submission);
    - `readonly_mounts` are rendered with an `:ro` suffix (used by the
      docker-cli preset to expose vendor CLI auth without letting the agent
      alter it);
    - `--rm` so nothing persists.
    """
    cmd = ["docker", "run", "--rm", "--network", network,
           "-v", f"{Path(cwd).resolve()}:{workdir_in_container}",
           "-w", workdir_in_container]
    for host, cont in (extra_mounts or []):
        cmd += ["-v", f"{Path(host).resolve()}:{cont}"]
    for host, cont in (readonly_mounts or []):
        cmd += ["-v", f"{Path(host).resolve()}:{cont}:ro"]
    cmd.append(image)
    cmd += argv
    return cmd


#: host auth dirs the docker-cli preset may expose (read-only) to the agent,
#: mapped into the container home /home/agent. Deliberately tiny: vendor CLI
#: auth only — never popcorn/leaderboard auth, so gated submission still holds.
CLI_AUTH_ALLOWLIST: list[tuple[str, str]] = [
    (".claude", "/home/agent/.claude"),  # claude CLI
    (".codex", "/home/agent/.codex"),  # codex CLI
    (".grok", "/home/agent/.grok"),  # grok CLI
]


def cli_auth_mounts() -> list[tuple[str, str]]:
    """Allowlisted vendor-CLI auth paths that exist on this host (host, container).

    Pure lookup against `CLI_AUTH_ALLOWLIST`; paths absent from the host are
    skipped so `docker run` never creates empty root-owned mount points.
    """
    home = Path.home()
    return [(str(home / name), cont)
            for name, cont in CLI_AUTH_ALLOWLIST if (home / name).exists()]


@dataclass
class DockerSandbox:
    """Run the agent command inside a container jailed to its workdir."""

    image: str
    network: str = "none"
    extra_mounts: list[tuple[str, str]] | None = None
    readonly_mounts: list[tuple[str, str]] | None = None
    runner: Callable | None = None

    def run(self, argv, cwd, env, timeout) -> subprocess.CompletedProcess:
        docker_cmd = build_docker_command(
            self.image, argv, cwd, network=self.network,
            extra_mounts=self.extra_mounts,
            readonly_mounts=self.readonly_mounts)
        run = self.runner or subprocess.run
        # env passed to `docker run` itself only affects the docker client; the
        # container starts from the image's clean env (no host creds leak in)
        return run(docker_cmd, cwd=cwd, capture_output=True, text=True,
                   timeout=timeout)


def make_sandbox(kind: str, image: str | None = None,
                 network: str = "none") -> Sandbox:
    """Build the configured sandbox for a coder.

    Kinds: "none" (host), "docker" (full jail: no network, no host auth), and
    "docker-cli" — a *weaker* preset for vendor CLI coders (claude/grok/codex)
    that need their vendor API and host auth to run at all: bridge network plus
    read-only `cli_auth_mounts()`. Popcorn/leaderboard auth is still never
    mounted. See SECURITY.md ("Sandbox tiers") before opting in.
    """
    if kind in ("none", "", None):
        return LocalSandbox()
    if kind == "docker":
        if not image:
            raise ValueError("docker sandbox requires 'sandbox_image' in the coder config")
        return DockerSandbox(image=image, network=network)
    if kind == "docker-cli":
        if not image:
            raise ValueError("docker-cli sandbox requires 'sandbox_image' in the coder config")
        # preset implies bridge — a CLI coder without its vendor API is useless,
        # and `network` arrives as the config default ("none") when unset
        return DockerSandbox(image=image, network="bridge",
                             readonly_mounts=cli_auth_mounts())
    raise ValueError(f"unknown sandbox kind: {kind}")
