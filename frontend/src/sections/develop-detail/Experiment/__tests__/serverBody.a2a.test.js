// A new A2A agent run reaches the real create route as the server reads it
// (target_type "a2a"; the server saves it as an HTTP source). Reads the
// generated contract: run it after the swagger and frontend contracts are
// regenerated from the serializers that declare the A2A fields.
import { afterEach, describe, expect, it } from "vitest";
import { serverExperimentBody } from "../serverBody";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { definitionSchema } from "src/api/contracts/strictBody";
import { entryOfCard, newRunCard } from "../experimentFormValues";
import { PROMPT_CONFIG_TYPE } from "../constants";

afterEach(() => setLaunchOverrideForTest(null));

const COLUMN = "4f1e0c52-1a2b-4c3d-8e9f-0a1b2c3d4e51";
const a2a = (over = {}) => ({
  target_type: "a2a",
  name: "Support agent · answer",
  a2a: {
    card_url: "https://support.acme.test",
    skill_id: "answer",
    text_column: COLUMN,
    data_columns: [],
    context_column: null,
    auth: { kind: "bearer", secret: "ACME_TOKEN" },
    card: {
      name: "Support agent",
      url: "https://support.acme.test/a2a",
      skills: [
        { id: "answer", name: "Answer", tags: [], inputModes: ["text"] },
      ],
    },
    ...over,
  },
});
const adapt = (entry, isEditing = false) =>
  serverExperimentBody(
    {
      name: "A2A run",
      ...(!isEditing
        ? { dataset_id: "8d3c1f7e-2b6a-4c11-9d0e-5f6a7b8c9d01" }
        : {}),
      prompt_config: [entry],
      user_eval_metrics: [],
    },
    {
      columns: [{ id: COLUMN, name: "question" }],
      isEditing,
      initial: { name: "A2A run" },
    },
  );

describe("A2A agent run on the real create route", () => {
  it("the contract declares the A2A entry the server accepts", () => {
    const entry = definitionSchema("PromptConfigEntry");
    expect(entry.properties.target_type.enum).toContain("a2a");
    expect(entry.properties.a2a).toBeTruthy();
  });

  it("travels as it is, with no block", () => {
    const entry = a2a();
    const result = adapt(entry);
    expect(result.blocks).toEqual([]);
    expect(result.body.prompt_config[0]).toEqual(entry);
  });

  it.each([
    [
      { data_columns: [COLUMN] },
      "Sending columns as a data part isn't available in this release. Clear Data part (JSON) from on the A2A agent run, then run.",
    ],
    [
      {
        auth: {
          kind: "oauth2",
          token_url: "https://a.test/t",
          client_id: "c",
          secret: "S",
        },
      },
      "Basic and OAuth 2 sign-in for A2A agents aren't available in this release. Choose Bearer token, API key header or None, then run.",
    ],
    [
      { auth: { kind: "bearer", secret: "" } },
      "Pick the workspace secret the A2A agent's auth sends, then run.",
    ],
    [
      { card: null },
      "The A2A agent run has no agent card. Discover the agent card, then run.",
    ],
    [
      { text_column: null },
      "Pick the column the A2A agent's message text comes from, then run.",
    ],
  ])(
    "blocks what the server does not send yet, with what to change (%o)",
    (over, text) => {
      const result = adapt(a2a(over));
      expect(result.blocks.map((b) => b.text)).toContain(text);
    },
  );

  it("the entry the A2A card builds after Discover reaches the server with only the keys it takes", () => {
    const card = newRunCard(PROMPT_CONFIG_TYPE.A2A);
    card.a2a = {
      ...card.a2a,
      card_url: "https://support.acme.test",
      // Discover keeps where it found the card, for the card's own header.
      fetched_from: "https://support.acme.test/.well-known/agent-card.json",
      card: a2a().a2a.card,
      skill_id: "answer",
      text_column: COLUMN,
      auth: { kind: "bearer", secret: "ACME_TOKEN" },
    };
    const entry = entryOfCard(card);
    expect(entry.a2a.fetched_from).toBe(
      "https://support.acme.test/.well-known/agent-card.json",
    );
    const result = adapt(entry);
    expect(result.blocks).toEqual([]);
    const sent = result.body.prompt_config[0];
    // The server's A2A serializer refuses any other key with a 400.
    Object.keys(sent).forEach((key) =>
      expect(["id", "name", "target_type", "a2a"]).toContain(key),
    );
    expect(Object.keys(sent.a2a).sort()).toEqual([
      "auth",
      "card",
      "card_url",
      "context_column",
      "data_columns",
      "skill_id",
      "text_column",
    ]);
    // The form's own card keeps it.
    expect(entry.a2a).toHaveProperty("fetched_from");
  });

  it("settings of another run type on an A2A run block Run and never travel", () => {
    const withHttp = adapt({
      ...a2a(),
      http: { url: "https://x.test", method: "POST" },
    });
    expect(withHttp.blocks.map((b) => b.text)).toEqual([
      "This A2A agent run carries settings of another run type, which an A2A agent doesn't take. Remove the run and add the A2A agent again, then run.",
    ]);
    expect(withHttp.body.prompt_config[0]).not.toHaveProperty("http");
    // An empty one is what the server does without it: it stays home, no block.
    const empty = adapt({ ...a2a(), variable_mapping: {} });
    expect(empty.blocks).toEqual([]);
    expect(empty.body.prompt_config[0]).not.toHaveProperty("variable_mapping");
    // Per-run limits keep their own line.
    const limited = adapt({ ...a2a(), limits: { tries: 3 } });
    expect(limited.blocks.map((b) => b.text)).toEqual([
      "Per-run limits (tries, cost cap) aren't supported. Clear the run's limits to run.",
    ]);
    expect(limited.body.prompt_config[0]).not.toHaveProperty("limits");
  });

  it("an edit still blocks an A2A run: saved runs come back as HTTP sources", () => {
    const result = adapt(a2a(), true);
    expect(result.blocks.map((b) => b.text)).toContain(
      "A2A agent runs aren't supported here. Remove that run, or use a prompt or an agent.",
    );
  });
});
