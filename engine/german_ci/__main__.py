"""Allow `python3 -m german_ci ...`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
