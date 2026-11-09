// RED at 7e05e449: the source gate/loader do not exist; no query consumer
// fetches binary data, aborts stale handoffs, or offers manual fallback.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HubSourceGate from "../../deployments/new/HubSourceGate";
import { loadHubDeploymentSource } from "../api/deploymentSource";
import axios from "src/utils/axios";
import { fixture, PIN } from "./hubBundleFixtures";

vi.mock("src/utils/axios", () => ({ default: { get: vi.fn() } }));
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
const ready = {
  source: "hub",
  manifest: { name: "first", entries: [{ id: "main" }] },
  provenance: { ref: PIN },
};
const create = vi.fn();
function Draft({ initialBundle }) {
  const [bundle, setBundle] = useState(initialBundle);
  const [step, setStep] = useState(0);
  return (
    <>
      <span data-testid="draft">{bundle?.manifest.name || "empty"}</span>
      <span>{["Source", "Settings", "Review"][step]}</span>
      <button onClick={() => setBundle({ manifest: { name: "manual" } })}>
        Upload manual
      </button>
      <button onClick={() => setStep(Math.min(2, step + 1))}>Continue</button>
      {step === 2 && <button onClick={() => create(bundle)}>Deploy</button>}
    </>
  );
}
Draft.propTypes = { initialBundle: PropTypes.object };
const child = (bundle) => <Draft initialBundle={bundle} />;
const gate = (load, props = {}) => (
  <HubSourceGate hubRef={PIN} workspaceId="w1" load={load} {...props}>
    {child}
  </HubSourceGate>
);
afterEach(() => {
  vi.clearAllMocks();
});

describe("binary authenticated loader", () => {
  it("uses only configured version/bundle endpoints, source=deploy and raw bytes", async () => {
    const data = await fixture();
    axios.get
      .mockResolvedValueOnce({ data: data.metadata })
      .mockResolvedValueOnce({
        data: data.archive.buffer,
        headers: { "x-omega-hub-bundle-sha256": data.metadata.bundleSha256 },
      })
      .mockResolvedValueOnce({ data: data.metadata });
    const bundle = await loadHubDeploymentSource(PIN);
    expect(bundle.source).toBe("hub");
    expect(axios.get.mock.calls[0][0]).toBe(
      "/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0",
    );
    expect(axios.get.mock.calls[1]).toEqual([
      expect.stringMatching(/\/bundle$/),
      expect.objectContaining({
        responseType: "arraybuffer",
        params: { source: "deploy" },
        signal: expect.anything(),
        onDownloadProgress: expect.any(Function),
      }),
    ]);
    expect(axios.get).toHaveBeenCalledTimes(3); // Fresh withdrawal check after the download.
  });
  it.each([404, 410])(
    "surfaces missing/removed HTTP %s without a download or fallback version",
    async (status) => {
      axios.get.mockRejectedValueOnce({
        statusCode: status,
        error: { code: "OMEGA_HUB_GONE" },
      });
      await expect(loadHubDeploymentSource(PIN)).rejects.toMatchObject({
        status,
      });
      expect(axios.get).toHaveBeenCalledTimes(1);
    },
  );
  it("rejects yanked metadata before spending a counted download", async () => {
    const { metadata } = await fixture();
    axios.get.mockResolvedValueOnce({
      data: { ...metadata, status: "yanked" },
    });
    await expect(loadHubDeploymentSource(PIN)).rejects.toThrow(
      /yanked|withdrawn/i,
    );
    expect(axios.get).toHaveBeenCalledTimes(1);
  });
  it("rejects a version withdrawn during transfer", async () => {
    const data = await fixture();
    axios.get
      .mockResolvedValueOnce({ data: data.metadata })
      .mockResolvedValueOnce({ data: data.archive.buffer, headers: {} })
      .mockResolvedValueOnce({ data: { ...data.metadata, status: "yanked" } });
    await expect(loadHubDeploymentSource(PIN)).rejects.toThrow(
      /yanked|withdrawn/i,
    );
  });
});

