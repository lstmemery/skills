import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import build_fingerprint_denylist  # noqa: E402
from capture_common import digest  # noqa: E402


class BuildDenylistTest(unittest.TestCase):
    def test_writes_private_fingerprints_only_and_is_idempotent(self):
        value = 'plain-looking-value-0042'
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'sub' / 'fingerprints'
            for _ in range(2):
                with patch('sys.stdin', io.StringIO(value + '\nshort\n')):
                    build_fingerprint_denylist.main(['--file', str(target)])
            self.assertEqual(target.read_text(), digest(value.encode())[:16] + '\n')
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
            self.assertNotIn(value, target.read_text())


if __name__ == '__main__':
    unittest.main()
