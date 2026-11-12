import React, { useState } from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import CreateApiKey from "../CreateApiKey";
import KeysEmptyState from "../KeysEmptyState";
import PersonalKeySnippet, { PersonalKeyControl } from "../PersonalKeySnippet";
import { usePersonalKeyText } from "src/components/personal-key-snippet-context";
import { personalKeyRows } from "../personalKeys";

const state = vi.hoisted(() => ({
  user: "person-a",
  org: "org-a",
  workspace: "workspace-a",
  userInfo: {},
  get: vi.fn(),
  post: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: state.get, post: state.post },
  endpoints: {
    keys: { generateSecretKey: "/accounts/key/generate_secret_key/" },
  },
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { ...state.userInfo, id: state.user } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: state.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: state.workspace,
    currentWorkspaceName: "Workspace A",
  }),
}));

function Example({
  template = 'FI_API_KEY="YOUR_FI_API_KEY" FI_SECRET_KEY="YOUR_FI_SECRET_KEY"',
}) {
  const fill = usePersonalKeyText();
  return (
    <>
      <PersonalKeyControl />
      <pre data-testid="snippet">{fill(template)}</pre>
    </>
  );
}
Example.propTypes = { template: PropTypes.string };

function Page() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <KeysEmptyState onCreate={() => setOpen(true)} />
      <CreateApiKey open={open} onClose={() => setOpen(false)} />
    </>
  );
}
const response = {
  data: {
    status: true,
    result: {
      api_key: "example-api-value",
      secret_key: "example-secret-value",
    },
  },
};
// Match the issuer's explicit-membership check and axios' normalized error.
const membershipRefusal = {
  statusCode: 403,
  status: false,
  code: "permission_denied",
  detail: "Active workspace membership is required.",
};
function mockMembershipIssuer(workspaceIds) {
  state.post.mockImplementation(async (_url, body) => {
    if (body.workspace_id && !workspaceIds.includes(body.workspace_id)) {
      throw membershipRefusal;
    }
    return response;
  });
}

beforeEach(() => {
  state.user = "person-a";
  state.org = "org-a";
  state.workspace = "workspace-a";
  state.userInfo = {
    organization: { id: "org-a" },
    organization_role: "Member",
    org_level: 3,
    default_workspace_id: "workspace-a",
    default_workspace_role: "workspace_member",
    ws_level: 3,
  };
  state.get.mockReset();
  state.post.mockReset().mockResolvedValue(response);
});

