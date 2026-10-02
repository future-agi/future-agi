"""Digest attachment to the first user anchor.

Why prepend to the user anchor instead of inserting a system message
in the middle of the conversation: a mid-conversation ``role=system``
entry can be silently dropped, hoisted to a top-level ``system``
parameter, or rejected outright depending on the wire format the model
expects.

Prepending the digest as a text block at the head of the first user
message's content sidesteps every variant of that quirk and preserves
the sacred-anchor property — anchors stay at the head of the
conversation; they just carry additional context the agent has
already established.

Sentinel markers wrap the digest so re-compaction on a later iteration
can splice in the NEW digest while removing the OLD one, preventing
unbounded digest stacking across iterations.
"""

from typing import List


DIGEST_OPEN = "<<<EVAL_CTX_DIGEST_BEGIN>>>"
DIGEST_CLOSE = "<<<EVAL_CTX_DIGEST_END>>>"


def _scrub_sentinels(text: str) -> str:
    """Remove any literal sentinel markers from raw text.

    Defends against the (vanishingly rare) case where the summarizer
    LLM itself emits one of our sentinel strings inside its digest —
    if it did, future ``strip_existing_digest`` calls could splice the
    wrong region. Replace with a visible placeholder so the markers
    never appear in actual content.
    """
    if not isinstance(text, str):
        return text
    if DIGEST_OPEN in text:
        text = text.replace(DIGEST_OPEN, "<<<digest-open-marker-removed>>>")
    if DIGEST_CLOSE in text:
        text = text.replace(DIGEST_CLOSE, "<<<digest-close-marker-removed>>>")
    return text


def build_digest_block(digest: str) -> str:
    """Wrap a raw digest string with sentinel markers + a one-line header
    explaining to the agent what the block represents.

    Scrubs any literal sentinel markers out of the raw digest first so
    the markers we add are guaranteed to be the unique delimiters in
    the final string.
    """
    safe_digest = _scrub_sentinels(digest or "")
    return (
        f"{DIGEST_OPEN}\n"
        f"[PRIOR CONTEXT DIGEST — established facts; treat as ground truth]\n"
        f"{safe_digest}\n"
        f"{DIGEST_CLOSE}\n\n"
    )


def strip_existing_digest(text) -> str:
    """Remove all existing digest blocks from a string. No-op if no
    digest present. Used during re-compaction so we don't stack digests
    across iterations.

    Loops in case multiple digest blocks were ever stacked (would only
    happen if a previous strip somehow missed one). Bounded — each
    iteration removes at least one ``DIGEST_OPEN`` marker.
    """
    if not isinstance(text, str):
        return text
    while DIGEST_OPEN in text and DIGEST_CLOSE in text:
        open_idx = text.find(DIGEST_OPEN)
        close_idx = text.find(DIGEST_CLOSE, open_idx)
        if close_idx == -1:
            break
        after = close_idx + len(DIGEST_CLOSE)
        # Skip trailing whitespace right after the close marker
        while after < len(text) and text[after] in "\n\r ":
            after += 1
        text = text[:open_idx] + text[after:]
    return text


def merge_digest_into_user_message(user_msg: dict, digest_block: str) -> dict:
    """Splice a digest block into a user message's content, replacing
    any pre-existing digest in place. Handles all content shapes:

      - str            → prepend digest, strip old digest if present
      - list           → prepend as a text part (or merge into the first
                         text part), strip old digest if present
      - None / other   → coerce to string with digest prepended
    """
    if not isinstance(user_msg, dict):
        return user_msg

    content = user_msg.get("content")

    if content is None:
        return {**user_msg, "content": digest_block.rstrip()}

    if isinstance(content, str):
        return {
            **user_msg,
            "content": digest_block + strip_existing_digest(content),
        }

    if isinstance(content, list):
        new_parts = []
        digest_added = False
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and not digest_added
            ):
                stripped = strip_existing_digest(part.get("text", ""))
                new_parts.append(
                    {"type": "text", "text": digest_block + stripped},
                )
                digest_added = True
            elif isinstance(part, dict) and part.get("type") == "text":
                new_parts.append({
                    "type": "text",
                    "text": strip_existing_digest(part.get("text", "")),
                })
            else:
                # Media parts and other shapes — preserved as-is
                new_parts.append(part)
        if not digest_added:
            # No text part at all (multimodal-only) — insert digest as
            # leading text part
            new_parts.insert(
                0, {"type": "text", "text": digest_block.rstrip()},
            )
        return {**user_msg, "content": new_parts}

    # Fallback for unknown shapes
    return {**user_msg, "content": digest_block + str(content)}


def attach_digest_to_anchors(anchors: List, digest: str) -> List:
    """Return a copy of ``anchors`` with the digest spliced into the
    first user message's content. If no user anchor exists (only system
    messages present), synthesize a user message containing just the
    digest so it becomes the new first user anchor.
    """
    new_anchors = list(anchors)
    block = build_digest_block(digest)

    for i, m in enumerate(new_anchors):
        if isinstance(m, dict) and m.get("role") == "user":
            new_anchors[i] = merge_digest_into_user_message(m, block)
            return new_anchors

    # No user anchor — synthesize one carrying just the digest
    new_anchors.append({"role": "user", "content": block.rstrip()})
    return new_anchors
