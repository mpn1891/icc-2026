"""CLI entry point.

    python -m particle_sim --port 8443

The vendor README's quickstart, and it has to keep working exactly as written --
a README that lies about its own quickstart is a bug in the transcription, not a
detail. `CMD` in the Dockerfile runs this same line.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from .config import from_args
from .server import serve


def main(argv=None) -> int:
    cfg = from_args(argv)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    try:
        asyncio.run(serve(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
