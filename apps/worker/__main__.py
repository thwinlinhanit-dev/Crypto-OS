"""``python -m apps.worker`` -- run the job worker (design note section 10).

Exits on Ctrl-C. Reads no environment beyond ``DATA_DIR``/``DATABASE_URL``
via the shared settings (PD-11).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from apps.worker.runner import run_forever


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m apps.worker",
        description="Drain the CryptoOS system.job queue.",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="sleep between empty polls (default: 2.0)",
    )
    parser.add_argument(
        "--recover-stale",
        type=int,
        default=None,
        metavar="SECONDS",
        help="before looping, requeue jobs running longer than SECONDS "
        "(the spec names no recovery age, so this stays opt-in)",
    )
    parser.add_argument("--log-level", default="INFO", metavar="LEVEL")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(run_forever(poll_seconds=args.poll, recover_stale_after=args.recover_stale))


if __name__ == "__main__":
    main()