describe("source gate lifecycle (never creates a deployment)", () => {
  it("shows loading, then supplies initial bundle without automatic create or skipping review", async () => {
    const pending = deferred();
    const load = vi.fn(() => pending.promise);
    render(gate(load));
    expect(screen.getByTestId("hub-source-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("draft")).toBeNull();
    await act(async () => pending.resolve(ready));
    expect(screen.getByTestId("draft")).toHaveTextContent("first");
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Continue"));
    expect(screen.getByText("Settings")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Continue"));
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Deploy"));
    expect(create).toHaveBeenCalledOnce();
  });
  it("cancels pending fetch, ignores stale completion and preserves a manual upload", async () => {
    const pending = deferred();
    const load = vi.fn(() => pending.promise);
    render(gate(load));
    fireEvent.click(screen.getByTestId("hub-source-manual"));
    expect(load.mock.calls[0][1].signal.aborted).toBe(true);
    fireEvent.click(screen.getByText("Upload manual"));
    await act(async () => pending.resolve(ready));
    expect(screen.getByTestId("draft")).toHaveTextContent("manual");
    expect(create).not.toHaveBeenCalled();
  });
  it("aborts on ref/workspace switch and cannot publish an older response", async () => {
    const first = deferred();
    const second = deferred();
    const third = deferred();
    const load = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(third.promise);
    const view = render(gate(load));
    view.rerender(gate(load, { hubRef: "future-agi/first-agent@2.0.0" }));
    expect(load.mock.calls[0][1].signal.aborted).toBe(true);
    view.rerender(
      gate(load, { hubRef: "future-agi/first-agent@2.0.0", workspaceId: "w2" }),
    );
    expect(load.mock.calls[1][1].signal.aborted).toBe(true);
    await act(async () => {
      first.resolve(ready);
      second.resolve(ready);
    });
    expect(screen.queryByTestId("draft")).toBeNull();
    await act(async () =>
      third.resolve({ ...ready, manifest: { ...ready.manifest, name: "new" } }),
    );
    expect(screen.getByTestId("draft")).toHaveTextContent("new");
  });
  it("does not discard an edited draft on ref change until explicitly replacing it", async () => {
    const load = vi.fn().mockResolvedValue(ready);
    const view = render(gate(load));
    await screen.findByTestId("draft");
    fireEvent.click(screen.getByText("Upload manual"));
    view.rerender(gate(load, { hubRef: "future-agi/first-agent@2.0.0" }));
    expect(screen.getByTestId("draft")).toHaveTextContent("manual");
    expect(load).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("hub-source-replace"));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });
  it("pauses rather than discarding a ready manual draft on workspace change", async () => {
    const load = vi.fn().mockResolvedValue(ready);
    const view = render(gate(load));
    await screen.findByTestId("draft");
    fireEvent.click(screen.getByText("Upload manual"));
    view.rerender(gate(load, { workspaceId: "w2" }));
    expect(
      screen.getByTestId("hub-source-workspace-changed"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("draft")).not.toBeVisible();
    expect(screen.getByText("Continue")).toBeDisabled();
    expect(load).toHaveBeenCalledTimes(1);
    view.rerender(gate(load));
    expect(screen.getByTestId("draft")).toHaveTextContent("manual");
    expect(screen.getByTestId("draft")).toBeVisible();
    view.rerender(gate(load, { workspaceId: "w2" }));
    fireEvent.click(screen.getByTestId("hub-source-workspace-replace"));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(create).not.toHaveBeenCalled();
  });
  it("supports retry and manual fallback after failure; unmount aborts", async () => {
    const pending = deferred();
    const load = vi
      .fn()
      .mockRejectedValueOnce(new Error("unavailable"))
      .mockReturnValueOnce(pending.promise);
    const view = render(gate(load));
    await screen.findByTestId("hub-source-error");
    fireEvent.click(screen.getByTestId("hub-source-retry"));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    view.unmount();
    expect(load.mock.calls[1][1].signal.aborted).toBe(true);
  });
});
