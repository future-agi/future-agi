import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import EmptyState from "src/components/empty-state";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import GatewayEmptyState from "../../components/GatewayEmptyState";
import { ACCESS } from "../catalogRows";
import { copyText } from "../copyText";
import ModelApi from "./ModelApi";

const keyState = vi.hoisted(() => ({ value: "" }));
vi.mock("../../overview/GatewaySnippetKey", () => ({
  default: ({ children }) => children(keyState.value),
}));
vi.mock("../copyText", () => ({ copyText: vi.fn() }));

const theme = createTheme({ palette: palette("light"), typography });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  keyState.value = "";
});

function renderThemed(ui) {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>{ui}</MemoryRouter>
    </ThemeProvider>,
  );
}

// A model no route reaches: not sold on Future AGI credits and no provider
// of the organization serves it, so the API tab has no request to copy.
const unsoldRow = {
  id: "acme/unpriced-chat",
  type: "chat",
  access: ACCESS.NONE,
  ownRoutes: [],
  accessReason: "Not available on Future AGI credits: unpriced.",
};

describe("EmptyLayout title element", () => {
  it("keeps the title a div, not a heading, when no titleComponent is given", () => {
    renderThemed(
      <EmptyLayout title="Nothing here yet" description="Add one first." />,
    );
    expect(screen.getByText("Nothing here yet", { exact: true }).tagName).toBe(
      "DIV",
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("keeps the gateway empty state's title a div by default", () => {
    renderThemed(<GatewayEmptyState title="No keys yet" />);
    expect(screen.getByText("No keys yet", { exact: true }).tagName).toBe(
      "DIV",
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("changes only the element, never the title's typography, for an h2", () => {
    const { unmount } = renderThemed(<EmptyLayout title="Nothing here yet" />);
    const divClass = screen.getByText("Nothing here yet").className;
    unmount();
    renderThemed(<EmptyLayout title="Nothing here yet" titleComponent="h2" />);
    const heading = screen.getByRole("heading", {
      level: 2,
      name: "Nothing here yet",
    });
    expect(heading.tagName).toBe("H2");
    expect(heading.className).toBe(divClass);
  });

  it.each(["div", "h3"])(
    "honours an explicit %s titleComponent",
    (component) => {
      renderThemed(
        <EmptyLayout title="Nothing here yet" titleComponent={component} />,
      );
      expect(
        screen.getByText("Nothing here yet", { exact: true }).tagName,
      ).toBe(component.toUpperCase());
      expect(
        screen.queryByRole("heading", { level: 2 }),
      ).not.toBeInTheDocument();
      if (component === "h3") {
        expect(
          screen.getByRole("heading", { level: 3, name: "Nothing here yet" }),
        ).toBeInTheDocument();
      } else {
        expect(screen.queryByRole("heading")).not.toBeInTheDocument();
      }
    },
  );

  it("honours a non-h2 titleComponent forwarded by GatewayEmptyState", () => {
    renderThemed(<GatewayEmptyState title="No keys yet" titleComponent="h3" />);
    expect(
      screen.getByRole("heading", { level: 3, name: "No keys yet" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2 })).not.toBeInTheDocument();
  });

  it("keeps the native DS level-2 heading for direct EmptyState consumers", () => {
    renderThemed(<EmptyState title="Direct DS empty state" />);
    expect(
      screen.getByRole("heading", { level: 2, name: "Direct DS empty state" }),
    ).toBeInTheDocument();
  });
});

describe("EmptyLayout icon suppression", () => {
  it.each([undefined, "/assets/icons/empty-state-test.svg"])(
    "removes the entire icon tile with hideIcon and icon=%s",
    (icon) => {
      const { container, unmount } = renderThemed(
        <EmptyLayout title="Nothing here yet" icon={icon} />,
      );
      const visibleTitleGroup =
        screen.getByText("Nothing here yet").parentElement;
      expect(visibleTitleGroup.parentElement.children).toHaveLength(2);
      expect(visibleTitleGroup.parentElement.firstElementChild).not.toBe(
        visibleTitleGroup,
      );
      if (icon) {
        expect(container.querySelector(".svg-color")).toBeInTheDocument();
      }
      unmount();

      const { container: hiddenContainer } = renderThemed(
        <EmptyLayout title="Nothing here yet" icon={icon} hideIcon />,
      );
      const hiddenTitleGroup =
        screen.getByText("Nothing here yet").parentElement;
      expect(hiddenTitleGroup.parentElement.children).toHaveLength(1);
      expect(hiddenTitleGroup.parentElement.firstElementChild).toBe(
        hiddenTitleGroup,
      );
      expect(
        hiddenContainer.querySelector(".svg-color"),
      ).not.toBeInTheDocument();
    },
  );

  it("retains description, action, docs, steps and quickstart without an icon", () => {
    renderThemed(
      <EmptyLayout
        title="Nothing here yet"
        description="Add one first."
        hideIcon
        action={<button type="button">Create item</button>}
        link="/docs/gateway"
        linkText="Read the docs"
        steps={[{ title: "Create a key", description: "Then send a request." }]}
        quickstart={{
          title: "Send from code",
          snippets: [{ label: "Shell", code: "printf 'hello'" }],
        }}
      />,
    );
    expect(screen.getByText("Add one first.")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create item" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the docs" })).toHaveAttribute(
      "href",
      "/docs/gateway",
    );
    expect(screen.getByRole("listitem")).toHaveTextContent("Create a key");
    expect(screen.getByText("Then send a request.")).toBeInTheDocument();
    expect(screen.getByText("Send from code")).toBeInTheDocument();
    expect(screen.getByText("printf 'hello'")).toBeInTheDocument();
  });
});

describe("model API tab: a model with no request to copy", () => {
  it("titles its empty state as a level-2 heading, like its sold sibling", () => {
    renderThemed(
      <ModelApi row={unsoldRow} baseUrl="http://localhost:28490/v1" />,
    );
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No request to copy for this model",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Call this model" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Go to Providers" }),
    ).toBeInTheDocument();
  });
});

// The model page holds no submitted request until Try it sends one (its
// state starts null), so a chat model without a call target must render its
// empty state for both a missing and an explicitly null snapshot.
const noTargetChatRows = [
  [
    "blocked",
    {
      id: "acme/blocked-chat",
      type: "chat",
      access: ACCESS.NONE,
      providerAccessState: "ready",
      ownRoutes: [],
      onCredits: false,
      accessReason:
        "Not available on Future AGI credits: supported pricing is unavailable. Use your own provider key.",
    },
    "No request to copy for this model",
    "Go to Providers",
  ],
  [
    "unverified",
    {
      id: "acme/unverified-chat",
      type: "chat",
      access: ACCESS.NONE,
      providerAccessState: "pending",
      ownRoutes: [],
      accessReason:
        "Own-provider access is not verified. Retry the provider read before using an own-key route.",
    },
    "Own-provider access is not verified",
    "Review Providers",
  ],
  [
    "pending-access",
    {
      id: "example-provider/pending-chat",
      type: "chat",
      access: ACCESS.UNKNOWN,
      providerAccessState: "pending",
      ownRoutes: [{ sendAs: "example-provider/pending-chat", providerId: "example-provider" }],
    },
    "Own-provider access is not verified",
    "Review Providers",
  ],
  [
    "error-access",
    {
      id: "example-provider/error-chat",
      type: "chat",
      access: ACCESS.UNKNOWN,
      providerAccessState: "error",
      ownRoutes: [{ sendAs: "example-provider/error-chat", providerId: "example-provider" }],
    },
    "Own-provider access is not verified",
    "Review Providers",
  ],
];

describe("model API tab: a chat model with no call target and no submitted request", () => {
  it.each(
    noTargetChatRows.flatMap(([kind, row, title, action]) => [
      [kind, "missing", row, undefined, title, action],
      [kind, "null", row, null, title, action],
    ]),
  )(
    "renders the %s chat model's empty state with a %s request snapshot",
    (_kind, _snapshotLabel, row, requestSnapshot, title, action) => {
      const props = {
        row,
        baseUrl: "https://gateway.example.test/v1",
        hosted: true,
        deploymentModeConfirmed: true,
      };
      if (requestSnapshot !== undefined) props.requestSnapshot = requestSnapshot;
      renderThemed(<ModelApi {...props} />);
      expect(
        screen.getByRole("heading", { level: 2, name: title }),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: action })).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "Call this model" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Copy code" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/using your submitted request/i)).toBeNull();
      expect(document.querySelector("pre")).toBeNull();
      expect(document.body.textContent).not.toMatch(/undefined|null/);
    },
  );
});

const ownRow = {
  id: "example-provider/example-model",
  type: "chat",
  access: ACCESS.OWN_KEY,
  managedOn: false,
  ownRoutes: [{ sendAs: "example-provider/example-model", providerName: "Example" }],
};
const apiProps = {
  baseUrl: "https://gateway.example.test/v1",
  hosted: true,
  deploymentModeConfirmed: true,
  managedOffered: false,
};
const requestCode = () => document.querySelector("pre code")?.textContent || "";

async function chooseKind(name) {
  await userEvent.click(screen.getByRole("combobox", { name: "Request kind" }));
  await userEvent.click(screen.getByRole("option", { name }));
}

describe("model API request recipes", () => {
  it.each(["chat", "unknown"])("treats uncatalogued %s metadata as unknown until an explicit selection", async (type) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, type, uncatalogued: true }} />);
    expect(screen.getByText("Type not reported")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy code" })).not.toBeInTheDocument();
    await chooseKind("Embeddings");
    expect(requestCode()).toContain("/v1/embeddings");
    expect(requestCode()).toContain('"input": "Hello"');
    expect(requestCode()).toContain(ownRow.id);
    expect(requestCode()).not.toContain("messages");
    expect(screen.queryByRole("checkbox", { name: "Stream the response" })).not.toBeInTheDocument();
    expect(screen.getByText(/compatibility has not been verified/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy code" }));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("resets the unknown-kind selection when the model changes", async () => {
    const { rerender } = renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, uncatalogued: true }} />);
    await chooseKind("Embeddings");
    rerender(
      <ThemeProvider theme={theme}><MemoryRouter>
        <ModelApi {...apiProps} row={{ ...ownRow, id: "example-provider/another", ownRoutes: [{ sendAs: "example-provider/another" }], uncatalogued: true }} />
      </MemoryRouter></ThemeProvider>,
    );
    expect(screen.queryByRole("button", { name: "Copy code" })).not.toBeInTheDocument();
  });

  it.each([["images", "prompt", "/images/generations"], ["rerank", "documents", "/rerank"]])(
    "offers a complete %s recipe without chat controls", (type, field, endpoint) => {
      renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, type }} />);
      expect(requestCode()).toContain(endpoint);
      expect(requestCode()).toContain(`"${field}"`);
      expect(requestCode()).not.toContain("messages");
      expect(screen.queryByRole("combobox", { name: "Request kind" })).not.toBeInTheDocument();
      expect(screen.queryByRole("checkbox", { name: "Stream the response" })).not.toBeInTheDocument();
    },
  );

  it.each(["video", "unsupported"])("does not present %s as runnable", (type) => {
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, type }} />);
    expect(screen.queryByRole("button", { name: "Copy code" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/ready.to.run|\/videos|\/chat\/completions/i);
    expect(screen.getByText(/no request example|does not serve video/i)).toBeInTheDocument();
  });

  it.each(["images", "audio_speech", "audio_transcriptions"])("does not offer a %s request on managed credits", (type) => {
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, type, access: ACCESS.MANAGED, managedOn: true, ownRoutes: [] }} />);
    expect(screen.queryByRole("button", { name: "Copy code" })).not.toBeInTheDocument();
    expect(screen.getByText(/credits.*chat.*only/i)).toBeInTheDocument();
  });

  it("uses the matching submitted request and keeps its streaming choice", async () => {
    const requestSnapshot = {
      model: ownRow.id,
      messages: [{ role: "system", content: 'System "quoted"\nline' }, { role: "user", content: "Submitted prompt" }],
      temperature: 0, max_tokens: 42, top_p: 0.5, stream: true,
    };
    renderThemed(<ModelApi {...apiProps} row={ownRow} requestSnapshot={requestSnapshot} />);
    expect(screen.getByText(/using your submitted request/i)).toBeInTheDocument();
    expect(requestCode()).toContain("Submitted prompt");
    expect(requestCode()).toContain('"temperature": 0');
    expect(requestCode()).toMatch(/^curl -N /);
    await userEvent.click(screen.getByRole("button", { name: "Python" }));
    expect(requestCode()).toContain("stream=True");
    expect(requestCode()).toContain("max_tokens=42");
  });

  it("fills a fresh personal key without rewriting matching words in submitted prompts", async () => {
    keyState.value = "synthetic-personal-key";
    const content = '$AGENTCC_API_KEY process.env.AGENTCC_API_KEY os.environ["AGENTCC_API_KEY"]';
    renderThemed(<ModelApi {...apiProps} row={ownRow} requestSnapshot={{ model: ownRow.id, messages: [{ role: "user", content }], stream: false }} />);
    for (const language of ["cURL", "Python", "TypeScript"]) {
      if (language !== "cURL") await userEvent.click(screen.getByRole("button", { name: language }));
      expect(requestCode()).toContain("synthetic-personal-key");
      expect(requestCode()).toContain("$AGENTCC_API_KEY process.env.AGENTCC_API_KEY");
    }
  });

  it("ignores a snapshot belonging to another model", () => {
    renderThemed(<ModelApi {...apiProps} row={ownRow} requestSnapshot={{ model: "another/model", messages: [{ role: "user", content: "Other model secret prompt" }], stream: true }} />);
    expect(requestCode()).toContain("Hello");
    expect(document.body.textContent).not.toContain("Other model secret prompt");
    expect(screen.queryByText(/using your submitted request/i)).not.toBeInTheDocument();
  });

  it.each(["chat", "images", "rerank", "audio_speech", "audio_transcriptions"])("shows %s requests on the marked placeholder when the hosted URL is missing", (type) => {
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, type }} baseUrl="" />);
    expect(document.querySelector("pre")?.textContent).toContain("YOUR_GATEWAY_URL");
    expect(screen.getByText(/public URL isn't available/i)).toBeInTheDocument();
  });
});

