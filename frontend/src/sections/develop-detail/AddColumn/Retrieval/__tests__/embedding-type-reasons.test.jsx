// W10-EXPERIMENTS-DATASETS-U02: the Retrieval embedding Type list greyed out
// Open AI (and Hugging Face) without saying why, while Sentence Transformers,
// which model serving cannot load (it answers 404 for every model), was the
// one type never disabled. The form told the user to configure a provider key
// but offered no way to do it, although the key dialog was mounted. Every
// type the user cannot pick now says why, before it is picked, and the form
// opens the key dialog. Fixtures are contract-shaped server answers; no
// credential is involved.
import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";

const state = vi.hoisted(() => ({
  providers: [],
  access: undefined,
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
  "src/sections/develop-detail/Common/modelAccess",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEmbeddingAccess: () => ({ data: state.access, isLoading: false }),
  }),
);
// The Type list as the user meets it: each option with its enabled state.
vi.mock("src/components/FromSearchSelectField", () => {
  const TypeList = ({ label, options }) => (
    <ul role="listbox" aria-label={label}>
      {options.map((o) => (
        <li
          key={o.value}
          role="option"
          aria-selected={false}
          aria-disabled={Boolean(o.disabled)}
        >
          {o.label}
        </li>
      ))}
    </ul>
  );
  TypeList.propTypes = {
    label: PropTypes.string,
    options: PropTypes.arrayOf(PropTypes.object),
  };
  return { FormSearchSelectFieldControl: TypeList };
});
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/Common/ConfigureKeys/ConfigureKeys",
  () => ({
    default: ({ open }) =>
      open ? <div role="dialog" aria-label="API Key Configuration" /> : null,
  }),
);

import EmbeddingConfigField from "../EmbeddingConfigField";

const status = (openai, huggingface) => [
  { provider: "openai", hasKey: openai },
  { provider: "huggingface", hasKey: huggingface },
];
const UNAVAILABLE_OPENAI = {
  model_name: "text-embedding-3-small",
  providers: "openai",
  type: "embedding",
  credential_mode: "unavailable",
  payer: null,
  is_available: false,
};

const ST_REASON =
  "Sentence Transformers embeddings are not available on this server. Use Hugging Face instead.";

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
const typeOption = (name) => screen.getByRole("option", { name });

beforeEach(() => {
  state.providers = [];
  state.access = undefined;
});

describe("Sentence Transformers is never offered as a working type", () => {
  it("cannot be picked and says why before it is picked", () => {
    show({ providers: status(true, true), access: undefined });
    expect(typeOption("Sentence Transformers")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByText(ST_REASON)).toBeInTheDocument();
  });

  it("leaves the provider types enabled, with no reason, when every key is present", () => {
    show({ providers: status(true, true), access: undefined });
    expect(typeOption("Open AI")).toHaveAttribute("aria-disabled", "false");
    expect(typeOption("Hugging Face")).toHaveAttribute(
      "aria-disabled",
      "false",
    );
    expect(screen.queryByText(/API key to use/)).toBeNull();
  });

  it("says it once when a saved column already uses it", () => {
    show({
      providers: status(true, true),
      access: undefined,
      type: "sentence_transformers",
    });
    expect(screen.getAllByText(/not available on this server/)).toHaveLength(
      1,
    );
  });
});

describe("a disabled provider type says why", () => {
  it("Open AI without a key names the key to add", () => {
    show({ providers: status(false, true), access: undefined });
    expect(typeOption("Open AI")).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("Add your OpenAI API key to use Open AI."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Hugging Face API key/)).toBeNull();
  });

  it("Hugging Face without a key names the key to add", () => {
    show({ providers: status(true, false), access: undefined });
    expect(typeOption("Hugging Face")).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(
      screen.getByText("Add your Hugging Face API key to use Hugging Face."),
    ).toBeInTheDocument();
  });

  it("a type the server marks unavailable does not ask for a key", () => {
    show({
      providers: status(true, true),
      access: { openai: UNAVAILABLE_OPENAI, huggingface: null },
    });
    expect(typeOption("Open AI")).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("Open AI is not available for this workspace."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/OpenAI API key/)).toBeNull();
  });
});

describe("the form opens the key dialog it asks the user to use", () => {
  it("Configure API keys opens the key dialog", () => {
    show({ providers: status(false, false), access: undefined });
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Configure API keys" }));
    expect(
      screen.getByRole("dialog", { name: "API Key Configuration" }),
    ).toBeInTheDocument();
  });

  it("the key disclosure carries the action", () => {
    show({ providers: status(false, false), access: undefined });
    const disclosure = screen.getByTestId("embedding-managed-disclosure");
    expect(disclosure.textContent).toBe(
      "Future AGI credits do not pay for embeddings. Add your own provider key. Configure API keys",
    );
    expect(disclosure).toContainElement(
      screen.getByRole("button", { name: "Configure API keys" }),
    );
  });
});
