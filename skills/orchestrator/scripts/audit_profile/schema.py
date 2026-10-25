"""Validate the exact shipped JSON Schema subset, without optional dependencies."""
import datetime as dt
import json
import re
from pathlib import Path

from .errors import Refusal

MAX_BYTES = 65536
SCHEMA_BYTES = Path(__file__).with_name('snapshot.schema.json').read_bytes()
SCHEMA = json.loads(SCHEMA_BYTES)


def _validate(value, rule):
    types = {'object': dict, 'array': list, 'integer': int, 'string': str}
    if type(value) is not types[rule['type']]:
        raise Refusal('schema-refused')
    if 'const' in rule and value != rule['const']:
        raise Refusal('schema-refused')
    if 'enum' in rule and value not in rule['enum']:
        raise Refusal('schema-refused')
    if isinstance(value, dict):
        if set(value) != set(rule['required']):
            raise Refusal('schema-refused')
        for key in value:
            _validate(value[key], rule['properties'][key])
    elif isinstance(value, list):
        if len(value) > rule['maxItems']:
            raise Refusal('schema-refused')
        for item in value:
            _validate(item, rule['items'])
    elif isinstance(value, str):
        if 'pattern' in rule and re.fullmatch(rule['pattern'], value) is None:
            raise Refusal('schema-refused')
        if rule.get('format') == 'date-time':
            try:
                dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
            except ValueError:
                raise Refusal('schema-refused') from None


def validate_snapshot(snapshot):
    _validate(snapshot, SCHEMA)
    for key in ('services', 'certificates'):
        ids = [record['id'] for record in snapshot[key]]
        if len(ids) != len(set(ids)):
            raise Refusal('duplicate-id')
    return snapshot


def canonical(snapshot):
    validate_snapshot(snapshot)
    return json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode() + b'\n'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refusal('duplicate-json-key')
        result[key] = value
    return result


def read_json(raw):
    if len(raw) > MAX_BYTES:
        raise Refusal('input-size-refused')
    try:
        return json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        raise Refusal('json-refused') from None


def read_snapshot(raw):
    return validate_snapshot(read_json(raw))
