import { describe, expect, it, vi } from "vitest";

import { hasQueryValue, lagLine, statusLine } from "../copy";
import {
  apiError,
  createBody,
  emptyForm,
  errorStatus,
  formOf,
  headersRetypeMessage,
  headersWithoutValue,
  keptLine,
  splitSubscriptions,
  subscriptionsOf,
  updateBody,
} from "../form";
import {
  ENV_URL_LIFETIME_MS,
  curlOf,
  downloadEnvLine,
  visibleControls,
} from "../text";

// The words a POSIX shell makes of a command that uses only single quotes,
// backslashes and line continuations; any other shell syntax outside quotes
// (; & | $ ` < > ( )) is an injection and throws.
function shellWords(cmd) {
  const words = [];
  let cur = null;
  let i = 0;
  while (i < cmd.length) {
    const c = cmd[i];
    if (c === "'") {
      const j = cmd.indexOf("'", i + 1);
      if (j < 0) throw new Error("unterminated quote");
      cur = (cur ?? "") + cmd.slice(i + 1, j);
      i = j + 1;
    } else if (c === "\\") {
      if (cmd[i + 1] !== "\n") cur = (cur ?? "") + cmd[i + 1];
      i += 2;
    } else if (/\s/.test(c)) {
      if (cur !== null) words.push(cur);
      cur = null;
      i += 1;
    } else if (";&|`$<>()".includes(c)) {
      throw new Error(`unquoted shell syntax: ${c}`);
    } else {
      cur = (cur ?? "") + c;
      i += 1;
    }
  }
  if (cur !== null) words.push(cur);
  return words;
}

// A saved endpoint as GET /webhooks/endpoints/{id}/ returns it.
const saved = (patch = {}) => ({
  name: "ci",
  url: "https://h.ai/x",
  org_wide: false,
  subscriptions: [{ event_type: "prompt.version.created", filters: {} }],
  payload_mode: "thin",
  include_people_names: true,
  header_names: [],
  timeout_ms: 15000,
  rate_limit_per_s: 10,
  max_in_flight: 4,
  max_age_s: 259200,
  accepts_run_targeting: false,
  ...patch,
});

describe("statusLine (DESIGN.md section 11.2 strings)", () => {
  const base = { url_host: "hooks.acme.ai", status: "enabled", health: {} };
  it.each([
    [{}, "Healthy"],
    [{ health: { lane: "slow" } }, "Slow: answers take over 5 s"],
    [
      { status: "disabled_auto", disabled_reason: "gone_410" },
      "Turned off: hooks.acme.ai answered 410 Gone",
    ],
    [
      { status: "disabled_auto", disabled_reason: "unsafe_address" },
      "Turned off: hooks.acme.ai resolves to a private or internal address",
    ],
    [
      {
        status: "disabled_auto",
        disabled_reason: "failing",
        health: { consecutive_failed_attempts: 10 },
      },
      "Turned off: every attempt failed for a day (10 attempts)",
    ],
    [
      { status: "disabled_manual", disabled_reason: "creator_removed" },
      "Turned off: its creator left this workspace",
    ],
    [{ status: "disabled_manual", disabled_reason: "manual" }, "Turned off"],
    [
      { status: "blocked_by_policy", disabled_reason: "policy" },
      "Blocked: not in this workspace's allowed hosts",
    ],
  ])("%j -> %s", (patch, text) => {
    expect(statusLine({ ...base, ...patch }).text).toBe(text);
  });

  it("names the time a failing run started", () => {
    const since = new Date(2026, 9, 23, 10, 42).toISOString();
    expect(statusLine({ ...base, health: { failing_since: since } }).text).toBe(
      "Failing since 10:42 · retrying",
    );
  });
});

describe("lagLine", () => {
  it("is null when nothing waits", () => {
    expect(lagLine({ due: 0, paused: 0 })).toBeNull();
  });
  it("counts due and paused deliveries", () => {
    const oldest = new Date(Date.now() - 14 * 60_000).toISOString();
    expect(lagLine({ due: 200, paused: 14, oldest_due_at: oldest })).toBe(
      "214 deliveries waiting · oldest 14 minutes",
    );
  });
});

