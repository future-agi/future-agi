import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { describe, expect, it } from "vitest";

import { AgentPlaygroundGraphsReadResponse } from "src/generated/api-contracts/api.zod";

// Real graph-retrieve response captured by the backend parity tests
// (futureagi/agent_playground/tests/views/test_th8216b_graph_write_contract_parity.py).
// Captures with a populated active_version also carry nested nulls
// (commit_message, ref_graph_id, ...) that Orval types as non-null; that
// pre-existing x-nullable gap is tracked separately (TH-8217 pre-carry).
const capturedBody = (name) =>
  JSON.parse(
    fs.readFileSync(
      path.resolve(
        process.cwd(),
        "../futureagi/agent_playground/tests/fixtures/contracts/th8216b/captured",
        name,
      ),
      "utf8",
    ),
  ).body;

describe("generated agent-playground graph contracts", () => {
  it("accepts the captured graph retrieve response with a null active_version", () => {
    const body = capturedBody("graph_retrieve_own_without_versions_200.json");
    expect(body.result.active_version).toBeNull();
    expect(
      AgentPlaygroundGraphsReadResponse.parse(body).result.active_version,
    ).toBeNull();
  });

  it("still rejects an active_version that is not an object or null", () => {
    const body = capturedBody("graph_retrieve_own_without_versions_200.json");
    const broken = { ...body, result: { ...body.result, active_version: "1" } };
    expect(AgentPlaygroundGraphsReadResponse.safeParse(broken).success).toBe(
      false,
    );
  });
});
