import sys
from pathlib import Path

from .common import System
from .pin_image import run_pin
from .setup_job import run_setup
from .teardown import run_teardown
from .watchdog import run_watchdog

ROOT = Path(__file__).resolve().parent.parent


def main(argv, env):
    system = System()
    command = argv[0] if argv else ""
    if command == "setup":
        run_setup(env, system, ROOT)
    elif command == "teardown":
        run_teardown(env, system, ROOT)
    elif command == "watchdog":
        run_watchdog(env, system, ROOT)
    elif command == "pin-image" and len(argv) == 2:
        return run_pin(argv[1], ROOT, system)
    else:
        print("usage: run.py setup | teardown | watchdog | pin-image TAG", file=sys.stderr)
        return 2
    return 0  # setup, teardown and the watchdog never fail the job
