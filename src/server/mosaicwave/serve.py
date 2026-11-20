"""``python -m mosaicwave.serve``: the one way every launcher starts the API. See ``mosaicwave.bind``."""

from __future__ import annotations

import sys

from mosaicwave.bind import main

if __name__ == "__main__":
    sys.exit(main())
