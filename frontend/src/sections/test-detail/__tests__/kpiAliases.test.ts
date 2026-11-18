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
