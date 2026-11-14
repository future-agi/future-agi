// Runs the real mixpanel-browser library (not mocked) with the app's own
// Mixpanel set-up and decodes the requests it sends, so the properties the
// library adds itself ($current_url, $referrer, $initial_referrer) are checked
// too.
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

const sent = vi.hoisted(() => {
  vi.stubEnv("VITE_MIXPANEL_TOKEN", "synthetic-project-token");
  vi.stubEnv("VITE_MIXPANEL_HOST", "http://127.0.0.1:9");
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
  });

  const requests = [];
  // Stands in for the browser's XMLHttpRequest: records each request and
  // answers it with success.
  class RecordingXMLHttpRequest {
    constructor() {
      this.withCredentials = false;
      this.readyState = 0;
      this.status = 0;
      this.responseText = "";
      this.headers = {};
    }

    open(method, url) {
      this.method = method;
      this.url = url;
    }

    setRequestHeader(name, value) {
      this.headers[name] = value;
    }

    send(body) {
      requests.push({ method: this.method, url: this.url, body });
      this.readyState = 4;
      this.status = 200;
      this.responseText = "1";
      setTimeout(() => this.onreadystatechange?.(), 0);
    }

    abort() {}
  }
  window.XMLHttpRequest = RecordingXMLHttpRequest;

  // The page was opened from a verification link, so the library stores that
  // link as the first referrer when it starts.
  const page = {
    referrer: `${window.location.origin}/auth/jwt/verify/synthetic-user-7c1d/SYNTHETIC-LINK-CODE-2f9e`,
  };
  Object.defineProperty(document, "referrer", {
    configurable: true,
    get: () => page.referrer,
  });
  return { requests, page };
});

import mixpanel from "mixpanel-browser";
import { Events } from "../EventNames";
import { identifyUser, trackError, trackEvent } from "../mixpanel";

const ORIGIN = window.location.origin;
const USER = "synthetic-user-7c1d";
const CODE = "SYNTHETIC-LINK-CODE-2f9e";
const SSO_VALUE = "SYNTHETIC-SSO-VALUE-81b4";
const PROMPT_TEXT = "SYNTHETIC PROMPT TEXT 5d02";

const TOKEN_ROUTES = [
  [`/auth/jwt/verify/${USER}/${CODE}`, "/auth/jwt/verify/:uuid/:token"],
  [
    `/auth/jwt/invitation/accept/${USER}/${CODE}`,
    "/auth/jwt/invitation/accept/:uuid/:token",
  ],
  [
    `/auth/jwt/invitation/set-password/${USER}/${CODE}`,
    "/auth/jwt/invitation/set-password/:uuid/:token",
  ],
];
const SIGN_IN_LANDING = `/dashboard/develop?sso_token=${SSO_VALUE}&is_new_user=true&tab=all`;

const decodeRequest = ({ body }) => {
  const encoded = decodeURIComponent(String(body).replace(/^data=/, ""));
  const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
};

// Lets the library's request queues send what they hold, then returns every
// request sent since the last call, decoded, by endpoint.
const flushSentRequests = async () => {
  await vi.advanceTimersByTimeAsync(11000);
  const requests = sent.requests.map((request) => ({
    url: request.url,
    items: [decodeRequest(request)].flat(),
  }));
  sent.requests.length = 0;
  return requests;
};

const flushSentEvents = async () =>
  (await flushSentRequests())
    .filter((request) => request.url.includes("/track"))
    .flatMap((request) => request.items);

const sentEvent = (events, name) => {
  const matching = events.filter((event) => event.event === name);
  expect(matching).toHaveLength(1);
  return matching[0];
};

const visit = (path, referringUrl = "") => {
  window.history.replaceState({}, "", path);
  sent.page.referrer = referringUrl;
};

