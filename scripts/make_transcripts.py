"""Generate the sample run transcripts for the submission (FR-033, TASK-033).

Runs three real agent sessions with the configured LLM and saves the rendered CLI output to
transcripts/. Approvals and clarification answers are passed as flags so the runs are
repeatable; everything else (plans, reasoning, actions) comes from the live model.

Usage (from the repo root, with GROQ_API_KEY set in .env):
    python scripts/make_transcripts.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "transcripts"

RUNS = [
    (
        "01_refund_approval_parallel.txt",
        "Refund above the approval threshold, human approval, then restock + email in parallel",
        ["Rahul Mehta says the left ear cup of the headphones from order 1042 crackles and he "
         "wants a refund. Refund him, restock the returned unit and email him about it.",
         "--approve", "all"],
    ),
    (
        "02_policy_and_memory.txt",
        "Policy check using long-term memory and refund history (repeat-refund escalation)",
        ["Rahul Mehta (C001) is asking whether order 1042 can still be refunded. Check our "
         "policy and his history, explain, and only add a note to his profile if needed.",
         "--approve", "none"],
    ),
    (
        "03_clarification_and_induced_failures.txt",
        "Ambiguous request -> clarification; injected tool timeout and malformed LLM output "
        "are recovered; HIGH-risk cancel approved",
        ["Cancel Priya's order.",
         "--answer", "Cancel order 1051, the desk lamp. She no longer needs it.",
         "--approve", "all",
         "--inject-failure", "tool_timeout,bad_llm_output"],
    ),
]


def main() -> int:
    from opsagent.cli import app
    from opsagent.seed import seed_sqlite

    OUT.mkdir(exist_ok=True)
    runner = CliRunner()
    combined = ["# OpsAgent sample run transcripts", "",
                "Real runs against the live LLM, captured with `opsagent ask ... --transcript`.",
                ""]
    ok = True
    for fname, title, args in RUNS:
        print(f"== {fname}: reseeding store...", flush=True)
        seed_sqlite()  # fresh store for each run (same process, no file-lock issues)
        path = OUT / fname
        print(f"== running: {title} (can take 1 to 3 minutes on the free tier)", flush=True)
        result = runner.invoke(app, ["ask", *args, "--transcript", str(path)])
        if result.exit_code != 0:
            ok = False
            print(result.output[-3000:])
            if result.exception:
                print(f"ERROR: {type(result.exception).__name__}: {result.exception}")
        text = path.read_text(encoding="utf-8") if path.exists() else result.output
        combined += [f"## {fname}", "", f"_{title}_", "", "```text", text.rstrip(), "```", ""]
        print(f"   saved {path.name}", flush=True)
    (OUT / "ALL_TRANSCRIPTS.md").write_text("\n".join(combined), encoding="utf-8")
    print(f"Saved transcripts to {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
