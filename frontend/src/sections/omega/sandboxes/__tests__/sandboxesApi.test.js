import { describe, expect, it, vi } from "vitest";

import {
  createSandboxesApi,
  encodeFilePath,
  sandboxRoutes,
} from "../api/sandboxesApi";

function setup(reply = {}) {
  const omegaJson = vi.fn(async () => reply);
  const openOmegaStream = vi.fn(() => ({
    close: vi.fn(),
    done: Promise.resolve({ ended: true }),
  }));
  return {
    api: createSandboxesApi({ omegaJson, openOmegaStream }),
    omegaJson,
    openOmegaStream,
  };
}

describe("sandboxes client (unit)", () => {
  it("speaks only the customer /workspace/sandboxes family, never /os", async () => {
    const { api, omegaJson } = setup({
      sandbox: { id: "sbx_1" },
      snapshots: [],
      activity: [],
      file: {},
    });
    await api.list();
    await api.get("sbx_1");
    await api.hibernate("sbx_1");
    await api.resume("sbx_1");
    await api.snapshots("sbx_1");
    await api.restore("sbx_1", "snap_1", { name: "copy" });
    await api.activity("sbx_1");
    await api.destroy("sbx_1");
    const paths = omegaJson.mock.calls.map(([path]) => path);
    expect(paths).toEqual([
      "/workspace/sandboxes",
      "/workspace/sandboxes/sbx_1",
      "/workspace/sandboxes/sbx_1/hibernate",
      "/workspace/sandboxes/sbx_1/resume",
      "/workspace/sandboxes/sbx_1/snapshots",
      "/workspace/sandboxes/sbx_1/snapshots/snap_1/restore",
      "/workspace/sandboxes/sbx_1/activity",
      "/workspace/sandboxes/sbx_1",
    ]);
    expect(paths.every((path) => !path.startsWith("/os"))).toBe(true);
    expect(omegaJson.mock.calls[5][1]).toEqual({
      method: "POST",
      json: { name: "copy" },
    });
    expect(omegaJson.mock.calls[7][1]).toEqual({ method: "DELETE" });
  });

  it("creates with the given body and unwraps the sandbox", async () => {
    const { api, omegaJson } = setup({ sandbox: { id: "sbx_9" } });
    const input = {
      image: "alpine:latest",
      limits: { cpus: 1, memoryMb: 512, pidsLimit: 256 },
      network: "none",
      idleTtlSeconds: 1800,
    };
    await expect(api.create(input)).resolves.toEqual({ id: "sbx_9" });
    expect(omegaJson).toHaveBeenCalledWith("/workspace/sandboxes", {
      method: "POST",
      json: input,
    });
  });

  it("encodes ids and file paths segment by segment and refuses traversal", async () => {
    const { api, omegaJson } = setup({ file: { path: "a b/c.txt" } });
    await api.writeFile("sbx_1", "a b/c.txt", "hello");
    expect(omegaJson).toHaveBeenCalledWith(
      "/workspace/sandboxes/sbx_1/files/a%20b/c.txt",
      { method: "PUT", json: { content: "hello", encoding: "utf8" } },
    );
    await api.listFiles("sbx_1", "src");
    expect(omegaJson).toHaveBeenLastCalledWith(
      "/workspace/sandboxes/sbx_1/files",
      { query: { path: "src" } },
    );
    expect(sandboxRoutes.one("a/b")).toBe("/workspace/sandboxes/a%2Fb");
    expect(() => encodeFilePath("../etc/passwd")).toThrow(TypeError);
    expect(() => sandboxRoutes.one("")).toThrow(TypeError);
  });

  it("streams exec as a non-resumable POST so a reconnect never re-runs the command", () => {
    const { api, openOmegaStream } = setup();
    const onEvent = vi.fn();
    api.exec("sbx_1", { command: "echo 1" }, { onEvent });
    expect(openOmegaStream).toHaveBeenCalledWith(
      "/workspace/sandboxes/sbx_1/exec",
      expect.objectContaining({
        method: "POST",
        json: { command: "echo 1" },
        resumable: false,
        onEvent,
      }),
    );
  });

  it("downloads a file's bytes with a raw request, never as JSON", async () => {
    const blob = new Blob(["png"]);
    const omegaFetch = vi.fn(async () => ({
      ok: true,
      response: { blob: async () => blob },
    }));
    const api = createSandboxesApi({
      omegaJson: vi.fn(),
      omegaFetch,
      openOmegaStream: vi.fn(),
    });
    await expect(api.downloadFile("sbx_1", "out/my chart.png")).resolves.toBe(
      blob,
    );
    expect(omegaFetch).toHaveBeenCalledWith(
      "/workspace/sandboxes/sbx_1/download/out/my%20chart.png",
      expect.objectContaining({ raw: true, headers: { Accept: "*/*" } }),
    );
  });

  it("turns a refused download into a typed error", async () => {
    const omegaFetch = vi.fn(async () => ({
      ok: false,
      state: "refused",
      status: 404,
      code: "not_found",
      message: "File not found",
    }));
    const api = createSandboxesApi({
      omegaJson: vi.fn(),
      omegaFetch,
      openOmegaStream: vi.fn(),
    });
    await expect(api.downloadFile("sbx_1", "gone.txt")).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });
  });

  it("uploads raw bytes with a fresh Idempotency-Key, and deletes, moves and makes folders on their own routes", async () => {
    const { api, omegaJson } = setup({
      file: { path: "a.bin", bytes: 3 },
      deleted: {},
      moved: {},
      folder: {},
    });
    const file = new Blob(["abc"]);
    await api.uploadFile("sbx_1", "dir/a b.bin", file);
    await api.uploadFile("sbx_1", "dir/a b.bin", file);
    await api.deletePath("sbx_1", "dir/old.txt");
    await api.deletePath("sbx_1", "dir", { recursive: true });
    await api.movePath("sbx_1", "a.txt", "b.txt");
    await api.makeFolder("sbx_1", "out");
    const [first, second, del, delRecursive, move, mkdir] =
      omegaJson.mock.calls;
    expect(first[0]).toBe("/workspace/sandboxes/sbx_1/upload/dir/a%20b.bin");
    expect(first[1]).toMatchObject({
      method: "PUT",
      body: file,
      headers: { "Content-Type": "application/octet-stream" },
    });
    expect(first[1].headers["Idempotency-Key"]).toBeTruthy();
    expect(second[1].headers["Idempotency-Key"]).not.toBe(
      first[1].headers["Idempotency-Key"],
    );
    expect(del).toEqual([
      "/workspace/sandboxes/sbx_1/files/dir/old.txt",
      { method: "DELETE", query: {} },
    ]);
    expect(delRecursive).toEqual([
      "/workspace/sandboxes/sbx_1/files/dir",
      { method: "DELETE", query: { recursive: "true" } },
    ]);
    expect(move).toEqual([
      "/workspace/sandboxes/sbx_1/move",
      { method: "POST", json: { from: "a.txt", to: "b.txt" } },
    ]);
    expect(mkdir).toEqual([
      "/workspace/sandboxes/sbx_1/folders/out",
      { method: "PUT" },
    ]);
    expect(
      omegaJson.mock.calls.every(([path]) =>
        path.startsWith("/workspace/sandboxes/"),
      ),
    ).toBe(true);
  });

  it("reads a hidden environments API (404 not_found, HIDE-API) as no environments, and still throws any other failure", async () => {
    const refused = (fields) =>
      vi.fn(async () => {
        throw Object.assign(new Error("refused"), fields);
      });
    const hidden = createSandboxesApi({
      omegaJson: refused({ status: 404, code: "not_found" }),
    });
    await expect(hidden.environments()).resolves.toEqual([]);
    for (const fields of [
      { status: 404, code: "product_not_admitted" },
      { status: 503, code: "unavailable" },
      { status: 403, code: "route_not_allowed" },
    ]) {
      const api = createSandboxesApi({ omegaJson: refused(fields) });
      await expect(api.environments()).rejects.toMatchObject(fields);
    }
    const { api } = setup({ environments: [{ id: "env_1" }] });
    await expect(api.environments()).resolves.toEqual([{ id: "env_1" }]);
  });

  it("cancels a command by its exec id", async () => {
    const { api, omegaJson } = setup({ cancelled: true });
    await api.cancelExec("sbx_1", "exec_abc");
    expect(omegaJson).toHaveBeenCalledWith(
      "/workspace/sandboxes/sbx_1/exec/exec_abc/cancel",
      { method: "POST" },
    );
  });
});
