"""Explicit pilot CLI; never switches an ordinary worker into this profile."""
import argparse
import json
import sys
from pathlib import Path

from .collector import collect
from .errors import Refusal
from .launch import launch_profile
from .schema import MAX_BYTES, canonical, read_json


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal('arguments-refused')


def bounded_file(path):
    with Path(path).open('rb') as stream:
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise Refusal('input-size-refused')
    return content


def main():
    parser = Parser(description='Credential-isolated offline audit pilot')
    commands = parser.add_subparsers(dest='operation', required=True)
    collector = commands.add_parser('collect')
    collector.add_argument('--plan', required=True)
    launcher = commands.add_parser('launch')
    launcher.add_argument('--snapshot', required=True)
    launcher.add_argument('--worker', required=True)
    launcher.add_argument('--output', required=True)
    try:
        args = parser.parse_args()
        if args.operation == 'collect':
            result = canonical(collect(read_json(bounded_file(args.plan))))
        else:
            result = json.dumps(launch_profile(bounded_file(args.snapshot),
                                bounded_file(args.worker), Path(args.output)), sort_keys=True).encode() + b'\n'
    except Refusal as error:
        sys.stdout.write(json.dumps({'refused': str(error)}) + '\n')
        return 70
    except (OSError, UnicodeError):
        sys.stdout.write('{"refused":"io-unavailable"}\n')
        return 70
    sys.stdout.buffer.write(result)
    return 0


if __name__ == '__main__':
    sys.exit(main())
