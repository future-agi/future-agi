// Supplemental union proofs. Copy into the assembled frontend test tree.
// R-LIT: all credential-like literals below are tagged, visibly synthetic fixtures.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import CreateApiKey from "src/sections/keys/view/CreateApiKey";
import ActionMenu from "src/sections/keys/view/ActionMenu";
import CellRenderer from "src/sections/keys/view/CellRenderer";

const state = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  remove: vi.fn(),
  mutation: null,
}));
vi.mock("src/utils/axios", () => ({
  default: { post: state.post, get: state.get, delete: state.remove },
  endpoints: {
    keys: {
      generateSecretKey: "/accounts/key/generate_secret_key/",
      disablekey: "/synthetic/disable/",
      deleteKey: "/synthetic/delete/",
      enableKey: "/synthetic/enable/",
    },
  },
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { id: "synthetic-person" } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "synthetic-org" }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "synthetic-workspace",
    currentWorkspaceName: "Synthetic workspace",
  }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/tooltip/CustomTooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("@tanstack/react-query", () => ({
  useMutation: (options) => {
    state.mutation = options;
    return { mutate: vi.fn(), isPending: false };
  },
}));

// Typed rows keep the negative literals inspectable rather than constructing them.
const invalidPairs = [
  {
    tag: "synthetic-missing-secret",
    api_key: "example-api-value",
    secret_key: undefined,
  },
  {
    tag: "synthetic-missing-api",
    api_key: undefined,
    secret_key: "example-secret-value",
  },
  {
    tag: "synthetic-partially-masked-secret",
    api_key: "example-api-value",
    secret_key: "example****value",
  },
  {
    tag: "synthetic-partially-masked-api",
    api_key: "example****value",
    secret_key: "example-secret-value",
  },
  {
    tag: "synthetic-nonstring-secret",
    api_key: "example-api-value",
    secret_key: 1234,
  },
  {
    tag: "synthetic-nonstring-api",
    api_key: 1234,
    secret_key: "example-secret-value",
  },
];
const credentialFields = ["api_key", "secret_key", "apiKey", "secretKey"];
// A starred value is the server's masked hint and is shown as sent; a value
// without a star is never shown.
const listValues = [
  {
    tag: "synthetic-raw-list-value",
    value: "example-secret-value",
    shown: "********",
  },
  {
    tag: "synthetic-partial-list-value",
    value: "example****value",
    shown: "example****value",
  },
];

beforeEach(() => {
  state.post.mockReset();
  state.get.mockReset();
  state.remove.mockReset();
  state.mutation = null;
});
afterEach(cleanup);

describe("personal-key union all-or-nothing creation", () => {
  it.each(invalidPairs)(
    "rejects $tag without revealing either half",
    async ({ api_key, secret_key }) => {
      state.post.mockResolvedValue({
        data: { status: true, result: { api_key, secret_key } },
      });
      render(<CreateApiKey open onClose={vi.fn()} />);
      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Create a key",
        }),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "The key response could not be read.",
      );
      expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Secret key")).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Shell environment"),
      ).not.toBeInTheDocument();
      expect(state.post).toHaveBeenCalledTimes(1);
      expect(state.get).not.toHaveBeenCalled();
      expect(state.mutation).toBeNull();
    },
  );

  it("keeps the dev close control disabled until the single mint settles", async () => {
    let finish;
    state.post.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const onClose = vi.fn();
    render(<CreateApiKey open onClose={onClose} />);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Create a key",
      }),
    );
    expect(
      screen.getByRole("button", { name: "Close key dialog" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Close key dialog" }));
    expect(onClose).not.toHaveBeenCalled();
    // R-LIT tag: synthetic-complete-pair.
    finish({
      data: {
        status: true,
        result: {
          api_key: "example-api-value",
          secret_key: "example-secret-value",
        },
      },
    });
    await screen.findByDisplayValue("example-secret-value");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Close key dialog" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Close key dialog" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(state.post).toHaveBeenCalledTimes(1);
    expect(state.get).not.toHaveBeenCalled();
  });
});

describe("personal-key union list confidentiality", () => {
  describe.each(credentialFields)("%s field", (field) => {
    it.each(listValues)(
      "shows $tag as $shown without copy controls",
      ({ value, shown }) => {
        const { container } = render(
          <CellRenderer value={value} column={{ colDef: { field } }} />,
        );
        expect(container.textContent).toBe(shown);
        if (shown !== value) {
          expect(container.innerHTML).not.toContain(value);
        }
        expect(screen.queryByRole("button")).not.toBeInTheDocument();
        expect(state.get).not.toHaveBeenCalled();
      },
    );
  });
});

describe("personal-key union stricter system-key actions", () => {
  const actionSurfaces = [
    { tag: "table-actions", renderRow: (data) => <ActionMenu data={data} /> },
    {
      tag: "legacy-actions",
      renderRow: (data) => (
        <CellRenderer data={data} column={{ colDef: { field: "id" } }} />
      ),
    },
  ];
  it.each(actionSurfaces)(
    "$tag refuses system mutations even after a row changes",
    ({ renderRow }) => {
      // R-LIT tag: synthetic-action-row; type/action values are public enums.
      const row = {
        id: "synthetic-key-id",
        type: "user",
        key_name: "Synthetic key",
        enabled: true,
      };
      const view = render(renderRow(row));
      view.rerender(renderRow({ ...row, type: "system" }));
      for (const action of ["delete", "disable", "enable"]) {
        expect(() => state.mutation.mutationFn({ action })).toThrow(
          "System keys cannot be changed.",
        );
      }
      expect(state.post).not.toHaveBeenCalled();
      expect(state.remove).not.toHaveBeenCalled();
      expect(state.get).not.toHaveBeenCalled();
    },
  );
});
