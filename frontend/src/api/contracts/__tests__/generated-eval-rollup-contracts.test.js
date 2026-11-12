import { describe, expect, it } from "vitest";

import {
  TracerTraceReadResponse,
  TracerTraceVoiceCallDetailResponse,
} from "src/generated/api-contracts/api.zod";

import swagger from "../../../../../api_contracts/openapi/swagger.json";

const tracePayload = (rollup) => ({
  status: true,
  result: {
    trace: {},
    observation_spans: [
      { observation_span: {}, eval_scores: [], eval_rollup: rollup },
    ],
    summary: {},
    graph: {},
  },
});

const voicePayload = (rollup) => ({
  status: true,
  result: {
    id: "call-1",
    trace_id: "trace-1",
    project_id: "project-1",
    provider_call_id: null,
    status: null,
    recording: {},
    recording_available: false,
    call_metadata: {},
    observation_span: [],
    eval_outputs: {},
    eval_rollup: rollup,
    turn_count: null,
    talk_ratio: null,
    agent_talk_percentage: null,
    bot_talk_pct: null,
    user_talk_pct: null,
    avg_agent_latency_ms: null,
    user_wpm: null,
    bot_wpm: null,
    user_interruption_count: null,
    ai_interruption_count: null,
  },
});

const evalEntry = {
  eval_config_id: "eval-1",
  eval_name: "Quality",
  output_type: null,
  template_type: null,
  target_type: null,
  choices_map: null,
  aggregate: { passed: 1, failed: 0 },
  spans: [
    {
      span_id: "span-1",
      span_name: "",
      value: null,
      explanation: null,
      error: false,
      status: null,
      eval_config_id: "eval-1",
    },
  ],
};

const makeRollup = (entry = evalEntry) => ({
  scope: "trace",
  evals: [entry],
  error: false,
});

const endpoints = [
  {
    name: "trace",
    schema: TracerTraceReadResponse,
    payload: tracePayload,
    rollup: (parsed) => parsed.result.observation_spans[0].eval_rollup,
  },
  {
    name: "voice",
    schema: TracerTraceVoiceCallDetailResponse,
    payload: voicePayload,
    rollup: (parsed) => parsed.result.eval_rollup,
  },
];

const jsonValues = [
  { label: "string", value: "passed" },
  { label: "number", value: 0.75 },
  { label: "boolean", value: false },
  { label: "null", value: null },
  { label: "array", value: ["choice", 0, false, null, { nested: [1] }] },
  { label: "object", value: { nested: ["text", 0, false, null] } },
];

describe.each(endpoints)("generated $name eval rollup contract", (endpoint) => {
  it.each(jsonValues)(
    "preserves $label JSON values and nullable metadata",
    ({ value }) => {
      const rollup = makeRollup({
        ...evalEntry,
        choices_map: value,
        aggregate: value,
        spans: [{ ...evalEntry.spans[0], value }],
      });
      const parsed = endpoint.schema.parse(endpoint.payload(rollup));
      expect(endpoint.rollup(parsed)).toEqual(rollup);
    },
  );

  it("preserves omitted optional metadata", () => {
    const rollup = {
      scope: "span",
      evals: [
        {
          eval_config_id: "eval-1",
          eval_name: "Quality",
          aggregate: {},
          spans: [{ span_id: "span-1" }],
        },
      ],
    };
    expect(
      endpoint.rollup(endpoint.schema.parse(endpoint.payload(rollup))),
    ).toEqual(rollup);
  });

  it.each(["span", "trace"])(
    "preserves %s target metadata and populated scalars",
    (targetType) => {
      const rollup = makeRollup({
        ...evalEntry,
        output_type: "PASS_FAIL",
        template_type: "llm",
        target_type: targetType,
        spans: [
          { ...evalEntry.spans[0], explanation: "Passed", status: "completed" },
        ],
      });
      expect(
        endpoint.rollup(endpoint.schema.parse(endpoint.payload(rollup))),
      ).toEqual(rollup);
    },
  );

  it("keeps legacy responses without an eval rollup valid", () => {
    const parsed = endpoint.schema.parse(endpoint.payload(undefined));
    expect(endpoint.rollup(parsed)).toBeUndefined();
  });

  it.each([
    { label: "empty object", rollup: {} },
    { label: "invalid scope", rollup: { scope: "session", evals: [] } },
    {
      label: "invalid target",
      rollup: makeRollup({ ...evalEntry, target_type: "session" }),
    },
    {
      label: "missing span identity",
      rollup: makeRollup({ ...evalEntry, spans: [{ value: 1 }] }),
    },
    {
      label: "non-JSON aggregate",
      rollup: makeRollup({ ...evalEntry, aggregate: { invalid: undefined } }),
    },
    {
      label: "non-JSON span value",
      rollup: makeRollup({
        ...evalEntry,
        spans: [{ ...evalEntry.spans[0], value: () => 1 }],
      }),
    },
  ])("rejects $label", ({ rollup }) => {
    expect(endpoint.schema.safeParse(endpoint.payload(rollup)).success).toBe(
      false,
    );
  });

  it("requires the aggregate even though its JSON value can be null", () => {
    const { aggregate: _aggregate, ...withoutAggregate } = evalEntry;
    expect(
      endpoint.schema.safeParse(endpoint.payload(makeRollup(withoutAggregate)))
        .success,
    ).toBe(false);
  });
});

describe("shared eval rollup schema nullability", () => {
  it("allows a null voice rollup without allowing a null trace rollup", () => {
    expect(
      TracerTraceVoiceCallDetailResponse.parse(voicePayload(null)).result
        .eval_rollup,
    ).toBeNull();
    expect(TracerTraceReadResponse.safeParse(tracePayload(null)).success).toBe(
      false,
    );
  });

  it("inlines only the nullable voice wrapper while sharing its typed eval entries", () => {
    const definitions = swagger.definitions;
    const shared = definitions.EvalRollup;
    const voice = definitions.TraceVoiceCallDetailResult.properties.eval_rollup;
    expect(shared["x-nullable"]).not.toBe(true);
    expect(voice["x-nullable"]).toBe(true);
    expect(voice.$ref).toBeUndefined();
    expect(voice.properties).toEqual(shared.properties);
    expect(voice.required).toEqual(shared.required);
    expect(voice.properties.evals.items.$ref).toBe(
      "#/definitions/EvalRollupEval",
    );
    expect(definitions.TraceDetailSpan.properties.eval_rollup).toEqual({
      $ref: "#/definitions/EvalRollup",
    });
  });
});
