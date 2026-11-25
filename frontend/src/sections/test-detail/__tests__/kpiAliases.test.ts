import { readFileSync } from "node:fs";
import { join } from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import payloads from "../../../../../futureagi/simulate/tests/kpi_alias_payloads.json";
import { extractKpis } from "../common";
import { getSuffix } from "../PerformanceMetrics/common";

const directions = ["forward", "reverse"] as const;
type KpiPayload = typeof payloads.system & Record<string, unknown>;

// The authenticated KPI GET is checked against these same fixtures in
// test_kpi_metric_names.py. Use its captured payloads for a live contract run.
const payloadDir = process.env.KPI_CALLER_PAYLOAD_DIR;

describe("run-detail KPI alias integration", () => {
  it.each(directions)(
    "keeps all three choice results visible (%s projection)",
    (direction) => {
      const data: KpiPayload = payloadDir
        ? JSON.parse(
            readFileSync(join(payloadDir, `${direction}.json`), "utf8"),
          )
        : { ...payloads.system, ...payloads[direction] };
      const { deterministicEvals, evalMetrics, systemMetrics } = extractKpis(
        data,
        data.agent_type,
      );
      expect(deterministicEvals, "visible choice distributions").toHaveLength(
        3,
      );
      const [first, second, third] = Object.keys(payloads[direction]);
      expect(
        Object.fromEntries(
          deterministicEvals.map(({ id, data: bars }) => [id, bars]),
        ),
      ).toEqual({
        [first]: [
          { name: "Warm", value: 67 },
          { name: "Cold", value: 33 },
        ],
        [second]: [
          { name: "Calm", value: 50 },
          { name: "Rough", value: 50 },
        ],
        [third]: [
          { name: "Fine", value: 75 },
          { name: "Flat", value: 25 },
        ],
      });
      expect(systemMetrics).toMatchObject({
        avg_score: 85,
        avg_agent_latency: 310,
      });
      expect(data.avg_response).toBe(420);
      expect(systemMetrics).not.toHaveProperty("avg_response");
      expect(evalMetrics).not.toHaveProperty("avg_response");
      expect(evalMetrics).toMatchObject({
        "avg_score__00000000-0000-0000-0000-000000000004": 20,
        "avg_response__00000000-0000-0000-0000-000000000005": 30,
        "avg_agent_latency__00000000-0000-0000-0000-000000000006": 40,
      });
      expect(getSuffix("avg_agent_latency")).toBe("ms");
      expect(getSuffix("avg_response")).toBe("ms");
    },
  );
});

const id = (n: number) => `00000000-0000-0000-0000-00000000000${n}`;
const projectionCases = {
  projection: {
    bars: {
      tier_1: [
        { name: "Warm", value: 67 },
        { name: "Cold", value: 33 },
      ],
      [`tier1__${id(2)}`]: [
        { name: "Calm", value: 50 },
        { name: "Rough", value: 50 },
      ],
    },
    scores: {},
  },
  cross_type: {
    bars: { [`avg1__${id(2)}`]: [{ name: "Yes", value: 100 }] },
    scores: { avg_1: 50 },
  },
  mixed: {
    bars: {
      tier_1: [
        { name: "Warm", value: 67 },
        { name: "Cold", value: 33 },
      ],
      [`tier_1__${id(2)}`]: [
        { name: "Calm", value: 50 },
        { name: "Rough", value: 50 },
      ],
      [`tier1__${id(3)}`]: [
        { name: "Fine", value: 75 },
        { name: "Flat", value: 25 },
      ],
    },
    scores: { [`avg_score__${id(4)}`]: 20 },
  },
  chain: {
    bars: {
      a__1: [{ name: "One", value: 100 }],
      [`a_1__${id(2)}`]: [{ name: "Three", value: 100 }],
      [`a1__${id(3)}`]: [
        { name: "Four", value: 50 },
        { name: "Five", value: 50 },
      ],
    },
    scores: {},
  },
} as const;
type ProjectionCase = keyof typeof projectionCases;

describe("run-detail KPI projection collisions", () => {
  it.each(Object.keys(projectionCases) as ProjectionCase[])(
    "shows every evaluation result (%s)",
    (name) => {
      const expected: Record<string, unknown> = payloads.projection[name];
      const { agent_type, avg_score, avg_response, avg_agent_latency } =
        payloads.system;
      const data: Record<string, unknown> = payloadDir
        ? JSON.parse(readFileSync(join(payloadDir, `${name}.json`), "utf8"))
        : {
            agent_type,
            avg_score,
            avg_response,
            avg_agent_latency,
            ...expected,
          };
      const { deterministicEvals, evalMetrics, systemMetrics } = extractKpis(
        data,
        agent_type,
      );
      expect(
        Object.fromEntries(
          deterministicEvals.map(({ id: key, data: bars }) => [key, bars]),
        ),
      ).toEqual(projectionCases[name].bars);
      for (const key of Object.keys(projectionCases[name].bars)) {
        expect(data[key], key).toEqual(expected[key]);
      }
      expect(evalMetrics).toEqual(projectionCases[name].scores);
      expect(systemMetrics).toMatchObject({
        avg_score: 85,
        avg_agent_latency: 310,
      });
    },
  );
});
