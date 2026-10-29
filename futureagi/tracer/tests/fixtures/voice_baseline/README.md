# Voice call baseline: one Vapi call

## What this is

One Vapi call run through today's provider-poll code, in-process. Recorded at commit `ea12cc3f4`; unchanged after the merge of the feature branch with the hidden projects, sandbox keys and collector routing (`3c89355b6`). `test_voice_call_baseline.py` regenerates each file below and fails, naming every differing key, when today's code no longer produces it.

- `vapi_call.json`: the input, one Vapi call object. Every value is invented.
- `vapi_top_span.json`: the one dict `process_and_store_logs` hands to the collector export. This is the top span as stored; the collector writes it to ClickHouse and the Python side never touches it again.
- `vapi_detail.json`: the `result` the call detail returns for that span's ClickHouse row: the real `read_trace_detail` answers its statements, and `_voice_call_detail_clickhouse` assembles the response. The test validates the whole response envelope with the serializer the endpoint's strict response check uses.
- `vapi_list_row.json`: the row the voice list (`_list_voice_calls_clickhouse`) assembles for the same row. The real function runs; the test replaces its page read and answers its content statement.

## Re-recording

From `futureagi/`: `VOICE_BASELINE_RECORD=1 DJANGO_SETTINGS_MODULE=tfc.settings.test TEST=1 DEBUG=1 .venv/bin/python -m pytest --import-mode=importlib -p no:cacheprovider tracer/tests/test_voice_call_baseline.py -q`. This recording run rewrites the three output files and then fails each comparison test by design, so a shell that keeps the variable set can never pass. Then run the same command without the variable: that compare run must pass.

## What it covers and what it does not

- Vapi only. There is no Retell baseline here.
- The normaliser's two network side effects are stubbed. The recording rehost gets each URL back unchanged, so nothing is uploaded or billed and the provider URLs stay. The call-log fetch returns nothing, so the span has no `call_logs`. No agent key is resolved.
- Production gives each transcript entry a fresh uuid4. Here the ids come from a counter (`00000000-0000-0000-0000-000000000001`, ...) so the files stay stable.
- The export runs as in production up to the wire: only the gRPC exporter and the organisation's system-key lookup (`resolve_system_emit_credentials`, a Postgres read) are replaced. The test builds the ClickHouse row from the collector's rules, starting from the OTLP span that exporter received: `adapter.go` `Split` and `DeriveHotKeys`, and `converter.go` `spanToRow`. The collector never wrote this row. Its column names are not typed by hand: the test asserts them against the statements the readers issue (the detail's identity and content statements, and the voice list's content statement).
- Of the readers' statements the test holds the column names and the span identities bound into them, never a statement's expressions or filters: an expression that projects a different value under the same name, or a filter on a different column, passes here. Only a run against ClickHouse holds those. The row leaves `model`, the token counts and `cost` empty, skips the collector's token pricing and its wall-clock `_version`, and leaves out the Go overflow of integers beyond 2^53 and non-finite doubles; neither reader uses those columns for a root span, and no exported attribute reaches that overflow.
- The detail's simulator-context lookup is stubbed to find nothing. Without a simulator id it would still query Postgres by the provider call id.
- The voice list's bounded page read is replaced whole and hands back one hand-built page row; only its content statement is driven, and it gets the identity and content columns that statement projects. Eval and annotation reads come back empty.
- The drawer is not rendered. The table below comes from reading its sources.

## Facts the files show

- The span's `start_time` is the call's `createdAt`, not its `startedAt`.
- The row's `provider` column is `openai`, because `gen_ai.provider.name` outranks `gen_ai.system`. The detail shows that value as `observation_span[0].provider`; both readers still process the call as Vapi.
- The detail names the root `conversation` rather than using the span name. Its `call_metadata` is `{}`.
- The drawer reads `span_attributes.rawLog` and `.callLogs` in camelCase. The endpoint returns `raw_log` as a JSON string, and no client middleware renames keys, so those reads find nothing and the drawer falls back to `call_summary`.

## Drawer fields

`VoiceDetailDrawerV2` does not get the detail response as is; it gets a merge:

- From the voice list (`frontend/src/sections/test-detail/TestDetailDrawer/TestDetailSideDrawer.jsx:137-160`): `{...listRow, module, origin, project_id, ...voiceDetail, transcript: merged}`. `module` and `origin` come from the URL's row state, which the voice list sets to `"project"`; `project_id` comes from the URL's observe id, else the list row. Until the detail arrives the drawer shows the list row (`vapi_list_row.json`) with those three. Then every key the detail (`vapi_detail.json`) carries replaces the list value, a `null` included: `response_time_ms` is 700 in the list row and null in the detail (no drawer field reads it). The two transcripts are merged entry by entry.
- From the full page (`frontend/src/pages/dashboard/observe/VoiceFullPage.jsx:50-58`): `{...voiceDetail, id, trace_id, project_id, module: "project"}`. `project_id` is the URL's observe id, `id` and `trace_id` fall back to the URL's call id, and there is no list row and no `origin`.

Paths below are under `frontend/src/components/VoiceDetailDrawerV2/`. In the "from" column, "list, then detail" means the list row's value until the detail arrives and the detail's after; here the two are equal unless the value says otherwise. "absent" means absent from the detail, the list row and the URL alike. `AudioPlayerCustom` (outside the folder) takes the whole `data` object to play the recording, so its reads are not listed.