describe("properties sent by mixpanel-browser", () => {
  beforeEach(async () => {
    await flushSentEvents();
  });

  afterEach(() => {
    visit("/");
  });

  afterAll(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  it.each(TOKEN_ROUTES)(
    "reports %s by its route pattern in the URL properties of trackEvent events",
    async (path, pattern) => {
      visit(path, `${ORIGIN}${path}`);

      trackEvent("Synthetic page event", { count: 1 });

      const { properties } = sentEvent(
        await flushSentEvents(),
        "Synthetic page event",
      );
      expect(properties.$current_url).toBe(`${ORIGIN}${pattern}`);
      expect(properties.$referrer).toBe(`${ORIGIN}${pattern}`);
      expect(properties.count).toBe(1);
      expect(JSON.stringify(properties)).not.toContain(CODE);
      expect(JSON.stringify(properties)).not.toContain(USER);
    },
  );

  it.each(TOKEN_ROUTES)(
    "reports %s by its route pattern in trackError events",
    async (path, pattern) => {
      visit(path, `${ORIGIN}${path}`);

      trackError({ error: "synthetic failure" });

      const { properties } = sentEvent(
        await flushSentEvents(),
        `Client Error : ${pattern}`,
      );
      expect(properties.$current_url).toBe(`${ORIGIN}${pattern}`);
      expect(properties.$referrer).toBe(`${ORIGIN}${pattern}`);
      expect(properties.error).toBe("synthetic failure");
      expect(JSON.stringify(properties)).not.toContain(CODE);
      expect(JSON.stringify(properties)).not.toContain(USER);
    },
  );

  it("leaves sign-in parameters out of the URL properties after a sign-in landing", async () => {
    visit(SIGN_IN_LANDING, `${ORIGIN}${SIGN_IN_LANDING}`);

    trackEvent("Synthetic landing event", { count: 2 });
    trackError({ error: "synthetic failure" });

    const events = await flushSentEvents();
    const expectedUrl = `${ORIGIN}/dashboard/develop?tab=all`;
    for (const name of [
      "Synthetic landing event",
      "Client Error : /dashboard/develop",
    ]) {
      const { properties } = sentEvent(events, name);
      expect(properties.$current_url).toBe(expectedUrl);
      expect(properties.$referrer).toBe(expectedUrl);
      expect(JSON.stringify(properties)).not.toContain(SSO_VALUE);
      expect(JSON.stringify(properties)).not.toContain("is_new_user");
    }
  });

  it("names a client error by the route pattern when the caller passes the page URL", async () => {
    visit("/dashboard/develop");

    trackError(
      { error: "synthetic failure" },
      `${ORIGIN}/auth/jwt/verify/${USER}/${CODE}?sso_token=${SSO_VALUE}`,
    );

    const events = await flushSentEvents();
    sentEvent(events, `Client Error : ${ORIGIN}/auth/jwt/verify/:uuid/:token`);
    expect(JSON.stringify(events)).not.toContain(CODE);
    expect(JSON.stringify(events)).not.toContain(SSO_VALUE);
  });

  it("sends an ordinary page's URL and referrer unchanged", async () => {
    const path = "/dashboard/develop/dataset-1?tab=data&page=2";
    visit(path, "https://docs.example.test/guide?step=3");

    trackEvent("Synthetic ordinary event", { count: 3 });
    trackError({ error: "synthetic failure" });

    const events = await flushSentEvents();
    for (const name of [
      "Synthetic ordinary event",
      "Client Error : /dashboard/develop/dataset-1",
    ]) {
      const { properties } = sentEvent(events, name);
      expect(properties.$current_url).toBe(`${ORIGIN}${path}`);
      expect(properties.$referrer).toBe(
        "https://docs.example.test/guide?step=3",
      );
    }
  });

  it("keeps the first referrer it stores free of link codes", async () => {
    // The library sends the first referrer it stored with every later event
    // as $initial_referrer.
    expect(mixpanel.get_property("$initial_referrer")).not.toContain(CODE);

    visit("/dashboard/develop", "");
    trackEvent("Synthetic later event", { count: 4 });

    const { properties } = sentEvent(
      await flushSentEvents(),
      "Synthetic later event",
    );
    expect(properties.$initial_referrer).toBe(
      `${ORIGIN}/auth/jwt/verify/:uuid/:token`,
    );
  });

  it("leaves sign-in parameters and link codes out of the identify event and profile updates", async () => {
    visit(SIGN_IN_LANDING);

    identifyUser({ id: "synthetic-user-id-1", email: "user@example.test" });

    const requests = await flushSentRequests();
    const events = requests
      .filter((request) => request.url.includes("/track"))
      .flatMap((request) => request.items);
    const profileUpdates = requests.filter((request) =>
      request.url.includes("/engage"),
    );
    expect(profileUpdates.length).toBeGreaterThan(0);
    expect(JSON.stringify(profileUpdates)).toContain("$initial_referrer");
    expect(JSON.stringify(profileUpdates)).not.toContain(CODE);
    const { properties } = sentEvent(events, "$identify");
    expect(properties.$current_url).toBe(`${ORIGIN}/dashboard/develop?tab=all`);
    expect(JSON.stringify(requests)).not.toContain(SSO_VALUE);
    expect(JSON.stringify(requests)).not.toContain(CODE);
  });

  it("cleans link codes from campaign values the library adds to events and the profile", async () => {
    const campaign = encodeURIComponent(`${ORIGIN}/shared/${CODE}`);
    visit(`/auth/jwt/login?utm_campaign=${campaign}`);

    trackEvent("Synthetic campaign event", { count: 5 });

    const requests = await flushSentRequests();
    const { properties } = sentEvent(
      requests
        .filter((request) => request.url.includes("/track"))
        .flatMap((request) => request.items),
      "Synthetic campaign event",
    );
    expect(properties.utm_campaign).toBe(`${ORIGIN}/shared/:token`);
    expect(JSON.stringify(requests)).not.toContain(CODE);
  });

  it.each([
    "userPromptEntered",
    "GeneratePromptClicked",
    "promptEdited",
    "generatedPromptApplied",
    "runPromptInitiated",
    "improvePromptApplied",
    "improvementScopeEntered",
    "improvePromptClicked",
  ])(
    "sends nothing for the prompt drawer event %s, which has no event name",
    async (name) => {
      expect(Events[name]).toBeUndefined();

      trackEvent(Events[name], {
        form_fields: { statement: PROMPT_TEXT, newPrompt: PROMPT_TEXT },
      });

      expect(await flushSentEvents()).toEqual([]);
    },
  );

  it("sends the dataset run-prompt event without the prompt text", async () => {
    visit("/dashboard/develop/dataset-1");

    trackEvent(Events.datasetRunpromptRunClicked, {
      id: "dataset-1",
      prompt_template: [PROMPT_TEXT],
      model_name: "synthetic-model",
    });

    const { properties } = sentEvent(
      await flushSentEvents(),
      Events.datasetRunpromptRunClicked,
    );
    expect(properties.id).toBe("dataset-1");
    expect(properties.model_name).toBe("synthetic-model");
    expect(JSON.stringify(properties)).not.toContain(PROMPT_TEXT);
  });
});
