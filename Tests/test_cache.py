"""Unit tests for TTLCache."""

import threading
import time
import unittest


class TestTTLCache(unittest.TestCase):
    """Test the in-memory TTL cache."""

    def setUp(self):
        from cache import TTLCache
        self.cache = TTLCache(maxsize=5, ttl=1)

    def test_set_and_get(self):
        self.cache.set("key1", "value1")
        self.assertEqual(self.cache.get("key1"), "value1")

    def test_expired_entry_returns_none(self):
        self.cache.set("key1", "value1", ttl=1)
        time.sleep(1.1)
        self.assertIsNone(self.cache.get("key1"))

    def test_missing_key_returns_none(self):
        self.assertIsNone(self.cache.get("nonexistent"))

    def test_maxsize_eviction(self):
        for i in range(10):
            self.cache.set(f"key{i}", f"val{i}")
        # Cache should not exceed maxsize
        self.assertLessEqual(self.cache.stats()["size"], 5)

    def test_clear(self):
        self.cache.set("key1", "value1")
        self.cache.set("key2", "value2")
        self.cache.clear()
        self.assertEqual(self.cache.stats()["size"], 0)
        self.assertIsNone(self.cache.get("key1"))

    def test_stats(self):
        self.cache.set("k1", "v1")
        self.cache.set("k2", "v2")
        stats = self.cache.stats()
        self.assertEqual(stats["size"], 2)
        self.assertEqual(stats["maxsize"], 5)

    def test_custom_ttl(self):
        self.cache.set("key1", "value1", ttl=5)
        time.sleep(0.1)
        self.assertEqual(self.cache.get("key1"), "value1")

    def test_overwrite(self):
        self.cache.set("key1", "value1")
        self.cache.set("key1", "value2")
        self.assertEqual(self.cache.get("key1"), "value2")

    def test_thread_safety(self):
        """Basic thread-safety smoke test."""
        errors = []

        def writer():
            try:
                for i in range(100):
                    self.cache.set(f"key{i % 5}", f"val{i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for i in range(100):
                    self.cache.get(f"key{i % 5}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)]
        threads += [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread safety errors: {errors}")


class TestNormalizeAndCacheKey(unittest.TestCase):

    def test_normalize_text(self):
        from cache import normalize_text
        self.assertEqual(normalize_text("  Hello  WORLD  "), "hello world")

    def test_cache_key_deterministic(self):
        from cache import cache_key
        k1 = cache_key("hello", "world")
        k2 = cache_key("hello", "world")
        self.assertEqual(k1, k2)

    def test_cache_key_different_inputs(self):
        from cache import cache_key
        k1 = cache_key("hello", "world")
        k2 = cache_key("world", "hello")
        self.assertNotEqual(k1, k2)

    def test_cache_key_ignores_none(self):
        from cache import cache_key
        k1 = cache_key("a", None, "b")
        k2 = cache_key("a", "b")
        self.assertEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()