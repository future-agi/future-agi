import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import React from "react";
import ObserveTabBar, { typingInto } from "../ObserveTabBar";
import { ObserveHeaderContext } from "src/sections/project/context/ObserveHeaderContext";

const mockCreateSavedView = vi.fn();
let mockSavedViewsList = [];
// Features the deployment's license or plan denies.
const capabilities = vi.hoisted(() => ({ denied: new Set() }));
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureDenied: (feature) => capabilities.denied.has(feature),
}));
vi.mock("src/api/project/saved-views", () => ({
  useGetSavedViews: () => ({ data: { custom_views: mockSavedViewsList } }),
  useCreateSavedView: () => ({ mutate: mockCreateSavedView }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useDeleteSavedView: () => ({ mutate: vi.fn() }),
  useReorderSavedViews: () => ({ mutate: vi.fn() }),
  // Mirror of the real helper (the factory replaces the whole module).
  getOwnViewNames: (views, userId) =>
    (views ?? [])
      .filter((v) => userId && String(v.created_by?.id) === String(userId))
      .map((v) => v.name),
}));

const renderWithCtx = ({
  getViewConfig = () => ({}),
  activeTab = "traces",
  selectedTab,
  canSaveView,
  onTabChange = () => {},
} = {}) => {
  // resolveTabType reads selectedTab from the URL (not getTabType).
  window.history.pushState(
    {},
    "Test page",
    selectedTab ? `/?selectedTab=${selectedTab}` : "/",
  );
  return render(
    <ObserveHeaderContext.Provider
      value={{
        headerConfig: {},
        setHeaderConfig: () => {},
        activeViewConfig: null,
        setActiveViewConfig: () => {},
        registerGetViewConfig: () => {},
        getViewConfig,
      }}
    >
      <ObserveTabBar
        projectId="p1"
        activeTab={activeTab}
        onTabChange={onTabChange}
        canSaveView={canSaveView}
      />
    </ObserveHeaderContext.Provider>,
  );
};

const clickCreateViewButton = () => {
  const btn = document.querySelector("[data-create-view-btn]");
  if (!btn) throw new Error("data-create-view-btn not found");
  fireEvent.click(btn);
};

const typeAndSubmit = async (name) => {
  const input = await screen.findByRole("textbox");
  fireEvent.change(input, { target: { value: name } });
  const saveBtn = screen.getByRole("button", { name: /save|create/i });
  fireEvent.click(saveBtn);
};

describe("ObserveTabBar — Save View snapshots current filters", () => {
  beforeEach(() => {
    mockCreateSavedView.mockReset();
    mockSavedViewsList = [];
  });

  it("passes ctx.getViewConfig() output as config in createSavedView", async () => {
    const snapshot = {
      filters: [
        {
          column_id: "status",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "ERROR",
          },
        },
      ],
      display: { viewMode: "grid" },
    };
    renderWithCtx({ getViewConfig: () => snapshot });

    clickCreateViewButton();
    await typeAndSubmit("t1");

    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    const payload = mockCreateSavedView.mock.calls[0][0];
    expect(payload.config).toEqual(snapshot);
    expect(payload.tab_type).toBe("traces");
    expect(payload.name).toBe("t1");
    expect(payload.project_id).toBe("p1");
  });

  it("falls back to {} when getViewConfig returns null", async () => {
    renderWithCtx({ getViewConfig: () => null });

    clickCreateViewButton();
    await typeAndSubmit("t2");

    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].config).toEqual({});
  });
});

describe("ObserveTabBar — resolveTabType", () => {
  beforeEach(() => {
    mockCreateSavedView.mockReset();
    mockSavedViewsList = [];
  });

  it("saves tab_type 'traces' when selectedTab is not 'spans'", async () => {
    renderWithCtx({ activeTab: "traces" });
    clickCreateViewButton();
    await typeAndSubmit("t");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("traces");
  });

  it("saves tab_type 'spans' when selectedTab is 'spans' in the URL", async () => {
    renderWithCtx({ activeTab: "spans", selectedTab: "spans" });
    clickCreateViewButton();
    await typeAndSubmit("s");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("spans");
  });

  it("forces tab_type to 'users' when activeTab is 'users'", async () => {
    renderWithCtx({ activeTab: "users" });
    clickCreateViewButton();
    await typeAndSubmit("u");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("users");
  });

  it("forces tab_type to 'sessions' when activeTab is 'sessions'", async () => {
    renderWithCtx({ activeTab: "sessions" });
    clickCreateViewButton();
    await typeAndSubmit("ss");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("sessions");
  });

  it("inherits the current view's tab_type when activeTab is 'view-<id>'", async () => {
    mockSavedViewsList = [
      { id: "abc", name: "my-spans-view", tab_type: "spans" },
    ];
    renderWithCtx({ activeTab: "view-abc" });
    clickCreateViewButton();
    await typeAndSubmit("copy");
    await waitFor(() => expect(mockCreateSavedView).toHaveBeenCalled());
    expect(mockCreateSavedView.mock.calls[0][0].tab_type).toBe("spans");
  });

  it("offers no Create view on a page without a list, like Ask", () => {
    renderWithCtx({ activeTab: "sessions", canSaveView: false });
    expect(document.querySelector("[data-create-view-btn]")).toBeNull();
  });
});

// Insights is Enterprise: on a deployment without it (e.g. open source) the
// tab carries a lock and still opens, onto the gate its page shows.
describe("ObserveTabBar — Insights tab", () => {
  beforeEach(() => {
    mockSavedViewsList = [];
    capabilities.denied = new Set();
  });

  // The tab's accessible name is its shortcut tooltip, so find it by its text.
  const insightsTab = () => screen.getByText("Insights").closest("button");

  it("locks the Insights tab when the deployment doesn't include it", () => {
    capabilities.denied = new Set(["insights"]);
    const onTabChange = vi.fn();
    renderWithCtx({ onTabChange });
    expect(
      within(insightsTab()).getByRole("img", { name: "Enterprise feature" }),
    ).toBeInTheDocument();
    fireEvent.click(insightsTab());
    expect(onTabChange).toHaveBeenCalledWith("insights");
    // Named by the tab, not by its tooltip ("Enterprise feature · Press 4").
    expect(
      screen.getByRole("button", { name: "Insights, Enterprise feature" }),
    ).toBe(insightsTab());
  });

  it("shows no lock when Insights is allowed", () => {
    renderWithCtx();
    expect(
      within(insightsTab()).queryByRole("img", { name: "Enterprise feature" }),
    ).toBeNull();
  });
});

describe("typingInto, the guard of the tab shortcuts", () => {
  const el = (html) => {
    const host = document.createElement("div");
    host.innerHTML = html;
    document.body.appendChild(host);
    return host.firstElementChild;
  };

  it("skips fields, editable elements and open menus", () => {
    expect(typingInto(el("<input />"))).toBe(true);
    expect(typingInto(el('<div contenteditable="true">x</div>'))).toBe(true);
    expect(typingInto(el('<ul role="menu"><li>x</li></ul>'))).toBe(true);
    // A menu item, inside an MUI menu's list, while type-ahead is on.
    const menu = el('<ul role="listbox"><li tabindex="0">Insights</li></ul>');
    expect(typingInto(menu.querySelector("li"))).toBe(true);
  });

  it("lets a key on the page itself through", () => {
    expect(typingInto(el("<button>Tab</button>"))).toBe(false);
    expect(typingInto(document.body)).toBe(false);
    expect(typingInto(null)).toBe(false);
  });
});
