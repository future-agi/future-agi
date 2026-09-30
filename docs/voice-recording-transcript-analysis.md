# Voice recording colours and transcript synchronization

Date: 2026-09-30

## Summary

The investigation identified two separate issues in the voice call detail view:

| Issue | Finding | Can frontend alone fix it? |
| --- | --- | --- |
| Waveform speaker colours | Customer audio is displayed on the white Assistant track because the frontend reverses the channels. | Yes. Preserve the fixed channel order of hosted harness recordings. |
| Transcript timing | The transcript uses call start as its time origin because the original artifact lacks recording-start offset information. | An accurate correction requires recording-start information from the runner/backend. |

The expected palette is **Customer = orange** and **Assistant = white** in the dark theme shown in the screenshots.

This document records analysis and proposed work. No application code, API behaviour, or stored call data was changed during this investigation. Earlier changes to outcomes and sub-goal display are separate from this analysis.

## Calls investigated

- `070ac2ad-ee35-4f21-8c53-421773f959e1`: the screenshot shows a white waveform at the playback cursor while an orange Customer transcript turn is highlighted. The Customer turn starts at approximately `0:23` in the transcript.
- `b63ede8f-658b-486b-aa05-a9d33112f795`: the earlier screenshot showed a similar mismatch around the Customer turn at `2:42`.

Both calls have provider `phone` on the agent definition. Their stored call direction and provider payload are missing. The detailed channel comparison below was performed on `070ac2ad-ee35-4f21-8c53-421773f959e1`.

## 1. Confirmed waveform channel reversal

### Evidence from the recordings

The stereo recording was compared directly with the separately labelled mono artifacts:

- The Customer mono samples exactly match stereo channel 0 (left), allowing for the stereo file's trailing zero padding.
- The Assistant mono samples exactly match stereo channel 1 (right).

The recordings therefore have the intended fixed order: **left = Customer, right = Assistant**. The problem is in how the UI assigns those channels to speaker tracks.

### How the reversal happens

1. `CallExecutionDetailSerializer.get_provider()` falls back to the agent definition when no provider payload is available. For this call, it returns `phone`.
2. `get_call_type()` defaults missing direction metadata to `Inbound`.
3. `AudioPlayerCustom` derives `isInbound = true` from that response.
4. `useStereoChannels` applies:

   ```js
   const shouldFlip = isInbound && !isLiveKitProvider(provider);
   const [aData, cData] = shouldFlip
     ? [leftData, rightData]
     : [rightData, leftData];
   ```

5. `isLiveKitProvider` recognizes `livekit` and `livekit_bridge`, but not `phone`. The condition is therefore true, assigning Customer audio to the Assistant waveform and Assistant audio to the Customer waveform.

The serializer methods were also evaluated with the observed metadata shape, reproducing `Inbound`, `phone`, and the resulting channel flip.

The transcript role resolver has a different fallback: it treats an unknown direction as outbound and preserves the stored roles for these calls. This explains why the transcript can retain the correct speaker labels while the waveform is reversed.

### Proposed frontend fix

Recognize recordings produced by the hosted harness and preserve their fixed channel order, independently of the target provider and call direction:

- Channel 0 / left → Customer → orange.
- Channel 1 / right → Assistant → white.

Use recording provenance, such as the existing hosted artifact metadata where available, to select this behaviour. Confirm that the drawer receives that marker before implementing the condition. Preserve direction-based mapping for legacy recording formats that require it.

Do not globally swap the colours, disable every inbound flip, or treat all `phone` recordings as having the same layout. Those changes could break other recording sources. Also avoid changing the provider to `livekit` merely to bypass the frontend condition; the target provider and recording format describe different things.

If recording provenance is unavailable in a particular response, an explicit channel-layout field would be a stronger contract, but adding one would require an API change.

## 2. Transcript timestamps use call start instead of recording start

### Confirmed evidence

The original transcript artifact for `070ac2ad-ee35-4f21-8c53-421773f959e1` contains `messages`, `schema_version`, and `transcript`, but no `recording_offset_ms`.

Without that offset, hosted transcript ingestion falls back to the call's `started_at`. The first four stored transcript timestamps exactly match this calculation:

```text
stored_start_ms = (message.started_speaking_at - call.started_at) * 1000
```

For example, the Customer turn is stored at `23.336` seconds using that fallback. Comparison with the recording's speaker activity indicates that the transcript is approximately **5–6 seconds later** than corresponding audio activity. The earlier call showed a similar approximate offset.

The missing field and fallback calculation are confirmed. The numerical alignment estimate comes from channel activity correlation; it is not a word-level audio alignment or an exact correction value.

### Correct timing model

