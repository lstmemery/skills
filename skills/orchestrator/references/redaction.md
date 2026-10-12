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

Never run `cat wg0.conf` or `wg showconf`. Use this exact field selection to
omit private and preshared key columns:

```sh
wg show wg0 dump | cut -f 1,3-5
```

When reading a config file, remove key lines before any other parsing:

```sh
sed '/^[[:space:]]*\(PrivateKey\|PresharedKey\)[[:space:]]*=/d' wg0.conf
```

## Config, environment, and secret inventories

Print key names only, or apply this exact redaction before any other parsing:

```sh
sed 's/=.*/=<redacted>/'
```

Never print a raw config line as a diagnostic. If anything secret reaches your
output anyway, say so in `result.json` under `unresolved`.
