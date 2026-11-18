import { afterEach, describe, expect, it } from "vitest";
import axios from "src/utils/axios";
import { runHarnessEnvironment } from "../harnessEnvironments";

const REFUSAL = {
  status: false,
  code: "invalid_seed",
  attr: "seed",
  message: "seed: A seed is a whole number from 0 to 2^63 - 1.",
  details: { seed: ["A seed is a whole number from 0 to 2^63 - 1."] },
};

describe("a run's seed", () => {
  const original = axios.defaults.adapter;
  afterEach(() => {
    axios.defaults.adapter = original;
  });

  it("goes to the server as typed however long it is, and comes back with the server's refusal", async () => {
    const sent = [];
    axios.defaults.adapter = async (config) => {
      sent.push(JSON.parse(config.data));
      const error = new Error("Request failed with status code 400");
      error.config = config;
      error.response = { status: 400, data: REFUSAL, headers: {}, config };
      throw error;
    };

    const refused = await runHarnessEnvironment(
      "11111111-1111-4111-8111-111111111111",
      ["k1"],
      1,
      "idem-1",
      {},
      "12345678901234567890123",
    ).catch((error) => error);

    expect(sent).toEqual([
      expect.objectContaining({ seed: "12345678901234567890123" }),
    ]);
    expect(refused.attr ?? refused.response?.data?.attr).toBe("seed");
  });
});
