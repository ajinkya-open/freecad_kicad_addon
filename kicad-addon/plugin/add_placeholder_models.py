#!/usr/bin/env python3
"""KiCad action entrypoint for populating placeholder 3D models."""

from __future__ import annotations

import argparse
import sys
import time

from kipy import KiCad

from placeholder_models import add_placeholder_models


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Populate footprints with Kikakuka placeholder 3D models."
    )
    parser.add_argument(
        "--socket-path", "-s", default=None, help="KiCad IPC socket path"
    )
    parser.add_argument(
        "--token", "-t", default=None, help="KiCad IPC token"
    )
    parser.add_argument(
        "--client-name", "-c", default=None, help="IPC client name"
    )
    parser.add_argument(
        "--no-viewer", action="store_true", help="Do not open 3D Viewer"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress status output"
    )
    return parser.parse_known_args(argv)[0]


def connect_with_retry(args, max_attempts: int = 4, delay: float = 0.5):
    """Connect to KiCad and retrieve board with transient busy retries."""
    last_error = None
    for attempt in range(max_attempts):
        try:
            kicad = KiCad(
                socket_path=args.socket_path,
                kicad_token=args.token,
                client_name=args.client_name,
            )
            board = kicad.get_board()
            return kicad, board
        except Exception as error:
            last_error = error
            err_msg = str(error).lower()
            if "busy" in err_msg or "no handler available" in err_msg:
                time.sleep(delay)
                continue
            raise
    raise last_error


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        kicad, board = connect_with_retry(args)
        result = add_placeholder_models(kicad, board)
        if not args.no_viewer:
            try:
                kicad.run_action("common.Control.show3DViewer")
            except Exception:
                pass
    except Exception as error:
        err_text = str(error)
        if "no handler available" in err_text or "busy" in err_text:
            err_text = (
                f"{err_text} (KiCad is busy or a modal dialog is open in KiCad. "
                "Please close any open dialogs and retry.)"
            )
        print(f"Kikakuka: failed to populate placeholder models: {err_text}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(
            "Kikakuka: scanned {scanned} footprints; added/updated {added} placeholder "
            "models; {valid} already had a valid model; {missing} lacked size "
            "properties; {invalid} had invalid sizes.".format(
                scanned=result.scanned,
                added=result.added or result.updated,
                valid=result.already_valid,
                missing=result.missing_size,
                invalid=result.invalid_size,
            )
        )
        for detail in result.details:
            print(f"Kikakuka: skipped {detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

