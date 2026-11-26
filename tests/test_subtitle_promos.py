"""Ads and release credits come out of subtitle tracks; the programme stays."""

import unittest

from backend.agents import subtitle_promos


def cues(*texts):
    return [{"start": 10.0 * i, "end": 10.0 * i + 2, "text": t} for i, t in enumerate(texts)]


def caller(answer):
    seen = {}

    async def call(system, prompt, schema):
        seen["prompt"] = prompt
        return answer, {"input_tokens": 100, "output_tokens": 5}

    call.model = "glm-5.3-flash"
    call.seen = seen
    return call


class PromotionsTest(unittest.IsolatedAsyncioTestCase):
    def test_a_track_with_nothing_promotional_costs_no_call(self):
        self.assertEqual(subtitle_promos.shortlist(cues("Hello.", "Where were you?", "Home.")), [])

    def test_the_shortlist_is_suspects_plus_the_first_and_last_captions(self):
        track = cues("First line.", "Second.", "Middle.", "ReEncoded By Dr.XJ | PSA\nwww.PSArips.com",
                     "More dialogue.", "Penultimate.", "Last line.")
        self.assertEqual(subtitle_promos.shortlist(track), [0, 1, 3, 5, 6])

    async def test_only_shown_captions_can_be_removed(self):
        track = cues("Hi.", "There.", "Sync and corrections by someone", "Dialogue.", "More.", "Bye.")
        # The model names caption 3 (shown) and 4 (never shown): only 3 goes.
        call = caller({"remove": [3, 4]})
        remove, spend = await subtitle_promos.find(call, track)
        self.assertEqual(remove, [2])
        self.assertNotIn("Dialogue.", call.seen["prompt"])
        self.assertEqual(spend["calls"], 1)
        self.assertEqual(spend["phase"], "subtitle_promotions")
        self.assertGreater(spend["dollars"], 0)

    async def test_no_suspects_means_no_call(self):
        call = caller({"remove": [1]})
        remove, spend = await subtitle_promos.find(call, cues("Hi.", "Bye."))
        self.assertEqual((remove, spend), ([], None))
        self.assertNotIn("prompt", call.seen)


if __name__ == "__main__":
    unittest.main()
