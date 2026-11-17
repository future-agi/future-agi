import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  canUseNetwork,
  initialForm,
  templateChoices,
  ttlOptions,
  validateForm,
} from "../lib/createForm";
import {
  EXEC_IDLE,
  applyExecEnd,
  applyExecEvent,
  describeOutcome,
  startExec,
} from "../lib/execOutput";
import {
  formatDuration,
  formatExactTime,
  formatLimits,
  hibernatesIn,
  timeAgo,
  verbLabel,
} from "../lib/format";
import { fileErrorMessage } from "../lib/files";
import { isSandboxId, runHref, sandboxPath } from "../lib/links";
import { CAPS } from "./helpers";

describe("create form (unit)", () => {
  it("starts from the provider defaults with the network off", () => {
    const form = initialForm(CAPS);
    expect(form).toMatchObject({
      source: "image",
      image: "alpine:latest",
      cpus: "1",
      memoryMb: "512",
      pidsLimit: "256",
      network: "none",
      idleTtlSeconds: 1800,
    });
    expect(
      initialForm({ ...CAPS, defaultNetwork: "full", networkModes: ["none"] })
        .network,
    ).toBe("none");
  });

  it("refuses limits outside the provider caps and images it does not offer", () => {
    const form = {
      ...initialForm(CAPS),
      cpus: "2",
      memoryMb: "32",
      pidsLimit: "16.5",
      image: "ubuntu:latest",
    };
    expect(validateForm(form, CAPS)).toEqual({
      cpus: "Between 0.1 and 1",
      memoryMb: "Between 64 and 512",
      pidsLimit: "Enter a whole number",
      image: "Choose an image this deployment offers",
    });
    expect(validateForm(initialForm(CAPS), CAPS)).toEqual({});
  });

  it("sends exactly the API's create fields, image or template, never both", () => {
    const form = {
      ...initialForm(CAPS),
      name: "  etl  ",
      cpus: "0.5",
      network: "full",
    };
    expect(buildCreatePayload(form)).toEqual({
      name: "etl",
      image: "alpine:latest",
      limits: { cpus: 0.5, memoryMb: 512, pidsLimit: 256 },
      network: "full",
      idleTtlSeconds: 1800,
    });
    const templated = buildCreatePayload({
      ...form,
      source: "environment",
      environmentId: "env_1",
    });
    expect(templated.environmentId).toBe("env_1");
    expect(templated).not.toHaveProperty("image");
  });

  it("offers idle timeouts inside the caps and only container templates that are active", () => {
    expect(
      ttlOptions({
        ...CAPS,
        idleTtlSeconds: { default: 1200, min: 600, max: 3600 },
      }),
    ).toEqual([900, 1200, 1800, 3600]);
    expect(canUseNetwork({ ...CAPS, networkModes: ["none"] })).toBe(false);
    const choices = templateChoices([
      { id: "a", name: "Box", sandboxKind: "container", status: "active" },
      { id: "b", name: "Local", sandboxKind: "local", status: "active" },
      { id: "c", name: "Paused", sandboxKind: "container", status: "paused" },
    ]);
    expect(choices.map((c) => c.disabled)).toEqual([false, true, true]);
  });
});

describe("exec output (unit)", () => {
  it("folds started, output and exit events into one command's state", () => {
    let state = startExec("echo hi");
    state = applyExecEvent(state, {
      event: "started",
      json: { execId: "exec_1" },
    });
    expect(state).toMatchObject({ status: "running", execId: "exec_1" });
    state = applyExecEvent(state, { event: "stdout", json: { data: "hi\n" } });
    state = applyExecEvent(state, {
      event: "stderr",
      json: { data: "warn\n" },
    });
    state = applyExecEvent(state, {
      event: "exit",
      json: {
        exitCode: 0,
        timedOut: false,
        cancelled: false,
        truncated: false,
        durationMs: 12,
      },
    });
    expect(state.chunks).toEqual([
      { stream: "stdout", text: "hi\n" },
      { stream: "stderr", text: "warn\n" },
    ]);
    expect(describeOutcome(state)).toBe("exit 0");
    expect(applyExecEnd(state, { ended: true })).toBe(state);
  });

  it("reports cancel, refusal and a stream that ends without an exit", () => {
    const running = applyExecEvent(startExec("sleep 9"), {
      event: "started",
      json: { execId: "exec_2" },
    });
    expect(
      describeOutcome(
        applyExecEvent(running, {
          event: "exit",
          json: { exitCode: null, cancelled: true },
        }),
      ),
    ).toBe("Cancelled");
    expect(
      applyExecEnd(startExec("x"), {
        ok: false,
        message: "Sandbox is hibernated; resume it first.",
      }),
    ).toMatchObject({
      status: "failed",
      error: "Sandbox is hibernated; resume it first.",
    });
    const cut = applyExecEnd(running, { ended: true });
    expect(cut).toMatchObject({
      status: "failed",
      error: "The command stream ended before the command finished.",
    });
    // The header stays one short word; RunOutput shows the reason in full.
    expect(describeOutcome(cut)).toBe("Failed");
    expect(EXEC_IDLE.status).toBe("idle");
  });

  it("says a command on a hibernated sandbox waits for it to resume, then runs as any other", () => {
    const waking = startExec("echo woke", { waking: true, runningLimit: 4 });
    expect(describeOutcome(waking)).toBe("Resuming the sandbox…");
    const running = applyExecEvent(waking, {
      event: "started",
      json: { execId: "exec_3" },
    });
    expect(describeOutcome(running)).toBe("Running…");
    expect(describeOutcome(startExec("echo hi"))).toBe("Starting…");
  });

  it("words a resume refused at the running cap, and keeps every other refusal's own words", () => {
    const atCap = {
      ok: false,
      status: 409,
      code: "conflict",
      reason: "quota_exceeded",
      message:
        "Sandbox is hibernated and could not resume for this call: This workspace already has 4 active sandboxes (the limit is 4). Hibernate or destroy one first. Then retry this call.",
    };
    const waking = (runningLimit) =>
      startExec("ls", { waking: true, runningLimit });
    expect(applyExecEnd(waking(4), atCap)).toMatchObject({
      status: "failed",
      error:
        "The sandbox couldn't resume, so the command didn't run. This workspace is at its limit of 4 running sandboxes. Hibernate or delete one, then run the command again.",
    });
    expect(applyExecEnd(waking(1), atCap).error).toBe(
      "The sandbox couldn't resume, so the command didn't run. This workspace is at its limit of 1 running sandbox. Hibernate or delete one, then run the command again.",
    );
    expect(applyExecEnd(waking(null), atCap).error).toBe(
      "The sandbox couldn't resume, so the command didn't run. This workspace is at its limit of running sandboxes. Hibernate or delete one, then run the command again.",
    );
    const busy = {
      ok: false,
      status: 503,
      code: "capacity_exhausted",
      reason: "host_busy",
      message:
        "This server is busy right now, so the sandbox was not started. Try again in a few minutes.",
    };
    expect(applyExecEnd(waking(4), busy).error).toBe(busy.message);
    // A command on a running sandbox never resumed anything.
    expect(applyExecEnd(startExec("ls"), atCap).error).toBe(atCap.message);
  });
});