async function chooseRecipe(name) {
  await userEvent.click(screen.getByRole("combobox", { name: "Recipe" }));
  await userEvent.click(screen.getByRole("option", { name }));
}

describe("model API routing, native and audio recipes", () => {
  it("copies routing examples without inference or provider configuration changes", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const send = vi.spyOn(XMLHttpRequest.prototype, "send");
    const row = { ...ownRow, ownRoutes: [...ownRow.ownRoutes] };
    const before = JSON.stringify(row);
    renderThemed(<ModelApi {...apiProps} row={row} />);
    await chooseRecipe("Routing and fallback");
    expect(requestCode()).toContain('"models": ["YOUR_PROVIDER_ID/YOUR_FALLBACK_MODEL"]');
    expect(requestCode()).toContain('"allow_fallbacks": false');
    expect(screen.getByText(/429.*5xx/i)).toBeInTheDocument();
    expect(screen.getByText(/fallback_enabled: false/)).toHaveTextContent(/disables caller chains/i);
    expect(screen.getByText(/upstream-provider slugs/i)).toHaveTextContent(/organization provider IDs/i);
    expect(screen.getByText(/provider_preference_unavailable/)).toHaveTextContent(/route/i);
    expect(screen.getByText(/mixed.price.unit/i)).toHaveTextContent(/not.*supported/i);
    for (const language of ["cURL", "Python", "TypeScript"]) {
      if (language !== "cURL") await userEvent.click(screen.getByRole("button", { name: language }));
      await userEvent.click(screen.getByRole("button", { name: "Copy code" }));
      expect(copyText).toHaveBeenLastCalledWith(requestCode());
    }
    expect(copyText).toHaveBeenCalledTimes(3);
    expect(fetch).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    expect(JSON.stringify(row)).toBe(before);
  });

  it.each(["Messages", "Responses"])("offers %s only as an explicitly selected own-provider instructional recipe", async (name) => {
    keyState.value = "synthetic-personal-key";
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderThemed(<ModelApi {...apiProps} row={ownRow} baseUrl="https://gateway.example.test/team/v1/" />);
    await userEvent.click(screen.getByRole("checkbox", { name: "Stream the response" }));
    await chooseRecipe(`${name} (instructional)`);
    expect(requestCode()).toContain(`/team/v1/${name.toLowerCase()}`);
    expect(screen.getByText(/compatibility has not been verified/i)).toHaveTextContent(/own provider/i);
    expect(screen.getByText(/beta.*tool/i)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Stream the response" })).not.toBeInTheDocument();
    for (const language of ["cURL", "Python", "TypeScript"]) {
      if (language !== "cURL") await userEvent.click(screen.getByRole("button", { name: language }));
      expect(requestCode()).toContain("synthetic-personal-key");
      expect(requestCode()).not.toContain("/v1/v1");
      expect(requestCode()).not.toContain("chat.completions");
      await userEvent.click(screen.getByRole("button", { name: "Copy code" }));
      expect(copyText).toHaveBeenLastCalledWith(requestCode());
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it("never offers native recipes for managed chat even with retained own routes", async () => {
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, access: ACCESS.BOTH, managedOn: true, providerAccessState: "error" }} />);
    expect(requestCode()).toContain("/chat/completions");
    expect(screen.queryByText(/key instead/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "Recipe" }));
    expect(screen.queryByRole("option", { name: /Messages|Responses/ })).not.toBeInTheDocument();
  });

  it.each(["pending", "error"])("does not mistake %s access for a missing provider or expose retained recipes", (providerAccessState) => {
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, providerAccessState, access: ACCESS.UNKNOWN }} />);
    expect(screen.getByRole("heading", { level: 2, name: "Own-provider access is not verified" })).toBeInTheDocument();
    expect(screen.getByText(/retry the provider read/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy code" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Recipe" })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/add a provider|undefined/i);
  });

  it.each([["Speech", "/audio/speech"], ["Transcription", "/audio/transcriptions"]])("adds %s to the unknown-kind picker with an honest copy-only disclosure", async (name, endpoint) => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    renderThemed(<ModelApi {...apiProps} row={{ ...ownRow, type: "unknown", uncatalogued: true }} />);
    await chooseKind(name);
    expect(requestCode()).toContain(endpoint);
    expect(screen.getByText(/compatibility has not been verified/i)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Stream the response" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Recipe" })).not.toBeInTheDocument();
    if (name === "Speech") {
      expect(requestCode()).toContain("--output speech.mp3");
      expect(screen.getByText(/binary.*speech.mp3/i)).toBeInTheDocument();
    } else {
      expect(requestCode()).toContain("file=@./YOUR_AUDIO_FILE.wav");
      expect(screen.getByText(/text.*stdout/i)).toBeInTheDocument();
    }
    await userEvent.click(screen.getByRole("button", { name: "Copy code" }));
    expect(copyText).toHaveBeenLastCalledWith(requestCode());
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["Messages (instructional)", "Responses (instructional)", "Routing and fallback"])("shows the %s recipe on the marked placeholder when the public URL is unavailable", async (name) => {
    renderThemed(<ModelApi {...apiProps} row={ownRow} baseUrl="" />);
    await chooseRecipe(name);
    expect(document.querySelector("pre")?.textContent).toContain("YOUR_GATEWAY_URL");
    expect(screen.getByText(/isn't available from the console right now/)).toBeInTheDocument();
  });
});
