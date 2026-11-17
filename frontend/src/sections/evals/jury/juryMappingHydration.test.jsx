import React, { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import useJuryDatasetInputs from "./useJuryDatasetInputs";

vi.mock("src/launch/launchSwitch", () => ({ isSurfaceShown: () => true }));
vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const saved = {
  output: "output-column",
  steps: "evidence-steps-column",
  checks: "evidence-checks-column",
};
const checksOnly = { seats: [{ kind: "check" }] };
const stepAndCheck = {
  seats: [{ kind: "eval", level: "step" }, { kind: "check" }],
};

function setup(client, initialVersion, initialBinding) {
  const wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(
    ({ binding }) => {
      const [version, setVersion] = useState(initialVersion);
      const [mapping, setMapping] = useState(saved);
      const variables = useJuryDatasetInputs(
        "parent",
        true,
        version,
        ["output"],
        binding,
        setVersion,
        saved,
      );
      // DatasetTestMode prunes removed inputs, then auto-maps namesake columns.
      useEffect(() => {
        if (!variables.length) return;
        const keys = new Set(variables);
        setMapping((previous) => {
          const entries = Object.entries(previous).filter(([key]) =>
            keys.has(key),
          );
          return entries.length === Object.keys(previous).length
            ? previous
            : Object.fromEntries(entries);
        });
      }, [variables]);
      return { version, setVersion, mapping, variables };
    },
    { wrapper, initialProps: { binding: initialBinding } },
  );
}

const cache = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });

describe("saved jury mapping hydration with the query cache", () => {
  it.each([null, "latest"])(
    "reopens the saved version before consulting cached current data (%s)",
    async (initialVersion) => {
      const client = cache();
      client.setQueryData(
        ["jury-dataset-inputs", "parent", initialVersion],
        checksOnly,
      );
      let finish;
      axios.get.mockImplementation(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      const { result } = setup(client, initialVersion, {
        jury_version_id: "saved-v1",
      });
      await waitFor(() => expect(result.current.version).toBe("saved-v1"));
      expect(axios.get.mock.calls[0][1]).toEqual({
        params: { version_id: "saved-v1" },
      });
      expect(result.current.mapping).toEqual(saved);
      await act(async () => {
        finish({ data: { result: stepAndCheck } });
      });
      await waitFor(() => expect(result.current.variables).toContain("steps"));
      expect(result.current.mapping).toEqual(saved);
      client.clear();
    },
  );

  it("does not prune using another version's placeholder while switching", async () => {
    const client = cache();
    client.setQueryData(["jury-dataset-inputs", "parent", "old"], checksOnly);
    let finish;
    axios.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    // Prime a previous observer result without mounting the mapping form.
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, rerender } = renderHook(
      ({ version }) =>
        useJuryDatasetInputs(
          "parent",
          true,
          version,
          ["output"],
          {},
          vi.fn(),
          saved,
        ),
      { wrapper, initialProps: { version: "old" } },
    );
    expect(result.current).toEqual(["output", "checks"]);
    rerender({ version: "saved-v1" });
    expect(result.current).toEqual(["output", "steps", "checks"]);
    await act(async () => {
      finish({ data: { result: stepAndCheck } });
    });
    await waitFor(() =>
      expect(result.current).toEqual(["output", "steps", "checks"]),
    );
    client.clear();
  });

  it("allows an explicit version change and prunes only its resolved inputs", async () => {
    const client = cache();
    client.setQueryData(
      ["jury-dataset-inputs", "parent", "saved-v1"],
      stepAndCheck,
    );
    let finish;
    axios.get.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = setup(client, null, { jury_version_id: "saved-v1" });
    await waitFor(() => expect(result.current.version).toBe("saved-v1"));
    act(() => result.current.setVersion("latest"));
    expect(result.current.mapping).toEqual(saved);
    await act(async () => {
      finish({ data: { result: checksOnly } });
    });
    await waitFor(() =>
      expect(result.current.mapping).toEqual({
        output: saved.output,
        checks: saved.checks,
      }),
    );
    expect(result.current.version).toBe("latest");
    client.clear();
  });
});
