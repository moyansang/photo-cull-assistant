"""Explicit, read-only autofocus metadata inspection, separate from culling."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images',nargs='+',type=Path,help='Original camera JPEG/RAW files to inspect')
    parser.add_argument('--exiftool',type=Path,help='Trusted external ExifTool executable; otherwise use PATH')
    parser.add_argument('--timeout',type=float,default=10.0,help='Timeout per parser subprocess in seconds (default 10)')
    parser.add_argument('--output',type=Path,help='Create a NEW JSON report; default writes JSON to stdout')
    args=parser.parse_args(argv)
    if not 0 < args.timeout <= 120:
        parser.error('--timeout must be finite, >0 and <=120')
    if args.output:
        target=args.output.resolve()
        if target.exists() or any(target==path.resolve() for path in args.images):
            parser.error('--output must be a new file, never an input or existing file')
    from .af_metadata import read_af_metadata
    records=[read_af_metadata(path,exiftool_path=args.exiftool,timeout=args.timeout) for path in args.images]
    payload={'purpose':'read-only camera AF metadata; no subject selection or quality decision',
             'records':records,'automatic_decisions_enabled':False}
    encoded=json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if args.output:
        # Exclusive creation avoids overwriting a source through a race or symlink.
        try:
            with args.output.open('x',encoding='utf-8') as output:output.write(encoded)
        except OSError as exc:
            parser.exit(2,f'Cannot create report: {exc}\n')
    else:
        sys.stdout.write(encoded)
    return 2 if any(row.get('status')=='error' for row in records) else 0


if __name__=='__main__':raise SystemExit(main())
