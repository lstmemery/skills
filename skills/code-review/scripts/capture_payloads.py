"""The single immutable inventory shared by secret screening and storage."""
from __future__ import annotations

from dataclasses import dataclass
import json

from capture_common import encoded


@dataclass(frozen=True)
class PayloadInventory:
    """Labelled destination paths and bytes, assembled once per capture.

    Both the gate and store consume this tuple. No repository reads or manifest
    serialization may occur between screening and storage.
    """

    payloads: tuple[tuple[str, str, bytes], ...]

    @classmethod
    def from_observation(cls, manifest, blobs, diff, history):
        labels = {}
        for side in ('after', 'before'):
            for name in sorted(manifest[side]):
                sha = manifest[side][name].get('sha256')
                if sha in blobs:
                    labels.setdefault(sha, f'{side}:{name}')
        for row in manifest['authorities']:
            sha = row.get('sha256')
            if sha in blobs:
                labels.setdefault(sha, f"authority:{row['path']}")
        payloads = [(labels.get(sha, f'blob:{sha[:16]}'), f'blobs/{sha}', bytes(data)) for sha, data in sorted(blobs.items())]
        payloads.append(('diff.patch', 'diff.patch', bytes(diff)))
        payloads.append(('commits.txt', 'commits.txt', bytes(history)))
        payloads.append(('manifest.json', 'manifest.json', encoded(manifest) + b'\n'))
        return cls(tuple(payloads))

    @property
    def capture_id(self):
        # COMPLETE must identify the frozen manifest, never a later live input.
        return json.loads(self.payloads[-1][2])['capture_id']
