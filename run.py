"""Entry point: python3 run.py setup | teardown | watchdog | pin-image TAG"""
import os
import sys

from hotline_action.cli import main

sys.exit(main(sys.argv[1:], dict(os.environ)))
