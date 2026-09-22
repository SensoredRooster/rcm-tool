import unittest

from pair_delta import classify_pair_delta


def metrics(**kwargs):
    base = {f"{axis}_high_freq_rms": 0.001 for axis in ("lx", "ly", "rx", "ry")}
    base.update(kwargs)
    return base


class PairDeltaTests(unittest.TestCase):
    def test_neutral_unchanged(self):
        result = classify_pair_delta(
            "neutral-stick",
            "neutral-stick",
            "pass",
            "pass",
            metrics(),
            metrics(lx_high_freq_rms=0.002),
        )
        self.assertEqual(result["pair_delta"], "unchanged")

    def test_neutral_increased_at_rest(self):
        result = classify_pair_delta(
            "neutral-stick",
            "neutral-stick",
            "pass",
            "review",
            metrics(),
            metrics(lx_high_freq_rms=0.012),
        )
        self.assertEqual(result["pair_delta"], "increased_at_rest")

    def test_guided_increased_high_freq(self):
        result = classify_pair_delta(
            "guided-movement",
            "guided-movement",
            "pass",
            "review",
            metrics(),
            metrics(ly_high_freq_rms=0.02),
        )
        self.assertEqual(result["pair_delta"], "increased_high_freq")

    def test_unsupported_phase(self):
        result = classify_pair_delta(
            "neutral-stick",
            "neutral-stick",
            "unsupported",
            "pass",
            metrics(),
            metrics(),
        )
        self.assertEqual(result["pair_delta"], "unsupported")

    def test_protocol_mismatch(self):
        result = classify_pair_delta(
            "neutral-stick",
            "guided-movement",
            "pass",
            "pass",
            metrics(),
            metrics(),
        )
        self.assertEqual(result["pair_delta"], "unsupported")


if __name__ == "__main__":
    unittest.main()