Transcript turns and audio playback need the same time origin: the start of the recording.

The runner's `recording_offset_ms` means the time from recording start to the first transcript speech timestamp. Ingestion can use it as follows:

```text
recording_start = first_speech_timestamp - recording_offset_ms / 1000
turn_start_ms = (turn_speech_timestamp - recording_start) * 1000
```

This preserves any initial recording silence and accounts for the recording starting after the call begins.

### Proposed timing fix

The local runner source already records this offset and includes it in transcript uploads. The backend ingestion code already has a branch that consumes it. However, the investigated artifacts do not contain it; local source availability does not prove that the executed runner used that implementation.

For new calls:

1. Verify the runner build actually used by the hosted execution includes the offset-emission implementation.
2. Verify that a newly produced transcript artifact includes a numeric `recording_offset_ms`.
3. Verify ingestion stores recording-relative timestamps and that the detail response does not apply the offset a second time.

For existing calls:

- Recover the original recorder start time from retained execution evidence, if available, and use it to recalculate the transcript timing through an approved repair.
- If reliable timing evidence is unavailable, rerun the call to obtain a properly anchored recording and transcript.
- Do not apply a universal six-second adjustment. Recording startup delay can vary between calls.

Frontend-only code cannot recover a missing recording-start timestamp accurately from these fields. An estimated visual shift would not constitute a reliable synchronization fix.

## 3. Secondary highlighting behaviour

`TranscriptView` selects the latest turn whose start time has been reached. It does not require playback to remain before that turn's end, so the previous turn stays highlighted during silence. Speaker filtering can also leave an older visible turn selected.

This behaviour can make a highlight appear to identify a currently speaking person when that turn has already ended. It is separate from the confirmed channel reversal and missing timing anchor.

If the intended meaning is “currently speaking,” use the active turn's start/end interval and display no active speaker during silence. If keeping the previous turn selected helps navigation, distinguish selection from active speech. This is an optional frontend behaviour change, not required to correct the channel mapping.

## Validation before shipping a fix

- Reproduce the hosted `phone` call with missing direction metadata and verify Customer audio remains orange and Assistant audio remains white.
- Cover hosted recordings with inbound and outbound directions; their fixed channel order should remain the same.
- Cover legacy provider recordings that require an inbound flip.
- Verify both stereo splitting and separately labelled mono fallback paths.
- Use a recording that starts later than the call and includes initial silence; transcript timestamps and seeking must match recording time.
- Check a new runner artifact through ingestion and the detail response to catch dropped or double-applied offsets.
- Confirm the intended highlighting behaviour at turn boundaries, in silence, and under speaker filtering.

The current audio-player tests mock the stereo-splitting hook, so they do not establish that the real channel-mapping decision is correct. Add coverage that exercises that decision. Backend ingestion already has recording-offset test cases; end-to-end verification must also establish that the executed runner actually emits the field.

## Code references

### Application repository

- [Call detail serializer: provider, direction, recordings, transcript](../futureagi/simulate/serializers/test_execution.py)
- [Backend speaker-role resolution](../futureagi/simulate/utils/speaker_roles.py)
- [Hosted transcript ingestion and timing fallback](../futureagi/simulate/services/hosted_harness_ingestion.py)
- [Hosted transcript timing tests](../futureagi/simulate/tests/test_hosted_harness_channels.py)
- [Stereo channel splitting and mapping](../frontend/src/hooks/use-stereo-channels.js)
- [Audio track selection and colours](../frontend/src/sections/test-detail/TestDetailDrawer/AudioPlayerCustom.jsx)
- [Audio-player tests](../frontend/src/sections/test-detail/TestDetailDrawer/__tests__/AudioPlayerCustom.test.jsx)
- [LiveKit provider detection](../frontend/src/sections/agents/constants.js)
- [Playback time bridge](../frontend/src/components/VoiceDetailDrawerV2/VoiceAudioBridge.jsx)
- [Transcript highlighting](../frontend/src/components/VoiceDetailDrawerV2/TranscriptView.jsx)
- [Transcript time normalization](../frontend/src/components/VoiceDetailDrawerV2/transcriptUtils.js)

### Runner repository

The sibling `agent-learning-kit` checkout was inspected read-only:

- `src/fi/simulate/recording/room_recorder.py`: recording start time and stereo mixing.
- `src/fi/simulate/simulation/engines/livekit.py`, `_attach_recordings`: Customer/Assistant channel order and offset calculation.
- `src/fi/alk/harness/call_runner.py`: inclusion of `recording_offset_ms` in the uploaded transcript artifact.

Local commit `e388eaba` contains the transcript offset-emission change. The version used by the investigated executions was not established during this analysis.
