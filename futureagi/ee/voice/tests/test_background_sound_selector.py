import json
from pathlib import Path

from ee.voice.services.background_sound_selector import _extract_bg_id

_CATALOGUE = Path(__file__).resolve().parents[3] / "simulate" / "data" / "background_sounds.json"


def test_every_catalogue_id_round_trips_through_the_parser():
    for entry in json.loads(_CATALOGUE.read_text(encoding="utf-8")):
        assert _extract_bg_id(f"SELECTED: {entry['id']}") == entry["id"]
        assert _extract_bg_id(f"I would pick {entry['id']}, it fits.") == entry["id"]
