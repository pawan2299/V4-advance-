"""Unit tests for security module."""

import hashlib
import hmac
import os
import unittest
from unittest.mock import patch, MagicMock
from dataclasses import replace

# Set required env vars BEFORE importing config-dependent modules
os.environ.setdefault("VERIFY_TOKEN", "test_verify_token")
os.environ.setdefault("APP_SECRET", "test_app_secret")
os.environ.setdefault("PAGE_ID", "123")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/d")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:abc")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test_telegram_secret")

import security
from config import Settings


def _mock_settings(**overrides):
    """Create a MagicMock that behaves like Settings for patching."""
    mock = MagicMock()
    # Set all default fields
    defaults = {
        "verify_token": "test_verify_token",
        "app_secret": "test_app_secret",
        "telegram_webhook_secret": "test_telegram_secret",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


class TestVerifySignature(unittest.TestCase):
    """Test Meta webhook HMAC-SHA256 signature verification."""

    def test_valid_signature(self):
        secret = "test_app_secret"
        payload = b'{"object":"instagram","entry":[]}'
        expected = "sha256=" + hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256).hexdigest()

        with patch("security.SETTINGS", _mock_settings(app_secret=secret)):
            self.assertTrue(security.verify_signature(payload, expected))

    def test_invalid_signature(self):
        with patch("security.SETTINGS", _mock_settings(app_secret="test_secret")):
            self.assertFalse(security.verify_signature(b'{"object":"instagram"}', "sha256=invalid"))

    def test_missing_signature(self):
        with patch("security.SETTINGS", _mock_settings(app_secret="test_secret")):
            self.assertFalse(security.verify_signature(b"{}", ""))

    def test_missing_secret_rejects(self):
        with patch("security.SETTINGS", _mock_settings(app_secret="")):
            self.assertFalse(security.verify_signature(b"{}", "sha256=something"))


class TestVerifyMetaToken(unittest.TestCase):
    """Test constant-time verify token comparison."""

    def test_valid_token(self):
        with patch("security.SETTINGS", _mock_settings(verify_token="my_token")):
            self.assertTrue(security.verify_meta_verify_token("my_token"))

    def test_invalid_token(self):
        with patch("security.SETTINGS", _mock_settings(verify_token="my_token")):
            self.assertFalse(security.verify_meta_verify_token("wrong"))

    def test_empty_token(self):
        with patch("security.SETTINGS", _mock_settings(verify_token="my_token")):
            self.assertFalse(security.verify_meta_verify_token(""))


class TestVerifyTelegramSecret(unittest.TestCase):
    """Test Telegram webhook secret verification."""

    def test_valid_secret(self):
        with patch("security.SETTINGS", _mock_settings(telegram_webhook_secret="my_telegram_secret")):
            self.assertTrue(security.verify_telegram_secret("my_telegram_secret"))

    def test_invalid_secret(self):
        with patch("security.SETTINGS", _mock_settings(telegram_webhook_secret="my_telegram_secret")):
            self.assertFalse(security.verify_telegram_secret("wrong"))

    def test_no_secret_configured_rejects(self):
        with patch("security.SETTINGS", _mock_settings(telegram_webhook_secret="")):
            self.assertFalse(security.verify_telegram_secret("anything"))


if __name__ == "__main__":
    unittest.main()