describe("format and links (unit)", () => {
  it("formats only what the API returned", () => {
    const now = Date.parse("2026-10-13T12:00:00Z");
    expect(formatLimits({ cpus: 0.5, memoryMb: 2048 })).toBe("0.5 CPU · 2 GiB");
    expect(formatLimits(null)).toBe("—");
    expect(formatDuration(5400)).toBe("1 h 30 min");
    expect(timeAgo("2026-10-13T11:55:00Z", now)).toBe("5 min ago");
    expect(hibernatesIn("2026-10-13T12:28:00Z", now)).toBe("in 28 min");
    expect(hibernatesIn(undefined, now)).toBe("—");
    expect(verbLabel("sandbox.file.write")).toBe("Wrote a file");
    expect(verbLabel("sandbox.future")).toBe("sandbox.future");
  });

  it("gives the exact time with its time zone, for the hover on a relative time", () => {
    const exact = formatExactTime("2026-11-10T01:08:30Z", { timeZone: "UTC" });
    expect(exact).toMatch(/2026/);
    expect(exact).toMatch(/08:30/);
    expect(exact).toMatch(/UTC$/);
    expect(formatExactTime(undefined)).toBe("—");
  });

  it("links runs into Omega Console and keeps tab routes stable", () => {
    expect(runHref("conv 1")).toBe("/dashboard/console/chat/conv%201");
    expect(sandboxPath("sbx_1")).toBe("/dashboard/sandboxes/sbx_1");
    expect(sandboxPath("sbx_1", "run")).toBe("/dashboard/sandboxes/sbx_1/run");
    expect(sandboxPath("sbx_1", "overview")).toBe("/dashboard/sandboxes/sbx_1");
  });

  it("recognises only ids EE could have issued", () => {
    for (const id of [
      "sbx_0123456789abcdef01234567",
      "wsbx-abc123",
      "agent-task-run_1",
    ])
      expect(isSandboxId(id), id).toBe(true);
    for (const id of [
      "",
      "sbx_a48116bf…0fc9",
      "sbx 1",
      "..",
      ".",
      "a/b",
      "%2e%2e",
      undefined,
    ])
      expect(isSandboxId(id), String(id)).toBe(false);
  });
});

describe("file error copy (unit)", () => {
  it("names a size limit for our codes and for a 413 from any hop, else keeps the server's words", () => {
    expect(fileErrorMessage({ status: 413, code: "too_large" })).toBe(
      "This file is larger than this sandbox accepts in one upload.",
    );
    expect(
      fileErrorMessage({ status: 413, code: "request_body_too_large" }),
    ).toBe("This file is larger than Future AGI sends in one upload.");
    // A proxy or ingress limit: a 413 with no code of ours (its body may be HTML).
    expect(
      fileErrorMessage({
        status: 413,
        code: null,
        message: "Omega could not complete the request.",
      }),
    ).toBe("This file is larger than this deployment accepts in one upload.");
    expect(
      fileErrorMessage({
        status: 400,
        code: "validation",
        message: "x leads outside the sandbox workspace.",
      }),
    ).toBe("x leads outside the sandbox workspace.");
    expect(fileErrorMessage({ status: 500 })).toBe(
      "That did not work. Try again.",
    );
    expect(fileErrorMessage(null)).toBe("");
  });
});