describe("personal key first run", () => {
  it("shows the exact empty state without fetching or creating credentials", () => {
    render(<Page />);
    expect(
      screen.getByText(
        "No API keys yet. Create a personal key to use the SDK, CLI or OTLP.",
      ),
    ).toBeVisible();
    expect(state.get).not.toHaveBeenCalled();
    expect(state.post).not.toHaveBeenCalled();
  });
  it("opens a prefilled form without minting; submits once; shows both values and forgets on dismissal", async () => {
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    expect(screen.getByLabelText(/Key name/).value).toMatch(/^Personal key/);
    expect(screen.getByLabelText(/Workspace/)).toBeVisible();
    expect(state.post).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Create a key" });
    fireEvent.click(submit);
    fireEvent.keyDown(screen.getByLabelText(/Key name/), { key: "Enter" });
    expect(await screen.findByDisplayValue("example-api-value")).toBeVisible();
    expect(screen.getByDisplayValue("example-secret-value")).toBeVisible();
    expect(screen.getByLabelText("Shell environment").value).toBe(
      "export FI_API_KEY='example-api-value' FI_SECRET_KEY='example-secret-value'",
    );
    expect(screen.getByRole("button", { name: "Copy API key" })).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Copy Secret key" }),
    ).toBeVisible();
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][0]).toBe(
      "/accounts/key/generate_secret_key/",
    );
    expect(state.post.mock.calls[0][1].workspace_id).toBe("workspace-a");
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBe("workspace-a");
    fireEvent.click(screen.getByRole("button", { name: "I saved it" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    expect(
      screen.queryByDisplayValue("example-secret-value"),
    ).not.toBeInTheDocument();
    expect(state.post).toHaveBeenCalledTimes(1);
  });
  it.each([
    ["unrestricted", null],
    ["selected", "workspace-a"],
  ])(
    "sends an explicit %s workspace binding in the create body",
    async (_label, workspaceId) => {
      render(<Page />);
      fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
      fireEvent.mouseDown(
        screen.getByRole("combobox", { name: /Workspace/ }),
      );
      fireEvent.click(screen.getByRole("option", {
        name: workspaceId ? "Workspace A" : "No workspace restriction",
      }));
      const name = screen.getByLabelText(/Key name/).value;
      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Create a key",
        }),
      );
      await screen.findByRole("button", { name: "I saved it" });
      expect(state.post).toHaveBeenCalledTimes(1);
      expect(state.post.mock.calls[0][1]).toEqual({
        key_name: name,
        workspace_id: workspaceId,
      });
    },
  );
  it("defaults to the newly selected workspace when the dialog context changes", async () => {
    const view = render(<CreateApiKey open onClose={() => {}} />);
    state.workspace = "research";
    state.userInfo.default_workspace_id = "research";
    view.rerender(<CreateApiKey open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    await screen.findByRole("button", { name: "I saved it" });
    expect(state.post.mock.calls[0][1].workspace_id).toBe("research");
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBe("research");
  });
  it.each([
    ["Owner", 15],
    ["Admin", 8],
  ])("creates by default for a global %s without explicit workspace membership", async (role, level) => {
    // user-info reports effective global roles/levels even without membership.
    state.userInfo.organization_role = role;
    state.userInfo.org_level = level;
    state.userInfo.default_workspace_role = role;
    state.userInfo.ws_level = level;
    mockMembershipIssuer([]);
    render(<CreateApiKey open onClose={() => {}} />);
    const selection = screen.getByRole("combobox", { name: /Workspace/ });
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    expect(state.post.mock.calls[0][1].workspace_id).toBeNull();
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBeNull();
    expect(selection).toHaveTextContent("No workspace restriction");
    expect(await screen.findByDisplayValue("example-api-value")).toBeVisible();
    expect(screen.getByDisplayValue("example-secret-value")).toBeVisible();
    expect(state.post).toHaveBeenCalledTimes(1);
  });
  it("keeps a Research-only member pinned to their current workspace by default", async () => {
    state.workspace = "research";
    state.userInfo.default_workspace_id = "research";
    mockMembershipIssuer(["research"]);
    render(<CreateApiKey open onClose={() => {}} />);
    expect(screen.getByRole("combobox", { name: /Workspace/ })).toHaveTextContent("Workspace A");
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    await screen.findByRole("button", { name: "I saved it" });
    expect(state.post.mock.calls[0][1].workspace_id).toBe("research");
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBe("research");
    expect(state.post).toHaveBeenCalledTimes(1);
  });
  it.each([
    ["missing user-info", {}],
    ["unknown org level", { org_level: null }],
    ["no workspace membership level", { ws_level: null }],
    ["another organization", { organization: { id: "org-b" } }],
    ["another workspace", { default_workspace_id: "workspace-b" }],
  ])("uses an unrestricted default with %s", async (label, userInfo) => {
    state.userInfo = label === "missing user-info" ? userInfo : { ...state.userInfo, ...userInfo };
    mockMembershipIssuer([]);
    render(<CreateApiKey open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    expect(state.post.mock.calls[0][1].workspace_id).toBeNull();
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBeNull();
    await screen.findByRole("button", { name: "I saved it" });
    expect(state.post).toHaveBeenCalledTimes(1);
  });
  it.each(["normalized", "raw Axios"])("explains a %s membership refusal without retrying or changing the binding", async (shape) => {
    const refusal = { ...membershipRefusal, secret_key: "must-not-display" };
    state.post.mockRejectedValue(shape === "normalized" ? refusal : {
      response: { status: 403, data: refusal },
    });
    render(<CreateApiKey open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      'Active workspace membership is required. Choose "No workspace restriction" or ask a workspace admin to add you, then try again.',
    );
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][1].workspace_id).toBe("workspace-a");
    expect(screen.getByRole("combobox", { name: /Workspace/ })).toHaveTextContent("Workspace A");
    expect(screen.queryByRole("button", { name: "I saved it" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Secret key")).not.toBeInTheDocument();
    expect(screen.queryByText("must-not-display")).not.toBeInTheDocument();
    // The user, not an automatic retry, opts into an unpinned key.
    mockMembershipIssuer([]);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Workspace/ }));
    fireEvent.click(screen.getByRole("option", { name: "No workspace restriction" }));
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    await screen.findByRole("button", { name: "I saved it" });
    expect(state.post).toHaveBeenCalledTimes(2);
    expect(state.post.mock.calls[1][1].workspace_id).toBeNull();
    expect(state.post.mock.calls[1][2].headers["X-Workspace-Id"]).toBeNull();
  });
  it("keeps unrelated permission refusals generic", async () => {
    state.post.mockRejectedValue({
      ...membershipRefusal,
      detail: "Active organization membership is required.",
    });
    render(<CreateApiKey open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The key could not be confirmed.");
    expect(state.post).toHaveBeenCalledTimes(1);
  });
  it("can explicitly create an unpinned key when no current workspace exists", async () => {
    state.workspace = null;
    render(<CreateApiKey open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    await screen.findByRole("button", { name: "I saved it" });
    expect(state.post.mock.calls[0][1].workspace_id).toBeNull();
    expect(state.post.mock.calls[0][2].headers["X-Workspace-Id"]).toBeNull();
  });
  it("does not retry without the explicit workspace binding when the issuer rejects it", async () => {
    state.post.mockRejectedValue({ response: { status: 400 } });
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Workspace/ }));
    fireEvent.click(screen.getByRole("option", { name: "Workspace A" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The key could not be confirmed.",
    );
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.post.mock.calls[0][1].workspace_id).toBe("workspace-a");
    expect(
      screen.queryByRole("button", { name: "I saved it" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Secret key")).not.toBeInTheDocument();
  });
  it("keeps placeholders on mount and fills only this snippet after explicit creation", async () => {
    const view = render(
      <PersonalKeySnippet>
        <Example />
      </PersonalKeySnippet>,
    );
    expect(screen.getByTestId("snippet")).toHaveTextContent(
      'FI_API_KEY="YOUR_FI_API_KEY" FI_SECRET_KEY="YOUR_FI_SECRET_KEY"',
    );
    expect(state.get).not.toHaveBeenCalled();
    expect(state.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "I saved it" }));
    expect(screen.getByTestId("snippet")).toHaveTextContent(
      'FI_API_KEY="example-api-value" FI_SECRET_KEY="example-secret-value"',
    );
    view.rerender(
      <PersonalKeySnippet>
        <Example />
      </PersonalKeySnippet>,
    );
    expect(state.post).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Forget key" }));
    expect(screen.getByTestId("snippet")).toHaveTextContent(
      "YOUR_FI_SECRET_KEY",
    );
    expect(state.get).not.toHaveBeenCalled();
  });
  it("does not reveal an in-flight response after the signed-in person changes", async () => {
    let finish;
    state.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const view = render(
      <PersonalKeySnippet>
        <Example />
      </PersonalKeySnippet>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    state.user = "person-b";
    view.rerender(
      <PersonalKeySnippet>
        <Example />
      </PersonalKeySnippet>,
    );
    finish(response);
    await waitFor(() =>
      expect(
        screen.queryByDisplayValue("example-secret-value"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("snippet")).toHaveTextContent(
      "YOUR_FI_SECRET_KEY",
    );
  });
  it.each(["org", "workspace"])(
    "drops a filled snippet when %s changes",
    async (field) => {
      const view = render(
        <PersonalKeySnippet>
          <Example />
        </PersonalKeySnippet>,
      );
      fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Create a key",
        }),
      );
      fireEvent.click(
        await screen.findByRole("button", { name: "I saved it" }),
      );
      expect(screen.getByTestId("snippet")).toHaveTextContent(
        "example-secret-value",
      );
      state[field] = "other-scope";
      view.rerender(
        <PersonalKeySnippet>
          <Example />
        </PersonalKeySnippet>,
      );
      expect(screen.getByTestId("snippet")).toHaveTextContent(
        "YOUR_FI_SECRET_KEY",
      );
      expect(
        screen.queryByRole("button", { name: "Forget key" }),
      ).not.toBeInTheDocument();
      expect(state.post).toHaveBeenCalledTimes(1);
    },
  );
  it("does not retry a failed create on its own or put the error response in the UI", async () => {
    state.post.mockRejectedValue({ data: { secret_key: "must-not-display" } });
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The key could not be confirmed.",
    );
    expect(screen.queryByText("must-not-display")).not.toBeInTheDocument();
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.get).not.toHaveBeenCalled();
  });
  it("replaces the whole bracketed placeholder used by simulation SDK examples", async () => {
    // simulate/constants/installation_guide.py uses angle-bracketed values.
    render(
      <PersonalKeySnippet>
        <Example
          template={
            '# export FI_API_KEY="<YOUR_FI_API_KEY>"\n# export FI_SECRET_KEY="<YOUR_FI_SECRET_KEY>"'
          }
        />
      </PersonalKeySnippet>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create a key" }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "I saved it" }));
    expect(screen.getByTestId("snippet").textContent).toBe(
      '# export FI_API_KEY="example-api-value"\n# export FI_SECRET_KEY="example-secret-value"',
    );
    expect(state.post).toHaveBeenCalledTimes(1);
  });
  it("masks every list row and discards non-personal types", () => {
    const rows = personalKeyRows([
      {
        id: 1,
        type: "user",
        api_key: "raw-api",
        secret_key: "raw-secret",
        created_by: "Other member",
      },
      { id: 2, type: "system", api_key: "legacy" },
      { id: 3, type: "user", api_key: "abcd****1234", secret_key: "****1234" },
    ]);
    expect(rows.map((row) => row.id)).toEqual([1, 3]);
    expect(rows[0].api_key).toBe("********");
    expect(rows[0].secret_key).toBe("********");
    expect(rows[1].api_key).toBe("abcd****1234");
    expect(rows[1].secret_key).toBe("****1234");
  });
});
