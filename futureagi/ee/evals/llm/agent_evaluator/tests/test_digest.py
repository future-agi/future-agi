"""Unit tests for ``context/digest.py`` — sentinel-wrapped digest
attachment to user anchors.

Pure-function module — no I/O, no DB, no LLM calls.
"""

from ee.evals.llm.agent_evaluator.context import digest as D


# ── _scrub_sentinels() ───────────────────────────────────────────────────


def test_scrub_sentinels_none_returns_unchanged():
    assert D._scrub_sentinels(None) is None


def test_scrub_sentinels_non_string_returns_unchanged():
    obj = {"x": 1}
    assert D._scrub_sentinels(obj) is obj


def test_scrub_sentinels_empty_string():
    assert D._scrub_sentinels("") == ""


def test_scrub_sentinels_no_markers_pass_through():
    assert D._scrub_sentinels("ordinary content") == "ordinary content"


def test_scrub_sentinels_replaces_open_marker():
    s = f"Before {D.DIGEST_OPEN} after"
    out = D._scrub_sentinels(s)
    assert D.DIGEST_OPEN not in out
    assert "digest-open-marker-removed" in out


def test_scrub_sentinels_replaces_close_marker():
    s = f"Before {D.DIGEST_CLOSE} after"
    out = D._scrub_sentinels(s)
    assert D.DIGEST_CLOSE not in out
    assert "digest-close-marker-removed" in out


def test_scrub_sentinels_replaces_both_markers():
    s = f"x {D.DIGEST_OPEN} y {D.DIGEST_CLOSE} z"
    out = D._scrub_sentinels(s)
    assert D.DIGEST_OPEN not in out
    assert D.DIGEST_CLOSE not in out


# ── build_digest_block() ─────────────────────────────────────────────────


def test_build_digest_block_wraps_with_sentinels():
    block = D.build_digest_block("hello world")
    assert block.startswith(D.DIGEST_OPEN)
    assert D.DIGEST_CLOSE in block
    assert "hello world" in block
    assert "PRIOR CONTEXT DIGEST" in block


def test_build_digest_block_empty_or_none_safe():
    assert D.DIGEST_OPEN in D.build_digest_block("")
    assert D.DIGEST_OPEN in D.build_digest_block(None)


def test_build_digest_block_scrubs_embedded_markers():
    embedded = f"facts {D.DIGEST_OPEN} and {D.DIGEST_CLOSE} stuff"
    block = D.build_digest_block(embedded)
    # The only DIGEST_OPEN / DIGEST_CLOSE in the result must be the ones we added
    # (i.e. exactly one of each — the inner ones are scrubbed)
    assert block.count(D.DIGEST_OPEN) == 1
    assert block.count(D.DIGEST_CLOSE) == 1
    assert "digest-open-marker-removed" in block
    assert "digest-close-marker-removed" in block


# ── strip_existing_digest() ──────────────────────────────────────────────


def test_strip_existing_digest_non_string_passthrough():
    assert D.strip_existing_digest(None) is None
    assert D.strip_existing_digest(42) == 42


def test_strip_existing_digest_no_digest_no_change():
    s = "the quick brown fox"
    assert D.strip_existing_digest(s) == s


def test_strip_existing_digest_removes_one_block():
    block = D.build_digest_block("FACTS")
    full = block + "the actual user message"
    stripped = D.strip_existing_digest(full)
    assert D.DIGEST_OPEN not in stripped
    assert D.DIGEST_CLOSE not in stripped
    assert "the actual user message" in stripped


def test_strip_existing_digest_removes_stacked_digests():
    full = D.build_digest_block("A") + D.build_digest_block("B") + "tail"
    stripped = D.strip_existing_digest(full)
    assert D.DIGEST_OPEN not in stripped
    assert D.DIGEST_CLOSE not in stripped
    assert "tail" in stripped


