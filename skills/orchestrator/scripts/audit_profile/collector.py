"""Trusted collector: two fixed read operations, no journals or config dumps."""
import datetime as dt
import hashlib
import ipaddress
import re
import socket
import ssl

from .errors import Refusal
from .process import capture
from .schema import canonical

STATES = {'active', 'reloading', 'inactive', 'failed', 'activating', 'deactivating',
          'maintenance', 'refreshing'}
ID = r'[a-z][a-z0-9-]{0,47}'


def service_state(identifier, unit, runner=capture):
    if not isinstance(identifier, str) or not re.fullmatch(ID, identifier):
        raise Refusal('request-refused')
    if not isinstance(unit, str) or not re.fullmatch(r'[a-zA-Z0-9_.@-]{1,120}\.service', unit):
        raise Refusal('request-refused')
    result = runner(['/usr/bin/systemctl', 'show', '--property=ActiveState',
                     '--value', '--', unit])
    try:
        state = result.stdout.decode('ascii').strip()
    except UnicodeDecodeError:
        raise Refusal('service-state-refused') from None
    if result.returncode or state not in STATES:
        raise Refusal('service-state-refused')
    return {'id': identifier, 'active_state': state}


def certificate_identity(identifier, der):
    if not isinstance(identifier, str) or not re.fullmatch(ID, identifier):
        raise Refusal('request-refused')
    if not isinstance(der, bytes) or not 0 < len(der) <= 65536:
        raise Refusal('certificate-refused')
    result = capture(['/usr/bin/openssl', 'x509', '-inform', 'DER', '-noout', '-enddate'],
                     input_bytes=der)
    try:
        text = result.stdout.decode('ascii')
        if not re.fullmatch(r'notAfter=[A-Z][a-z]{2} +[0-9]{1,2} [0-9:]{8} [0-9]{4} GMT\n', text):
            raise Refusal('certificate-refused')
        expiry = dt.datetime.strptime(text.strip()[9:], '%b %d %H:%M:%S %Y GMT')
    except (UnicodeError, ValueError):
        raise Refusal('certificate-refused') from None
    return {'id': identifier, 'sha256': hashlib.sha256(der).hexdigest(),
            'not_after': expiry.strftime('%Y-%m-%dT%H:%M:%SZ')}


def peer_certificate(host, port, server_name):
    # Inventory only: deliberately observes expired/self-signed public certificates.
    # No client certificate, system trust store, credential or application request.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname=server_name) as tls:
                return tls.getpeercert(binary_form=True)
    except OSError:
        raise Refusal('certificate-connection-refused') from None


def collect(plan):
    if type(plan) is not dict or set(plan) != {'services', 'certificates'}:
        raise Refusal('request-refused')
    for kind in ('services', 'certificates'):
        if type(plan[kind]) is not list or len(plan[kind]) > 32:
            raise Refusal('request-refused')
        for request in plan[kind]:
            fields = {'id', 'unit'} if kind == 'services' else {'id', 'host', 'port', 'server_name'}
            if type(request) is not dict or set(request) != fields:
                raise Refusal('request-refused')
            if type(request['id']) is not str or not re.fullmatch(ID, request['id']):
                raise Refusal('request-refused')
            if kind == 'services':
                if type(request['unit']) is not str or not re.fullmatch(r'[a-zA-Z0-9_.@-]{1,120}\.service', request['unit']):
                    raise Refusal('request-refused')
            else:
                try:
                    ipaddress.ip_address(request['host'])
                except (ValueError, TypeError):
                    raise Refusal('request-refused') from None
                if type(request['port']) is not int or not 1 <= request['port'] <= 65535:
                    raise Refusal('request-refused')
                if type(request['server_name']) is not str or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', request['server_name']):
                    raise Refusal('request-refused')
    # Validate the entire trusted plan before contacting any source.
    snapshot = {'schema_version': 1, 'services': [], 'certificates': []}
    for request in plan['services']:
        snapshot['services'].append(service_state(request['id'], request['unit']))
    for request in plan['certificates']:
        der = peer_certificate(request['host'], request['port'], request['server_name'])
        snapshot['certificates'].append(certificate_identity(request['id'], der))
    canonical(snapshot)
    return snapshot
