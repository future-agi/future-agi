from agentic_eval.core.utils.llm_payloads import _IMAGE_EXT_PAT


def test_video_urls_are_not_fast_pathed_as_images():
    assert _IMAGE_EXT_PAT.search("https://cdn.example.com/clip.mp4") is None
    assert _IMAGE_EXT_PAT.search("https://cdn.example.com/photo.webp")