def test_strip_existing_digest_malformed_no_close_breaks_safely():
    # Only DIGEST_OPEN, no DIGEST_CLOSE — loop condition requires both
    s = f"{D.DIGEST_OPEN}\norphan open\nfollowed by content"
    out = D.strip_existing_digest(s)
    assert out == s  # No change


# ── merge_digest_into_user_message() ─────────────────────────────────────


def test_merge_digest_non_dict_returns_unchanged():
    assert D.merge_digest_into_user_message("string", "block") == "string"


def test_merge_digest_into_string_content():
    user_msg = {"role": "user", "content": "original prompt"}
    block = D.build_digest_block("digest content")
    out = D.merge_digest_into_user_message(user_msg, block)
    assert out["role"] == "user"
    assert isinstance(out["content"], str)
    assert out["content"].startswith(D.DIGEST_OPEN)
    assert "original prompt" in out["content"]


def test_merge_digest_into_string_replaces_existing_digest():
    block1 = D.build_digest_block("OLD")
    user_msg = {"role": "user", "content": block1 + "the real prompt"}
    block2 = D.build_digest_block("NEW")
    out = D.merge_digest_into_user_message(user_msg, block2)
    # OLD digest should be gone, NEW one in place
    assert "NEW" in out["content"]
    assert "OLD" not in out["content"]
    # Exactly one digest block
    assert out["content"].count(D.DIGEST_OPEN) == 1


def test_merge_digest_into_list_content_with_text_part():
    user_msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "x.png"}},
        ],
    }
    block = D.build_digest_block("DIGEST")
    out = D.merge_digest_into_user_message(user_msg, block)
    assert isinstance(out["content"], list)
    # First text part should now carry the digest prepended
    first = out["content"][0]
    assert first["type"] == "text"
    assert D.DIGEST_OPEN in first["text"]
    assert "describe this" in first["text"]
    # Media block preserved verbatim
    assert out["content"][1] == {"type": "image_url", "image_url": {"url": "x.png"}}


def test_merge_digest_into_list_content_multimodal_only_inserts_text_part():
    # No text part at all (image-only message)
    user_msg = {
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": "x.png"}}],
    }
    block = D.build_digest_block("DIGEST")
    out = D.merge_digest_into_user_message(user_msg, block)
    assert isinstance(out["content"], list)
    # Now should have 2 parts: text (digest) + image
    assert len(out["content"]) == 2
    assert out["content"][0]["type"] == "text"
    assert D.DIGEST_OPEN in out["content"][0]["text"]
    assert out["content"][1]["type"] == "image_url"


def test_merge_digest_into_none_content():
    user_msg = {"role": "user", "content": None}
    block = D.build_digest_block("D")
    out = D.merge_digest_into_user_message(user_msg, block)
    assert isinstance(out["content"], str)
    assert D.DIGEST_OPEN in out["content"]


def test_merge_digest_into_unknown_content_shape():
    user_msg = {"role": "user", "content": 42}
    block = D.build_digest_block("D")
    out = D.merge_digest_into_user_message(user_msg, block)
    assert D.DIGEST_OPEN in out["content"]
    assert "42" in out["content"]


# ── attach_digest_to_anchors() ───────────────────────────────────────────


def test_attach_digest_to_anchors_with_user_anchor():
    anchors = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    out = D.attach_digest_to_anchors(anchors, "DIGEST")
    assert out[0] == {"role": "system", "content": "SYS"}
    assert "USR" in out[1]["content"]
    assert D.DIGEST_OPEN in out[1]["content"]


def test_attach_digest_to_anchors_only_system_synthesizes_user():
    anchors = [{"role": "system", "content": "SYS"}]
    out = D.attach_digest_to_anchors(anchors, "DIGEST")
    assert len(out) == 2
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert D.DIGEST_OPEN in out[1]["content"]


def test_attach_digest_to_anchors_empty_list_synthesizes_user():
    out = D.attach_digest_to_anchors([], "DIGEST")
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert D.DIGEST_OPEN in out[0]["content"]
