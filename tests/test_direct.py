import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from codex_window.core import ping, WindowError

class Direct(unittest.TestCase):
    def request(self, events, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        Path(temp.name, "auth.json").write_text(json.dumps({"tokens": {"access_token": "test-only", "account_id": "test"}}))
        stream = io.BytesIO(b"".join(b"data: "+json.dumps(e).encode()+b"\n\n" for e in events))
        with patch.dict("os.environ", {"CODEX_HOME": temp.name}), patch("urllib.request.build_opener") as build:
            build.return_value.open.return_value = stream
            result = ping("codex", "low", **kwargs)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(request.data)["tools"], [])
            self.assertEqual(json.loads(request.data)["model"], "gpt-5.6-luna")
            self.assertEqual(build.return_value.open.call_count, 1)
            return result

    def test_full_stream_with_delta_and_usage(self):
        result = self.request([
            {"type":"response.output_text.delta", "delta":"1"},
            {"type":"response.completed", "response":{"usage":{"input_tokens":16,"output_tokens":5,"attribution":{"private":"omit"}}}}])
        self.assertEqual(result["answer"], "1")
        self.assertNotIn("attribution", result["usage"])

    def test_incomplete_stream_is_not_success(self):
        with self.assertRaises(WindowError):
            self.request([{"type":"response.output_text.delta","delta":"1"}])

    def test_failed_stream_is_not_success(self):
        with self.assertRaises(WindowError):
            self.request([{"type":"response.failed"}])

    def test_account_change_prevents_request(self):
        with self.assertRaisesRegex(WindowError, "account changed"):
            self.request([], expected_account="another-account")
