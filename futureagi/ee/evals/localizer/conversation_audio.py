"""Use simulation UI transcript boundaries for native-audio localization."""

import base64
import math
import uuid
from io import BytesIO

from tfc.utils.storage import audio_bytes_from_url_or_base64, upload_audio_to_s3


def simulation_audio_snapshot(call_execution, eval_config, input_types):
    """Snapshot exactly the drawer's aligned roles and recording-relative times.

    Only known recording mappings share this transcript's clock. Arbitrary
    audio variables must never inherit unrelated conversation boundaries.
    """
    if "audio" not in input_types.values():
        return None
    from simulate.serializers.test_execution import CallExecutionDetailSerializer
    from simulate.utils.speaker_roles import SpeakerRoleResolver

    rows = CallExecutionDetailSerializer().get_transcript(call_execution)
    # Eval mappings use raw provider channels; the UI swaps their URLs on read.
    provider = SpeakerRoleResolver.detect_provider(call_execution.provider_call_data)
    outbound = SpeakerRoleResolver.detect_is_outbound(call_execution)
    assistant_channel_role = (
        "assistant"
        if SpeakerRoleResolver.is_tested_agent(
            "assistant", provider=provider, is_outbound=outbound
        )
        else "user"
    )
    result = {}
    for key, modality in input_types.items():
        if modality != "audio":
            continue
        source = (eval_config.mapping or {}).get(key, "")
        source = source.removeprefix("call.") if isinstance(source, str) else ""
        roles = {
            "voice_recording": {"assistant", "user"},
            "stereo_recording": {"assistant", "user"},
            "assistant_recording": {assistant_channel_role},
            "customer_recording": {
                "user" if assistant_channel_role == "assistant" else "assistant"
            },
        }.get(source, set())
        result[key] = [
            {
                "utterance_id": str(row["id"]),
                "speaker_role": row["speaker_role"],
                "content": row["content"],
                "start_time": row["start_time_seconds"],
                "end_time": row["end_time_seconds"],
            }
            for row in rows
            if row.get("speaker_role") in roles
        ]
    return result


def create_utterance_segments(audio_input, turns):
    """Cut at stored boundaries; never guess missing times or speaker roles."""
    from pydub import AudioSegment

    if not turns:
        raise ValueError("No conversation utterances available")
    raw = (
        audio_input
        if isinstance(audio_input, bytes)
        else audio_bytes_from_url_or_base64(audio_input)
    )
    audio = AudioSegment.from_file(BytesIO(raw))
    segments = {}
    for index, turn in enumerate(turns, 1):
        start, end = turn.get("start_time"), turn.get("end_time")
        if (
            not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end
            or round(end * 1000) > len(audio)
            or turn.get("speaker_role") not in {"assistant", "user"}
        ):
            # Preserve the source numbering; invalid turns cannot be cited.
            continue
        buf = BytesIO()
        audio[round(start * 1000) : round(end * 1000)].export(buf, format="mp3")
        b64 = base64.b64encode(buf.getvalue()).decode()
        segments[f"segment_{index}"] = {
            **turn,
            "eligible_for_findings": turn["speaker_role"] == "assistant",
            "duration": end - start,
            "audio_bytes": b64,
            "url": upload_audio_to_s3(b64, object_key=f"tempaudio/{uuid.uuid4()}"),
        }
    if not any(unit["eligible_for_findings"] for unit in segments.values()):
        raise ValueError("No timed tested-agent utterances available")
    return segments, [], None
