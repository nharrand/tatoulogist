"""python -m webapp [--host H] [--port P] [--debug]"""

from __future__ import annotations

import argparse
import logging

from webapp.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tatou-web",
                                     description="Serve and rerun Tatou test reports.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
                        datefmt="%H:%M:%S")
    app = create_app()
    # threaded=True so a rerun's status can be polled while it runs.
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