describe("the Add webhook body", () => {
  it("sends one subscription per checked event with only the filters set", () => {
    const form = {
      ...emptyForm(),
      name: " ci ",
      url: " https://hooks.acme.ai/x ",
      events: {
        "prompt.version.created": true,
        "prompt.label.changed": true,
        "deployment.run.completed": false,
        "deployment.run.failed": true,
      },
      promptMode: "these",
      prompts: [{ id: "p1", name: "support-reply" }],
      labelMode: "these",
      labelNames: ["Production"],
      actions: ["moved"],
      environments: ["production"],
    };
    expect(subscriptionsOf(form)).toEqual([
      { event_type: "prompt.version.created", filters: { prompt_ids: ["p1"] } },
      {
        event_type: "prompt.label.changed",
        filters: {
          prompt_ids: ["p1"],
          label_names: ["Production"],
          actions: ["moved"],
        },
      },
      {
        event_type: "deployment.run.failed",
        filters: {
          environments: ["production"],
          statuses: ["failed", "interrupted"],
        },
      },
    ]);
    const body = createBody(
      { ...form, headers: [{ name: "Authorization", value: "Bearer x" }] },
      { workspaceId: "ws1" },
    );
    expect(body).toMatchObject({
      name: "ci",
      url: "https://hooks.acme.ai/x",
      workspace_id: "ws1",
      payload_mode: "thin",
      include_people_names: true,
      timeout_ms: 15000,
      rate_limit_per_s: 10,
      max_in_flight: 4,
      max_age_s: 259200,
      headers: { Authorization: "Bearer x" },
    });
    expect(body).not.toHaveProperty("accepts_run_targeting");
  });

  it("org-wide sends workspace_id null; all three label changes send no actions filter", () => {
    const form = {
      ...emptyForm({ orgWide: true }),
      name: "a",
      url: "https://h.ai/",
      events: { "prompt.label.changed": true },
    };
    expect(createBody(form, { workspaceId: "ws1" }).workspace_id).toBeNull();
    expect(subscriptionsOf(form)).toEqual([
      { event_type: "prompt.label.changed", filters: {} },
    ]);
  });

  it("an update sends nothing when nothing changed", () => {
    const endpoint = saved({
      subscriptions: [
        { event_type: "prompt.version.created", filters: {} },
        {
          event_type: "deployment.run.failed",
          filters: { environments: ["production"] },
        },
      ],
    });
    expect(updateBody(formOf(endpoint), endpoint)).toEqual({});
  });

  it("a masked saved URL starts empty and goes out only when typed in full (review S11)", () => {
    // url_display masks every query value; that text is not a URL the API accepts.
    const endpoint = {
      name: "a",
      url: "https://h.ai/x?token=…&v=…",
      org_wide: false,
      subscriptions: [{ event_type: "prompt.version.created", filters: {} }],
      payload_mode: "thin",
      include_people_names: true,
      header_names: [],
      timeout_ms: 15000,
      rate_limit_per_s: 10,
      max_in_flight: 4,
      max_age_s: 259200,
      accepts_run_targeting: false,
    };
    const form = formOf(endpoint);
    expect(form.url).toBe("");
    expect(form.urlMasked).toBe(true);
    expect(form.savedUrl).toBe(endpoint.url);
    expect(updateBody(form, endpoint)).toEqual({});
    expect(updateBody({ ...form, url: "   " }, endpoint)).toEqual({});
    expect(updateBody({ ...form, name: "b" }, endpoint)).toEqual({ name: "b" });
    expect(
      updateBody({ ...form, url: " https://h.ai/x?token=t2 " }, endpoint),
    ).toEqual({ url: "https://h.ai/x?token=t2" });
    // A saved URL with no query value is shown, and sent only when changed.
    const plain = { ...endpoint, url: "https://h.ai/x?flag" };
    const f2 = formOf(plain);
    expect([f2.url, f2.urlMasked]).toEqual(["https://h.ai/x?flag", false]);
    expect(updateBody(f2, plain)).toEqual({});
    expect(updateBody({ ...f2, url: "https://h.ai/y" }, plain)).toEqual({
      url: "https://h.ai/y",
    });
  });

  it("an update sends the URL and headers only when changed", () => {
    const endpoint = {
      name: "a",
      url: "https://h.ai/x?token=…",
      org_wide: false,
      subscriptions: [{ event_type: "prompt.version.created", filters: {} }],
      payload_mode: "thin",
      include_people_names: true,
      header_names: ["Authorization"],
      timeout_ms: 15000,
      rate_limit_per_s: 10,
      max_in_flight: 4,
      max_age_s: 259200,
      accepts_run_targeting: false,
    };
    const form = formOf(endpoint);
    const body = updateBody(form, endpoint);
    expect(body).not.toHaveProperty("url");
    expect(body).not.toHaveProperty("headers");
    expect(body).not.toHaveProperty("workspace_id");
    const body2 = updateBody(
      { ...form, url: "https://h.ai/y", headersTouched: true, headers: [] },
      endpoint,
    );
    expect(body2.url).toBe("https://h.ai/y");
    expect(body2.headers).toEqual({});
  });

  it("a kept saved header must be typed again once the headers change", () => {
    const endpoint = {
      name: "a",
      url: "https://h.ai/x",
      org_wide: false,
      subscriptions: [],
      payload_mode: "thin",
      include_people_names: true,
      header_names: ["Authorization", "X-Team"],
      timeout_ms: 15000,
      rate_limit_per_s: 10,
      max_in_flight: 4,
      max_age_s: 259200,
      accepts_run_targeting: false,
    };
    const form = formOf(endpoint);
    // Untouched: nothing is sent, so nothing is blanked.
    expect(headersWithoutValue(form)).toEqual([]);
    // A new header added next to the saved ones: both saved values are needed.
    const added = {
      ...form,
      headersTouched: true,
      headers: [...form.headers, { name: "X-New", value: "1" }],
    };
    expect(headersWithoutValue(added)).toEqual(["Authorization", "X-Team"]);
    // One saved header removed, the other typed again: nothing is missing.
    const retyped = {
      ...form,
      headersTouched: true,
      headers: [{ name: "X-Team", value: "core", saved: false }],
    };
    expect(headersWithoutValue(retyped)).toEqual([]);
    expect(updateBody(retyped, endpoint).headers).toEqual({ "X-Team": "core" });
    expect(headersRetypeMessage(["X-Team"])).toBe(
      "Type the value of X-Team again. Values are hidden after saving, and saving the headers replaces them all.",
    );
  });
});

