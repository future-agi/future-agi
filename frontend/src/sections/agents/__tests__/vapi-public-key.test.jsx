import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { fireEvent, render, screen } from "src/utils/test-utils";
import VapiPublicKeyField from "../AgentConfiguration/VapiPublicKeyField";
import { getAgentFormValues } from "../common";
import { useAgentSubmit } from "../useAgentConfigForm";

const axiosMocks = vi.hoisted(() => ({
  post: vi.fn(),
  createVersion: vi.fn((id) => `/simulate/agent-definitions/${id}/versions/`),
}));

vi.mock("src/utils/axios", () => ({
  default: { post: axiosMocks.post },
  endpoints: {
    agentDefinitions: { createVersion: axiosMocks.createVersion },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  Events: {},
  PropertyName: {},
}));

const typed = "made-up-vapi-public-key-typed";

function Harness({ values, onValues }) {
  const { control, setValue, getValues } = useForm({ defaultValues: values });
  return (
    <>
      <VapiPublicKeyField control={control} setValue={setValue} />
      <button onClick={() => onValues(getValues())}>Read form</button>
    </>
  );
}

Harness.propTypes = {
  values: PropTypes.object.isRequired,
  onValues: PropTypes.func.isRequired,
};

const unsaved = {
  vapiPublicKey: "",
  removeVapiPublicKey: false,
  hasVapiPublicKey: false,
};
const saved = { ...unsaved, hasVapiPublicKey: true };

describe("Vapi public key field", () => {
  it("is a password field labelled Vapi public key", () => {
    render(<Harness values={unsaved} onValues={vi.fn()} />);

    const input = screen.getByLabelText("Vapi public key");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue("");
    expect(screen.queryByText("Remove saved key")).not.toBeInTheDocument();
  });

  it("shows the saved state and never a value", () => {
    render(<Harness values={saved} onValues={vi.fn()} />);

    expect(
      screen.getByText("Saved (leave blank to keep it)"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Vapi public key")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain("enc::");
  });

  it("asks for the saved key to be removed", () => {
    let seen;
    render(
      <Harness
        values={saved}
        onValues={(value) => {
          seen = value;
        }}
      />,
    );

    fireEvent.click(screen.getByText("Remove saved key"));
    fireEvent.click(screen.getByText("Read form"));

    expect(seen.removeVapiPublicKey).toBe(true);
    expect(seen.vapiPublicKey).toBe("");
    expect(
      screen.getByText("The saved key is removed on save."),
    ).toBeInTheDocument();
  });

  it("typing a new key cancels a pending removal", () => {
    let seen;
    render(
      <Harness
        values={{ ...saved, removeVapiPublicKey: true }}
        onValues={(value) => {
          seen = value;
        }}
      />,
    );

    fireEvent.change(screen.getByLabelText("Vapi public key"), {
      target: { value: typed },
    });
    fireEvent.click(screen.getByText("Read form"));

    expect(seen.removeVapiPublicKey).toBe(false);
    expect(seen.vapiPublicKey).toBe(typed);
  });
});

describe("agent form values", () => {
  it("carry only whether a Vapi public key is saved", () => {
    const values = getAgentFormValues({
      configuration_snapshot: { provider: "vapi", has_vapi_public_key: true },
    });

    expect(values.hasVapiPublicKey).toBe(true);
    expect(values.vapiPublicKey).toBe("");
    expect(values.removeVapiPublicKey).toBe(false);
    expect(getAgentFormValues({}).hasVapiPublicKey).toBe(false);
  });
});

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  function Wrapper({ children }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }

  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
}

const voiceAgent = {
  agentType: "voice",
  agentName: "Support agent",
  languages: ["en"],
  provider: "vapi",
  apiKey: "made-up-private-key",
  assistantId: "asst_1",
  description: "desc",
  commitMessage: "msg",
  inbound: true,
  observabilityEnabled: false,
  headers: [],
  voiceTransport: "webrtc",
  contactNumber: "",
  vapiPublicKey: "",
  removeVapiPublicKey: false,
  hasVapiPublicKey: false,
};

describe("saving the Vapi public key", () => {
  let reset;

  function renderSubmit() {
    reset = vi.fn();
    return renderHook(
      () =>
        useAgentSubmit({
          agentDefinitionId: "agent-1",
          reset,
          queryClient: { invalidateQueries: vi.fn() },
          enqueueSnackbar: vi.fn(),
          setError: vi.fn(),
          setSelectedVersion: vi.fn(),
        }),
      { wrapper: createWrapper() },
    );
  }

  beforeEach(() => {
    vi.clearAllMocks();
    axiosMocks.post.mockResolvedValue({ data: { version: { id: "v1" } } });
  });

  it("sends a typed key as vapi_public_key", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({
      ...voiceAgent,
      vapiPublicKey: ` ${typed} `,
    });

    const [, payload] = axiosMocks.post.mock.calls[0];
    expect(payload.vapi_public_key).toBe(typed);
  });

  it("omits a blank key so the saved key is kept", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({ ...voiceAgent, hasVapiPublicKey: true });

    const [, payload] = axiosMocks.post.mock.calls[0];
    expect(payload).not.toHaveProperty("vapi_public_key");
  });

  it("sends null when the saved key is removed", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({
      ...voiceAgent,
      hasVapiPublicKey: true,
      removeVapiPublicKey: true,
    });

    const [, payload] = axiosMocks.post.mock.calls[0];
    expect(payload).toHaveProperty("vapi_public_key", null);
  });

  it("sends no key for another provider", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({
      ...voiceAgent,
      provider: "retell",
      vapiPublicKey: typed,
    });

    const [, payload] = axiosMocks.post.mock.calls[0];
    expect(payload).not.toHaveProperty("vapi_public_key");
  });

  it("does not keep the typed key in the form after saving", async () => {
    const { result } = renderSubmit();

    await result.current.onSubmit({ ...voiceAgent, vapiPublicKey: typed });

    const [saved] = reset.mock.calls[0];
    expect(saved.vapiPublicKey).toBe("");
    expect(saved.hasVapiPublicKey).toBe(true);
    expect(JSON.stringify(saved)).not.toContain(typed);
  });
});
