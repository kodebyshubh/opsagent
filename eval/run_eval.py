"""Convenience wrapper: python eval/run_eval.py [S-01 S-03 ...] (same as `opsagent eval`)."""

import sys

from rich.console import Console

from opsagent.evaluation import run_eval

if __name__ == "__main__":
    sys.exit(0 if run_eval(Console(), sys.argv[1:] or None) else 1)
