"""Unit tests for utility functions."""

import unittest
from unittest.mock import patch


class TestSanitizeText(unittest.TestCase):

    def test_removes_html_tags(self):
        from utils import sanitize_text
        result = sanitize_text("hello <script>alert('xss')</script>")
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)
        self.assertTrue(result.startswith("hello"))

    def test_preserves_hindi(self):
        from utils import sanitize_text
        self.assertEqual(sanitize_text("जय श्री कृष्ण"), "जय श्री कृष्ण")

    def test_preserves_common_punctuation(self):
        from utils import sanitize_text
        self.assertEqual(sanitize_text("Hello, world! How are you?"), "Hello, world! How are you?")

    def test_removes_special_chars(self):
        from utils import sanitize_text
        result = sanitize_text("hello\x00\x01world")
        self.assertNotIn("\x00", result)


class TestTruncate(unittest.TestCase):

    def test_short_text_unchanged(self):
        from utils import truncate
        self.assertEqual(truncate("hello", 10), "hello")

    def test_long_text_truncated(self):
        from utils import truncate
        result = truncate("a" * 100, 10)
        self.assertEqual(len(result), 10)

    def test_custom_suffix(self):
        from utils import truncate
        result = truncate("a" * 100, 15, suffix="!!")
        self.assertTrue(result.endswith("!!"))


class TestRedactSecret(unittest.TestCase):

    def test_short_value(self):
        from utils import redact_secret
        self.assertEqual(redact_secret(""), "***")

    def test_long_value(self):
        from utils import redact_secret
        result = redact_secret("my_super_secret_token_value")
        self.assertTrue(result.startswith("my_sup"))
        self.assertTrue(result.endswith("***"))


class TestRetryDecorator(unittest.TestCase):

    def test_succeeds_first_try(self):
        from utils import retry, RetryExhausted
        call_count = 0

        @retry(max_attempts=3)
        def succeeds():
            nonlocal call_count
            call_count += 1
            return "ok"

        self.assertEqual(succeeds(), "ok")
        self.assertEqual(call_count, 1)

    def test_retries_then_succeeds(self):
        from utils import retry, RetryExhausted
        call_count = 0

        @retry(max_attempts=3, base_delay=0.01)
        def fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("transient")
            return "ok"

        self.assertEqual(fails_then_succeeds(), "ok")
        self.assertEqual(call_count, 3)

    def test_exhausted_raises(self):
        from utils import retry, RetryExhausted

        @retry(max_attempts=2, base_delay=0.01)
        def always_fails():
            raise ValueError("permanent")

        with self.assertRaises(RetryExhausted):
            always_fails()


class TestSimpleMetrics(unittest.TestCase):

    def test_inc_counter(self):
        from utils import SimpleMetrics
        m = SimpleMetrics()
        m.inc("requests")
        m.inc("requests")
        snap = m.snapshot()
        self.assertEqual(snap["counters"]["requests"], 2)

    def test_set_gauge(self):
        from utils import SimpleMetrics
        m = SimpleMetrics()
        m.set_gauge("latency", 42.5)
        snap = m.snapshot()
        self.assertEqual(snap["gauges"]["latency"], 42.5)


if __name__ == "__main__":
    unittest.main()