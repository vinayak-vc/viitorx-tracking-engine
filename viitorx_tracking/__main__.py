"""`python -m viitorx_tracking <command>` (and the entry point of the compiled `viitorx-tracking.exe`)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