describe("saving an endpoint made with the API (review M2)", () => {
  const targeted = {
    event_type: "deployment.run.completed",
    filters: { deployment_ids: ["dep-A"] },
  };

  it("keeps subscriptions the form cannot show, and sends only what changed", () => {
    const endpoint = saved({
      subscriptions: [
        targeted,
        { event_type: "prompt.version.created", filters: {} },
      ],
    });
    const form = formOf(endpoint);
    expect(form.keptSubs).toEqual([targeted]);
    expect(form.events["deployment.run.completed"]).toBe(false);
    expect(keptLine(targeted)).toBe(
      "deployment.run.completed · deployments: dep-A",
    );
    // Only the timeout: the subscriptions, run targeting and payload stay out.
    expect(updateBody({ ...form, timeoutS: 20 }, endpoint)).toEqual({
      timeout_ms: 20000,
    });
    // A new event: every subscription goes out, the kept one exactly as saved.
    const more = {
      ...form,
      events: { ...form.events, "deployment.run.failed": true },
      environments: ["production"],
    };
    expect(updateBody(more, endpoint)).toEqual({
      subscriptions: [
        { event_type: "prompt.version.created", filters: {} },
        {
          event_type: "deployment.run.failed",
          filters: {
            environments: ["production"],
            statuses: ["failed", "interrupted"],
          },
        },
        targeted,
      ],
    });
  });

  it("a wildcard-only endpoint saves without re-sending (or losing) its subscription", () => {
    const endpoint = saved({
      subscriptions: [{ event_type: "prompt.*", filters: {} }],
    });
    const form = formOf(endpoint);
    expect(form.keptSubs).toEqual([{ event_type: "prompt.*", filters: {} }]);
    expect(subscriptionsOf(form)).toEqual([]);
    expect(updateBody({ ...form, name: "renamed" }, endpoint)).toEqual({
      name: "renamed",
    });
  });

  it.each([
    [
      "filters that differ between the two prompt events",
      [
        {
          event_type: "prompt.version.created",
          filters: { prompt_ids: ["p1"] },
        },
        { event_type: "prompt.label.changed", filters: { prompt_ids: ["p2"] } },
      ],
      2,
    ],
    [
      "an environment the checkboxes do not offer",
      [
        {
          event_type: "deployment.run.failed",
          filters: { environments: ["staging"] },
        },
      ],
      1,
    ],
    [
      "excluded prompts",
      [
        {
          event_type: "prompt.version.created",
          filters: { exclude_prompt_ids: ["p9"] },
        },
      ],
      1,
    ],
  ])("keeps %s read-only", (_what, subs, keptCount) => {
    const { editable, kept } = splitSubscriptions(subs);
    expect(kept).toHaveLength(keptCount);
    expect(editable).toHaveLength(subs.length - keptCount);
    const endpoint = saved({ subscriptions: subs });
    expect(updateBody(formOf(endpoint), endpoint)).toEqual({});
  });

  it("sends the admin-only fields only when they change", () => {
    const endpoint = saved({
      accepts_run_targeting: true,
      payload_mode: "full",
    });
    const form = formOf(endpoint);
    // A Member who may manage the webhook changes the rate: no admin field.
    expect(updateBody({ ...form, ratePerS: 5 }, endpoint)).toEqual({
      rate_limit_per_s: 5,
    });
    expect(
      updateBody(
        { ...form, acceptsRunTargeting: false, payloadMode: "thin" },
        endpoint,
      ),
    ).toEqual({ accepts_run_targeting: false, payload_mode: "thin" });
  });
});

