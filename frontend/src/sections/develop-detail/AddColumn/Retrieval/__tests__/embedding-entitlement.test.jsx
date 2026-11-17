// R1-04 (one key): the Retrieval embedding selector disabled OpenAI and
// Hugging Face whenever provider status said hasKey=false, so an org whose
// embeddings are covered by Future AGI credits could not pick them. A type is
// now enabled by its managed-or-BYOK entitlement (the model-list item's
// credential_mode, ONEKEY-ASSUMED-CONTRACT-r1). Without a verdict the old
// provider-key rule applies unchanged, so BYOK orgs see exactly what they saw.
// Fixtures are contract-shaped server answers; no credential is involved.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";

const state = vi.hoisted(() => ({
  providers: [],
  access: undefined,
  options: null,
}));

// Keep the real eligibility adapter without importing the application's HTTP
// client (and its unrelated generated-contract initialization).
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { develop: { modelList: "/model-hub/api/models_list/" } },
}));

vi.mock("src/api/model/api-keys", () => ({
  useApiKeysStatus: () => ({ data: state.providers }),
}));
vi.mock(
  "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/components/FromSearchSelectField", () => ({
  FormSearchSelectFieldControl: ({ options }) => {
    state.options = options;
    return null;
  },
}));
vi.mock(
  "src/sections/develop-detail/Common/modelAccess",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEmbeddingAccess: () => ({ data: state.access, isLoading: false }),
  }),
);

import EmbeddingConfigField from "../EmbeddingConfigField";

const status = (openai, huggingface) => [
  { provider: "openai", hasKey: openai },
  { provider: "huggingface", hasKey: huggingface },
];
const listed = (type, model, over) => ({
  model_name: model,
  providers: type,
  is_available: true,
  type: "embedding",
  ...over,
});
const openai = (over) => listed("openai", "text-embedding-3-small", over);
const huggingface = (over) => listed("huggingface", "all-mpnet-base-v2", over);
const MANAGED = { credential_mode: "managed", payer: "future_agi_credits" };
const BYOK = { credential_mode: "byok", payer: "own_provider_key" };
const UNAVAILABLE = {
  credential_mode: "unavailable",
  payer: null,
  is_available: false,
};

// What the selector offered before one key, for a given provider status.
// Sentence Transformers is never selectable: model serving loads no model for
// it (W10-EXPERIMENTS-DATASETS-U02).
const before = (openaiDisabled, huggingfaceDisabled) => [
  { value: "openai", label: "Open AI", disabled: openaiDisabled },
  { value: "huggingface", label: "Hugging Face", disabled: huggingfaceDisabled },
  {
    value: "sentence_transformers",
    label: "Sentence Transformers",
    disabled: true,
  },
];
const option = (value) => state.options.find((o) => o.value === value);

const FieldHarness = ({ type }) => {
  const { control } = useForm({
    defaultValues: { embeddingConfig: { type, model: "" } },
  });
  return <EmbeddingConfigField control={control} />;
};
FieldHarness.propTypes = { type: PropTypes.string };

const show = ({ providers, access, type = "" }) => {
  state.providers = providers;
  state.access = access;
  render(<FieldHarness type={type} />);
};

beforeEach(() => {
  state.providers = [];
  state.access = undefined;
  state.options = null;
});

describe("managed embedding entitlement enables a type without a provider key", () => {
  it("OpenAI covered by Future AGI credits is selectable and says so", () => {
    show({
      providers: status(false, false),
      access: { openai: openai(MANAGED), huggingface: null },
    });
    expect(option("openai")).toEqual({
      value: "openai",
      label: "Open AI",
      disabled: true,
    });
    expect(option("huggingface").disabled).toBe(true);
  });

  it("Hugging Face covered by Future AGI credits is selectable", () => {
    show({
      providers: status(false, false),
      access: { openai: null, huggingface: huggingface(MANAGED) },
    });
    expect(option("huggingface")).toEqual({
      value: "huggingface",
      label: "Hugging Face",
      disabled: true,
    });
    expect(option("openai").disabled).toBe(true);
  });

  it("names the payer under the field when a managed type is picked", () => {
    show({
      providers: status(false, false),
      access: { openai: openai(MANAGED), huggingface: null },
      type: "openai",
    });
    expect(screen.queryByTestId("embedding-payer-hint")).toBeNull();
    const hint = screen.getByTestId("embedding-managed-disclosure");
    expect(hint).toHaveTextContent(
      "Future AGI credits do not pay for embeddings. Add your own provider key.",
    );
    expect(hint).not.toHaveTextContent(/\byet\b/);
  });
});

describe("the server's verdict wins over a stale provider status", () => {
  it("credential_mode byok enables the type with its usual label", () => {
    show({
      providers: status(false, true),
      access: { openai: openai(BYOK), huggingface: null },
      type: "openai",
    });
    expect(option("openai")).toEqual({
      value: "openai",
      label: "Open AI",
      disabled: false,
    });
    expect(screen.queryByTestId("embedding-payer-hint")).toBeNull();
  });

  it("credential_mode unavailable disables the type even with a key row", () => {
    show({
      providers: status(true, true),
      access: { openai: openai(UNAVAILABLE), huggingface: null },
    });
    expect(option("openai").disabled).toBe(true);
    expect(option("huggingface").disabled).toBe(false);
  });
});

describe("BYOK regression: without a one-key verdict nothing changes", () => {
  it.each([
    ["no verdict yet", undefined],
    ["no listed embedding item", { openai: null, huggingface: null }],
    [
      "a server without the one-key fields",
      { openai: openai(), huggingface: huggingface({ is_available: false }) },
    ],
    [
      "a credential_mode that is not a contract value",
      {
        openai: openai({ credential_mode: "trial" }),
        huggingface: huggingface({ credential_mode: "trial" }),
      },
    ],
  ])("%s: the provider-key rule, as before", (_why, access) => {
    for (const [openaiKey, hfKey] of [
      [true, true],
      [true, false],
      [false, true],
      [false, false],
    ]) {
      state.options = null;
      show({ providers: status(openaiKey, hfKey), access });
      const unknownMode = _why === "a credential_mode that is not a contract value";
      expect(state.options).toEqual(unknownMode ? before(true, true) : before(!openaiKey, !hfKey));
    }
  });

  it("a provider missing from the status list stays selectable, as before", () => {
    show({ providers: [], access: undefined });
    expect(state.options).toEqual(before(false, false));
  });

  it("shows no payer hint for a BYOK pick", () => {
    show({ providers: status(true, true), access: undefined, type: "openai" });
    expect(screen.queryByTestId("embedding-payer-hint")).toBeNull();
  });
});
