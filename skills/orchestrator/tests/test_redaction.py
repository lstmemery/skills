import re
import unittest
from pathlib import Path


class RedactionReferenceTests(unittest.TestCase):
    def test_wireguard_dump_command_is_not_documented(self):
        reference = Path(__file__).resolve().parents[1] / "references" / "redaction.md"
        self.assertNotRegex(reference.read_text(), re.compile(r"wg show .* dump"))


if __name__ == "__main__":
    unittest.main()