describe("text helpers", () => {
  it("warns about a value in the query string", () => {
    expect(hasQueryValue("https://h.ai/x?token=abc")).toBe(true);
    expect(hasQueryValue("https://h.ai/x?flag")).toBe(false);
    expect(hasQueryValue("https://h.ai/x")).toBe(false);
  });

  it("Copy as curl quotes every value, the URL and header names too (review M5)", () => {
    const url = "https://hooks.example.com/x';id;'";
    const curl = curlOf(
      url,
      { "X-Evil'": "a'b", "webhook-signature": "v1,abc" },
      `{"note":"it's"}`,
    );
    expect(shellWords(curl)).toEqual([
      "curl",
      "-X",
      "POST",
      url,
      "-H",
      "X-Evil': a'b",
      "-H",
      "webhook-signature: <signature>",
      "--data-raw",
      `{"note":"it's"}`,
    ]);
    // The unquoted form the review found would have run `id`.
    expect(() => shellWords(`curl -X POST '${url}'`)).toThrow(/shell syntax/);
  });

  it("Copy as curl replaces the signature", () => {
    const curl = curlOf(
      "https://h.ai/x",
      { "webhook-signature": "v1,abc", "webhook-id": "evt_1" },
      '{"a":1}',
    );
    expect(curl).toContain("-H 'webhook-signature: <signature>'");
    expect(curl).toContain("-H 'webhook-id: evt_1'");
    expect(curl).not.toContain("v1,abc");
  });

  it("shows bidi overrides as visible code points", () => {
    expect(visibleControls("abc‮txt.exe")).toBe("abc<U+202E>txt.exe");
    expect(visibleControls("line1\nline2\t<b>x</b>")).toBe(
      "line1\nline2\t<b>x</b>",
    );
  });
});

describe("apiError and errorStatus (the shapes callers get)", () => {
  const body = {
    status: false,
    code: "WEBHOOK_HOST_NOT_ALLOWED",
    attr: "url",
    result: "Your organization only allows webhooks to *.acme.test.",
    message: "Your organization only allows webhooks to *.acme.test.",
  };

  it("reads FA's interceptor rejection: the body spread with statusCode", () => {
    const rejected = {
      ...body,
      statusCode: 400,
      transportCode: "ERR_BAD_REQUEST",
    };
    expect(apiError(rejected, "Could not save the webhook.")).toEqual({
      code: "WEBHOOK_HOST_NOT_ALLOWED",
      attr: "url",
      message: "Your organization only allows webhooks to *.acme.test.",
    });
    expect(errorStatus({ ...body, statusCode: 404 })).toBe(404);
  });

  it("reads a raw Axios error the same way", () => {
    const axiosError = { response: { status: 404, data: body } };
    expect(apiError(axiosError).code).toBe("WEBHOOK_HOST_NOT_ALLOWED");
    expect(errorStatus(axiosError)).toBe(404);
  });

  it("falls back when there is no message", () => {
    expect(apiError(null, "Could not redeliver.").message).toBe(
      "Could not redeliver.",
    );
    expect(apiError({ statusCode: 500 }, "Could not redeliver.").message).toBe(
      "Could not redeliver.",
    );
    expect(errorStatus(undefined)).toBeNull();
  });
});

describe("downloadEnvLine (WH-3 review S9)", () => {
  it("clicks an anchor in the document and revokes its URL only later", () => {
    vi.useFakeTimers();
    const create = vi.fn(() => "blob:wh-env");
    const revoke = vi.fn();
    const saved = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
    URL.createObjectURL = create;
    URL.revokeObjectURL = revoke;
    const clicks = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function record() {
        clicks.push({
          inDocument: document.body.contains(this),
          href: this.getAttribute("href"),
          download: this.download,
        });
      });
    try {
      downloadEnvLine("whsec_dGVzdA==");
      expect(clicks).toEqual([
        {
          inDocument: true,
          href: "blob:wh-env",
          download: "futureagi-webhook.env",
        },
      ]);
      expect(document.querySelector("a[download]")).toBeNull();
      expect(revoke).not.toHaveBeenCalled();
      vi.advanceTimersByTime(ENV_URL_LIFETIME_MS - 1);
      expect(revoke).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(revoke).toHaveBeenCalledWith("blob:wh-env");
    } finally {
      click.mockRestore();
      URL.createObjectURL = saved.create;
      URL.revokeObjectURL = saved.revoke;
      vi.useRealTimers();
    }
  });
});
