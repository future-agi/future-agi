import { describe, expect, it } from "vitest";

import {
  KEY_MODE,
  bodyText,
  isUsableKey,
  maskValue,
  routeWithQuery,
  shellQuote,
  toCurl,
} from "../curl";

const BASE = "https://app.example.test/omega/api";
const KEY = {
  apiKey: "c0c0c0c0c0c0c0c0c0c0c0c0c0c0wxyz",
  secretKey: "d1d1d1d1d1d1d1d1d1d1d1d1d1d1stuv",
};
const SCOPE = {
  organizationId: "22222222-2222-4222-8222-222222222222",
  workspaceId: "33333333-3333-4333-8333-333333333333",
};

const list = {
  id: "sandboxes.list",
  title: "List",
  route: "GET /workspace/sandboxes",
  method: "GET",
  path: "/workspace/sandboxes",
};
const create = {
  id: "sandboxes.create",
  title: "Create",
  route: "POST /workspace/sandboxes",
  method: "POST",
  path: "/workspace/sandboxes",
  json: { name: "it's mine", template: "base", network: "none" },
  idempotent: "sandbox",
};

describe("curl for a gateway request", () => {
  it("reads the key from the shell by default, and puts no key anywhere but its two headers", () => {
    const text = toCurl(list, { baseUrl: BASE, scope: SCOPE });
    expect(text).toBe(
      [
        "curl -sS 'https://app.example.test/omega/api/workspace/sandboxes'",
        '-H "X-Api-Key: $FI_API_KEY"',
        '-H "X-Secret-Key: $FI_SECRET_KEY"',
        `-H 'X-Organization-Id: ${SCOPE.organizationId}'`,
        `-H 'X-Workspace-Id: ${SCOPE.workspaceId}'`,
        "-H 'Accept: application/json'",
      ].join(" \\\n  "),
    );
  });

  it("masks a held key on screen, and carries it whole only in FULL mode", () => {
    const masked = toCurl(list, {
      baseUrl: BASE,
      key: KEY,
      mode: KEY_MODE.MASKED,
    });
    expect(masked).not.toContain(KEY.apiKey);
    expect(masked).not.toContain(KEY.secretKey);
    expect(masked).toContain(`X-Api-Key: ${maskValue(KEY.apiKey)}`);
    expect(masked).toContain(KEY.apiKey.slice(-4));
    // The secret shows none of itself, not even a tail (P11 fix round 2).
    expect(masked).toContain(
      "-H 'X-Secret-Key: \u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022'",
    );
    expect(masked).not.toContain(KEY.secretKey.slice(-4));

    const env = toCurl(list, { baseUrl: BASE, key: KEY, mode: KEY_MODE.ENV });
    expect(env).not.toContain(KEY.secretKey);
    expect(env).toContain('"X-Secret-Key: $FI_SECRET_KEY"');

    const full = toCurl(list, { baseUrl: BASE, key: KEY, mode: KEY_MODE.FULL });
    expect(full).toContain(`-H 'X-Api-Key: ${KEY.apiKey}'`);
    expect(full).toContain(`-H 'X-Secret-Key: ${KEY.secretKey}'`);
    // Never in the URL (the gateway answers 400 credential_in_query).
    const url = full.split("\n")[0];
    expect(url).not.toContain(KEY.apiKey);
    expect(url).not.toContain(KEY.secretKey);
  });

  it("falls back to the shell variables for a key it would not put in a command", () => {
    const text = toCurl(list, {
      baseUrl: BASE,
      key: { apiKey: "x'; rm -rf ~; '", secretKey: "short" },
      mode: KEY_MODE.FULL,
    });
    expect(text).toContain('"X-Api-Key: $FI_API_KEY"');
    expect(text).not.toContain("rm -rf");
    expect(isUsableKey({ apiKey: "short", secretKey: KEY.secretKey })).toBe(
      false,
    );
    expect(isUsableKey(KEY)).toBe(true);
  });

  it("sends a JSON body as its exact bytes, with its type and an Idempotency-Key", () => {
    const text = toCurl(create, {
      baseUrl: BASE,
      idempotencyKey: "fa-sbx-1111",
    });
    expect(text.split("\n")[0]).toBe(
      "curl -sS -X POST 'https://app.example.test/omega/api/workspace/sandboxes' \\",
    );
    expect(text).toContain("-H 'Content-Type: application/json'");
    expect(text).toContain("-H 'Idempotency-Key: fa-sbx-1111'");
    expect(text).toContain(`--data-binary ${shellQuote(bodyText(create))}`);
    expect(() => toCurl(create, { baseUrl: BASE })).toThrow(/Idempotency-Key/);
  });

  it("streams with -N and asks for event-stream; reads a large body from its file", () => {
    const exec = {
      ...list,
      id: "sandboxes.exec",
      method: "POST",
      path: "/workspace/sandboxes/sbx_1/exec",
      json: { command: "uname -a" },
      stream: true,
    };
    const text = toCurl(exec, { baseUrl: BASE });
    expect(text.split("\n")[0]).toMatch(/^curl -sS -N -X POST /);
    expect(text).toContain("-H 'Accept: text/event-stream'");

    const deploy = { ...create, bodyFile: "omega-deploy-request.json" };
    const file = toCurl(deploy, { baseUrl: BASE, idempotencyKey: "k" });
    expect(file).toContain("--data-binary '@omega-deploy-request.json'");
    expect(file).not.toContain("it's mine");
  });

  it("adds a query the way the tab's client does", () => {
    expect(
      routeWithQuery("/workspace/x", {
        source: "build",
        revision: 2,
        tail: "",
      }),
    ).toBe("/workspace/x?source=build&revision=2");
    const text = toCurl(
      {
        ...list,
        path: "/workspace/deployments/dep-0123456789abcdef/logs",
        query: { source: "build", revision: 2 },
      },
      { baseUrl: BASE },
    );
    expect(text).toContain("/logs?source=build&revision=2'");
  });

  it("refuses a base URL or a route that is not the gateway's", () => {
    for (const baseUrl of [
      "",
      "app.example.test/omega/api",
      "https://a.test",
      "https://a.test/omega/api?x=1",
      // A script URL in a base URL is refused like any other non-HTTP origin.
      // eslint-disable-next-line no-script-url
      "javascript:alert(1)//x/y",
    ]) {
      expect(() => toCurl(list, { baseUrl }), baseUrl).toThrow(TypeError);
    }
    expect(() =>
      toCurl({ ...list, path: "/os/sandboxes" }, { baseUrl: BASE }),
    ).toThrow(TypeError);
    expect(() =>
      toCurl({ ...list, path: "/workspace//x" }, { baseUrl: BASE }),
    ).toThrow(TypeError);
  });

  it("drops a selection id that is not an id", () => {
    const text = toCurl(list, {
      baseUrl: BASE,
      scope: { organizationId: "a'b", workspaceId: "ws 1" },
    });
    expect(text).not.toContain("X-Organization-Id");
    expect(text).not.toContain("X-Workspace-Id");
  });

  it("quotes any argument for a POSIX shell", () => {
    expect(shellQuote("a'b")).toBe("'a'\\''b'");
    expect(shellQuote('$HOME `x` "y"')).toBe("'$HOME `x` \"y\"'");
  });
});
