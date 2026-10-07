import { describe, it, expect } from "vitest";

import {
  buildConfigurationBody,
  displayConfigValue,
  needsRebuild,
  ownConfigKeys,
  showsSpeaksFirst,
  textToVariables,
  validateKeyName,
  variablesToText,
} from "../configurationEdit";

const CONFIG = {
  assistant_id: "asst_1",
  inbound: true,
  phone_number: "+14155550100",
  target_speaks_first: false,
  dynamic_variables: { tier: "gold", seats: 3, vip: true },
};

const empty = (overrides = {}) => ({
  config: CONFIG,
  secretDrafts: {},
  addedSecrets: {},
  connectionDrafts: {},
  speaksFirst: undefined,
  variables: { ...CONFIG.dynamic_variables },
  ...overrides,
});

describe("buildConfigurationBody", () => {
  it("returns nothing when nothing changed", () => {
    expect(buildConfigurationBody(empty())).toBeNull();
  });

  it("sends a replaced key and ignores a replace left blank", () => {
    const body = buildConfigurationBody(
      empty({ secretDrafts: { DEEPGRAM_API_KEY: "new", VAPI_API_KEY: "  " } }),
    );
    expect(body).toEqual({ environment_values: { DEEPGRAM_API_KEY: "new" } });
  });

  it("sends added keys with the replaced ones", () => {
    const body = buildConfigurationBody(
      empty({
        secretDrafts: { DEEPGRAM_API_KEY: "d-1" },
        addedSecrets: { GEMINI_API_KEY: "g-1" },
      }),
    );
    expect(body).toEqual({
      environment_values: { DEEPGRAM_API_KEY: "d-1", GEMINI_API_KEY: "g-1" },
    });
  });

  it("sends a connection setting only when it differs from the saved one", () => {
    expect(
      buildConfigurationBody(
        empty({ connectionDrafts: { phone_number: "+14155550100" } }),
      ),
    ).toBeNull();
    expect(
      buildConfigurationBody(
        empty({ connectionDrafts: { phone_number: "+14155550199" } }),
      ),
    ).toEqual({ config: { phone_number: "+14155550199" } });
  });

  it("sends a LiveKit URL saved upper-case under the name the API takes", () => {
    const body = buildConfigurationBody(
      empty({
        config: { LIVEKIT_URL: "wss://old.livekit.cloud" },
        variables: {},
        connectionDrafts: { LIVEKIT_URL: "wss://new.livekit.cloud" },
      }),
    );
    expect(body).toEqual({
      config: { livekit_url: "wss://new.livekit.cloud" },
    });
  });

  it("sends who speaks first only when it changed", () => {
    expect(buildConfigurationBody(empty({ speaksFirst: false }))).toBeNull();
    expect(buildConfigurationBody(empty({ speaksFirst: true }))).toEqual({
      config: { target_speaks_first: true },
    });
  });

  it("sends the whole variable set when it changed", () => {
    expect(
      buildConfigurationBody(
        empty({ variables: { tier: "silver", seats: 3 } }),
      ),
    ).toEqual({ config: { dynamic_variables: { tier: "silver", seats: 3 } } });
  });

  it("sends no variables while the text cannot be read", () => {
    expect(buildConfigurationBody(empty({ variables: undefined }))).toBeNull();
  });
});

describe("dynamic variables as text", () => {
  it("writes one KEY=value per line", () => {
    expect(variablesToText({ tier: "gold", seats: 3 })).toBe(
      "tier=gold\nseats=3",
    );
    expect(variablesToText(undefined)).toBe("");
  });

  it("reads lines back, keeping an untouched value's type", () => {
    expect(
      textToVariables("tier=silver\n\n seats = 3 \nnote=a=b", {
        seats: 3,
        tier: "gold",
      }),
    ).toEqual({ variables: { tier: "silver", seats: 3, note: "a=b" } });
  });

  it("refuses a line without a key", () => {
    expect(textToVariables("tier=gold\njustavalue")).toEqual({
      error: "Line 2 needs the form KEY=value.",
    });
    expect(textToVariables("=gold")).toEqual({
      error: "Line 1 needs the form KEY=value.",
    });
  });

  it("refuses a repeated key", () => {
    expect(textToVariables("tier=gold\ntier=silver")).toEqual({
      error: "tier appears more than once.",
    });
  });
});

describe("validateKeyName", () => {
  it.each([
    ["", "Enter a key name."],
    ["1KEY", "Use letters, digits and underscores, not starting with a digit."],
    ["SIMULATOR_DEEPGRAM_API_KEY", "SIMULATOR_ names are reserved."],
    [
      "DEEPGRAM_API_KEY",
      "DEEPGRAM_API_KEY is already set. Use the pencil to replace it.",
    ],
  ])("refuses %j", (name, message) => {
    expect(validateKeyName(name, ["DEEPGRAM_API_KEY"])).toBe(message);
  });

  it("accepts a new upper-snake name", () => {
    expect(validateKeyName("gemini_api_key", ["DEEPGRAM_API_KEY"])).toBeNull();
  });
});

describe("own config rows", () => {
  it("lists every own setting except the user's variables", () => {
    expect(ownConfigKeys(CONFIG, "vapi")).toEqual([
      "assistant_id",
      "inbound",
      "phone_number",
      "target_speaks_first",
    ]);
  });

  it("adds who-speaks-first for a voice agent that never set it", () => {
    expect(ownConfigKeys({ assistant_id: "a" }, "vapi")).toEqual([
      "assistant_id",
      "target_speaks_first",
    ]);
    expect(ownConfigKeys({ agent_id: "a" }, "retell_chat")).toEqual([
      "agent_id",
    ]);
  });

  it("marks what needs a rebuild", () => {
    expect(needsRebuild("assistant_id")).toBe(true);
    expect(needsRebuild("inbound")).toBe(true);
    expect(needsRebuild("target_system_prompt")).toBe(true);
    expect(needsRebuild("phone_number")).toBe(false);
    expect(needsRebuild("target_speaks_first")).toBe(false);
  });

  it("offers who speaks first on voice connectors only", () => {
    expect(showsSpeaksFirst("vapi")).toBe(true);
    expect(showsSpeaksFirst("retell_chat")).toBe(false);
  });

  it("shows an object value as JSON instead of [object Object]", () => {
    expect(displayConfigValue({ tier: "gold" })).toBe('{"tier":"gold"}');
    expect(displayConfigValue(3)).toBe("3");
  });
});
