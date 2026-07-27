"""Adapter that drives a headless vendor CLI (claude -p, codex exec, ...)."""

import shlex
import subprocess
import threading
import time

from chi.agents.protocol import CoderAdapter, IterationOutcome, SeedContext

PROMPT_TEMPLATE = """# Chi coder task (iteration {iteration})

You are a coder agent in the Chi autoresearch harness, working in this directory.
Goal: improve the score of `{candidate}` ({metric}, {direction}) while staying correct.

State: baseline={baseline}  champion={champion}
{mutation_note}

## Steering directive (obey it)
{steering}

## Dead ends — do NOT retry these
{dead_ends}

## Protocol (mandatory)
1. Edit ONLY `{candidate}`.
2. After each edit run:  chi eval --run-dir {run_dir} --agent {agent_id}
3. To search prior work:  chi query --run-dir {run_dir} "<text>"
4. If an approach measurably fails, record it:
   chi deadend --run-dir {run_dir} --agent {agent_id} --approach-class <c> \\
     --summary "<what failed>" --evidence-json '{{"delta_pct": <n>}}'
5. Stop after you have a measured improvement (or one honest dead-end).
"""


class CliSubprocessAdapter(CoderAdapter):
    """Runs one vendor-CLI session per iteration; harness does the bookkeeping."""

    def _eval_count(self) -> int:
        rows = self.store.query(
            "SELECT COUNT(*) AS n FROM experiments WHERE run_id=? AND author=?",
            (self.run_id, self.agent_id),
        )
        return int(rows[0]["n"])

    def run_iteration(self, seed: SeedContext) -> IterationOutcome:
        """Write the prompt, run the CLI in the workdir, count resulting evals."""
        self.ack_steering(seed.steering_hash)
        before = self._eval_count()
        dead = "\n".join(
            f"- [{d['approach_class']}] {d['summary']} (scope: {d['ruled_out_scope']})"
            for d in seed.dead_ends
        ) or "- none yet"
        prompt_dir = self.workdir / ".chi"
        prompt_dir.mkdir(exist_ok=True)
        prompt_file = prompt_dir / "prompt.md"
        prompt_file.write_text(PROMPT_TEMPLATE.format(
            iteration=seed.iteration, candidate=seed.problem.candidate,
            metric=seed.problem.score.metric, direction=seed.problem.score.direction,
            baseline=seed.baseline_score, champion=seed.champion_score,
            mutation_note=seed.mutation_note, steering=seed.steering_text,
            dead_ends=dead, run_dir=self.store.run_dir.resolve(), agent_id=self.agent_id,
        ))

        stop = threading.Event()

        def _beat() -> None:
            while not stop.wait(self.policies.heartbeat_seconds):
                self.heartbeat()

        thread = threading.Thread(target=_beat, daemon=True)
        thread.start()
        self.heartbeat()
        note = ""
        stdout = stderr = ""
        # agents run with a shimmed PATH: they may benchmark via popcorn, but a
        # ranked leaderboard submit is refused unless it comes through chi's gate
        import os

        from chi.eval.popcorn_shim import agent_env, install_shim

        shim_dir = self.workdir / ".chi" / "bin"
        install_shim(shim_dir)
        env = agent_env(os.environ, shim_dir)
        try:
            proc = self.sandbox.run(
                shlex.split(self.command.format(prompt_file=prompt_file.resolve())),
                self.workdir, env, self.policies.iteration_timeout_seconds,
            )
            stdout, stderr = proc.stdout, proc.stderr
            if proc.returncode != 0:
                note = f"exit {proc.returncode}"
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            note = "timeout"
        finally:
            stop.set()
        log = self.store.run_dir / "mirror" / f"agent_{self.agent_id}_iter{seed.iteration}.log"
        log.write_text(f"ts={time.time()}\nnote={note}\n--- stdout ---\n{stdout}\n"
                       f"--- stderr ---\n{stderr}\n")
        return IterationOutcome(evals_run=self._eval_count() - before, note=note)