| field read | where | baseline value | from |
|---|---|---|---|
| `data.call_type` | CallDetailsBar.jsx:188, VoiceDetailDrawerV2.jsx:425 | `"outbound"` | list, then detail |
| `data.status` | CallDetailsBar.jsx:191, VoiceDetailDrawerV2.jsx:419 … | `"completed"` | list, then detail |
| `data.duration_seconds` | CallDetailsBar.jsx:199 | `22` | list, then detail |
| `data.avg_agent_latency_ms` | CallDetailsBar.jsx:203, VoiceRightPanel.jsx:163 | `700` | list, then detail |
| `data.phone_number` | CallDetailsBar.jsx:207, CallDetailsBar.jsx:208 … | `"+15550100002"` | list, then detail |
| `data.created_at` | CallDetailsBar.jsx:217 | `"2026-09-01T10:00:00.000Z"` | list, then detail |
| `data.ended_reason` | CallDetailsBar.jsx:220, CallDetailsBar.jsx:223 … | `"customer-ended-call"` | list, then detail |
| `data.module` | CallDetailsBar.jsx:237, shareResource.js:7 | `"project"`, so the drawer also fetches the trace detail (CallDetailsBar.jsx:238) | URL; set by the full page |
| `data.origin` | shareResource.js:7 | `"project"`; absent on the full page | URL |
| `data.trace_id` | CallDetailsBar.jsx:231, CallDetailsBar.jsx:250 … | `"5689f6d7-caaf-5e64-8dc9-6f526ccca69b"` | list, then detail |
| `data.project_id` | CallDetailsBar.jsx:239, VoiceDetailDrawerV2.jsx:80 … | `"0b5e11e0-0000-4000-8000-0000000000b1"` | URL, then detail; URL on the full page |
| `data.transcript` | PathAnalysisView.jsx:262, VoiceDetailDrawerV2.jsx:412 … | list of 7 | detail (the list row has none) |
| `data.id` | VoiceDetailDrawerV2.jsx:224, VoiceDetailDrawerV2.jsx:334 … | `"5689f6d7-caaf-5e64-8dc9-6f526ccca69b"` | list, then detail |
| `data.provider_call_id` | VoiceDetailDrawerV2.jsx:334 | `"0b5e11e0-0000-4000-8000-00000000ca11"` | detail |
| `data.observation_span` | VoiceDetailDrawerV2.jsx:410, VoiceRightPanel.jsx:64 … | list of 1 | list (a stub root), then detail |
| `data.call_summary` | VoiceDetailDrawerV2.jsx:417, VoiceRightPanel.jsx:175 … | `"The caller booked a Tuesday morning appointment at the Example Clinic."` | list, then detail |
| `data.customer_name` | VoiceDetailDrawerV2.jsx:564 | `"+15550100002"` | list, then detail |
| `data.messages` | VoiceRightPanel.jsx:83 | list of 8 | detail |
| `data.turn_count` | VoiceRightPanel.jsx:160 | `7` | list, then detail |
| `data.talk_ratio` | VoiceRightPanel.jsx:161 | `1.3548387096774193` | list, then detail |
| `data.agent_talk_percentage` | VoiceRightPanel.jsx:162 | `57.53` | list, then detail |
| `data.user_wpm` | VoiceRightPanel.jsx:164 | `184` | list, then detail |
| `data.bot_wpm` | VoiceRightPanel.jsx:165 | `286` | list, then detail |
| `data.user_interruption_count` | VoiceRightPanel.jsx:166 | `0` | list, then detail |
| `data.ai_interruption_count` | VoiceRightPanel.jsx:167 | `0` | list, then detail |
| `data.eval_outputs` | VoiceRightPanel.jsx:204, VoiceRightPanel.jsx:206 | `{}` | detail |
| observation_span[i].parent_span_id | VoiceRightPanel.jsx:67 | `null` in the list row's stub, then absent | list, then detail |
| observation_span[i].observation_type | VoiceRightPanel.jsx:67 | `"conversation"` | list, then detail |
| root `.id` | VoiceRightPanel.jsx:314 | `"eadf51448abc5d54"` | list, then detail |
| root `.span_attributes` | VoiceRightPanel.jsx:331 | object, 89 keys | detail (absent from the stub) |
| root `.evals_metrics` | VoiceRightPanel.jsx:206 | absent | |
| root `.span_attributes.rawLog` (`.id`, `.summary`, `.artifact.performanceMetrics`, `.costBreakdown`) | VoiceRightPanel.jsx:94, 181-196 | absent | |
| root `.span_attributes.callLogs` | VoiceRightPanel.jsx:95 | absent | |
| `data.duration` (199), `data.avg_agent_latency` (203), `data.provider` (211), `data.call_metadata.provider` (211; the detail's `call_metadata` is `{}`), `data.cost` (214), `data.total_cost` (214), `data.timestamp` (217), `data.tags` (244), `data.trace.tags` (245) | CallDetailsBar.jsx | absent | |
| `data.scenario_graph` (212) | PathAnalysisView.jsx | absent | |
| `data.scenario_columns` (183), `data.persona_details` (215), `data.scenario` (218) | ScenarioView.jsx | absent | |
| `data.observation_spans` (410), `data.summary` (411), `data.recordings` (416), `data.customer_latency_metrics` (421), `data.customer_cost_breakdown` (422), `data.eval_metrics` (423) | VoiceDetailDrawerV2.jsx | absent | |
| `data.simulation_call_type` (49), `data.call_execution_id` (58), `data.scenario_graph.nodes` (63), `data.scenario_id` (190), `data.test_execution_id` (192) | VoiceLeftPanel.jsx | absent | |
| `data.session_id` (74), `data.avg_stop_time_after_interruption` (168), `data.customer_latency_metrics.system_metrics` (174), `data.attributes` (332), `data.trace_details.attributes` (333) | VoiceRightPanel.jsx | absent | |
