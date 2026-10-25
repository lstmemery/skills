# Worker output redaction

Every brief for live-inspection, audit, or secrets work must contain an
`Output redaction` section with the applicable rule below. Copy host-specific
rules and commands verbatim into the brief. Run filtering in the same command
that reads the data so unfiltered output never reaches the model.

## General rule

Delegated workers must pipe any command that can print credentials (service
journals such as cloudflared/caddy/plex, `*-headers`/`*token*` helpers,
env/printenv, config dumps, `curl -v`, docker inspect) through a redaction sed
before the output reaches the model, and must never execute unknown helper
scripts just to see their version.

## TrueNAS API, config, and certificate objects

TrueNAS config/cert endpoints return private keys inline. Select named fields
only, never a full config/certificate object, and pipe through this key-block
filter in the same command:

```sh
sed '/-----BEGIN .*PRIVATE KEY-----/,/-----END .*PRIVATE KEY-----/c\[REDACTED KEY]'
```

For the UI certificate, select only `ui_certificate.id`,
`ui_certificate.name`, and `ui_certificate.until`; never print the full object.
For example, after obtaining the JSON response, use this field selection:

```sh
jq '{ui_certificate: {id: .ui_certificate.id, name: .ui_certificate.name, until: .ui_certificate.until}}'
```

Use the key-block `sed` in the same command as the API call as an additional
guard, including when selecting named fields.

## WireGuard (`wg0`)

Never use WireGuard's dump subcommand: its interface row starts with the
private key.
Use only these key-free subcommands:

```sh
wg show wg0 listen-port
wg show wg0 peers
wg show wg0 allowed-ips
wg show wg0 latest-handshakes
wg show wg0 endpoints
```

Never run `cat wg0.conf` or `wg showconf`. When reading a config file, remove
key lines in the same command before any other parsing:

```sh
sed '/^[[:space:]]*\(PrivateKey\|PresharedKey\)[[:space:]]*=/d' wg0.conf | <other parsing>
```

## Config, environment, and secret inventories

Print key names only, or apply this exact redaction before any other parsing:

```sh
sed 's/=.*/=<redacted>/'
```

Never print a raw config line as a diagnostic. If anything secret reaches your
output anyway, report the incident without repeating its value through the
active contract:

- Direct workers: record it under `unresolved` in `result.json`.
- Receipt-only managed jobs: record it in `result.md` and under `unresolved` in
  an atomic `receipt.json`.
- Managed jobs with `worker_result` enabled: record it under `unresolved` in
  `result.json` and in an atomic `receipt.json`. The receipt must list
  `result.json` and every artifact it names.

For both managed contracts, write the receipt last, after the result and every
required artifact are durable, as specified in [managed-jobs.md](managed-jobs.md).

## Calibrate the output boundary and record its version

A clean detector scan means only that the selected detector/configuration found
nothing. It does not establish that an arbitrary response contains no secrets.
Before adopting or changing a detector, exercise a positive control, benign
control, bare credential, cookie JSON, private-key assignment, and encoded
credential envelope, plus a scanner-error case, using synthetic values. Record
the detector version, configuration hash, decoding limits, exit status, rule
IDs, and finding counts; never publish matched values. Record misses explicitly
and keep detection separate from
sanitization: a findings-producing scanner is not a redacting pipe.

Prefer named, selected-field read operations for known credential-bearing
sources. A voluntary shell filter does not enforce isolation when the same
worker can read the source through another command. Treat a credential-isolated
reader as a separate reviewed capability with an explicit access boundary;
proposing it does not authorize installation or a change to jail controls.

At admission, record the redaction reference revision/hash and result contract
used to render the brief. When an incident establishes that a recipe fails,
mark that recipe superseded and check the rendered next batch rather than
assuming the retrospective changed active templates. Report incident ownership
through the active result/receipt contract. Historical briefs remain immutable.

Until the helper emits this provenance itself, retain the reference path,
Git revision, SHA-256 of the rendered reference bytes, and active contract in
the coordinator's admission evidence. This is a manual record, not a claim of
helper enforcement or permission to alter launcher telemetry.
