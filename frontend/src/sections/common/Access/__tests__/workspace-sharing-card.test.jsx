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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import {
  NOT_IN_RELEASE,
  dimExceptLine,
} from "src/components/NotInRelease/notInRelease";
import WorkspaceSharingCard from "../WorkspaceSharingCard";

// The Sharing card on Settings > Workspace > General. While this build does
// not serve the sharing policy, the card is on screen with both controls off
// and disabled, says why with the shared release line, and sends nothing.
// Flipped to served, it is the working card it always was.

const POLICY_URL = "/model-hub/workspaces/ws-1/sharing-policy/";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  snackbar: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => h.get(...args),
    put: (...args) => h.put(...args),
  },
  endpoints: {
    develop: {
      w4: {
        sharingPolicy: (id) => `/model-hub/workspaces/${id}/sharing-policy/`,
        audit: "/model-hub/audit/",
      },
    },
  },
}));
vi.mock("src/components/snackbar", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: h.snackbar,
}));

const theme = createTheme({ palette: palette("light"), typography });

const policy = (fields = {}) => ({
  workspace_name: "Design",
  public_links: true,
  max_days: 7,
  max_days_limit: 30,
  can_edit: true,
  active_links: 0,
  ...fields,
});

function showCard(props = {}) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <WorkspaceSharingCard workspaceId="ws-1" {...props} />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

// The opacity the shared helper gives the parts of a disabled card.
const DIMMED = Object.values(dimExceptLine)[0].opacity;

// The opacity a node shows at inside the card: its own and that of every
// ancestor up to the card, multiplied.
function shownOpacity(node, card) {
  let shown = 1;
  for (let el = node; el; el = el.parentElement) {
    const own = parseFloat(window.getComputedStyle(el).opacity);
    if (!Number.isNaN(own)) shown *= own;
    if (el === card) break;
  }
  return Math.round(shown * 1000) / 1000;
}

// A colour written the way the browser reports a computed one.
function asComputed(color) {
  const probe = document.createElement("span");
  probe.style.color = color;
  return probe.style.color;
}

beforeEach(() => {
  vi.clearAllMocks();
  h.get.mockReset().mockRejectedValue({ statusCode: 404 });
  h.put.mockReset();
});

afterEach(cleanup);

describe("Sharing while this build does not serve the policy", () => {
  it("is shown with both controls off and disabled, and the shared release line says why", () => {
    showCard();
    const card = screen.getByTestId("workspace-sharing-card");
    const sharing = within(card);
    expect(sharing.getByText("Sharing")).toBeVisible();
    expect(sharing.getByText(NOT_IN_RELEASE)).toBeVisible();

    const publicLinks = sharing.getByRole("checkbox", { name: "Public links" });
    expect(publicLinks).toBeDisabled();
    expect(publicLinks).not.toBeChecked();
    expect(publicLinks).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(
      sharing.getByText("· Allow public, read-only links to datasets", {
        exact: false,
      }),
    ).toBeVisible();

    expect(sharing.getByText("Links expire after at most")).toBeVisible();
    const maxDays = sharing.getByLabelText(
      "Longest a public link lasts, in days",
    );
    expect(maxDays).toHaveAttribute("aria-disabled", "true");
    expect(maxDays).toHaveAccessibleDescription(NOT_IN_RELEASE);
    expect(maxDays.textContent.replace(/\u200b/g, "").trim()).toBe("");

    expect(card.textContent).not.toMatch(/coming soon|soon/i);
  });

  it("asks the server nothing, now or after a click on its controls", async () => {
    showCard();
    const sharing = within(screen.getByTestId("workspace-sharing-card"));
    fireEvent.click(sharing.getByRole("checkbox", { name: "Public links" }));
    fireEvent.mouseDown(
      sharing.getByLabelText("Longest a public link lasts, in days"),
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(h.get).not.toHaveBeenCalled();
    expect(h.put).not.toHaveBeenCalled();
    expect(h.snackbar).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("dims the switch label, its description and the expiry row evenly, and keeps the release line at full strength", () => {
    showCard();
    const card = screen.getByTestId("workspace-sharing-card");
    const sharing = within(card);
    const switchLabel = sharing.getByText("Public links");

    // The disabled switch does not grey its label on its own: the label
    // keeps its colour, and the shared dimming below is the only one.
    const labelColor = window.getComputedStyle(
      switchLabel.closest(".MuiFormControlLabel-label"),
    ).color;
    expect(labelColor).toBe(asComputed(theme.palette.text.primary));

    const rows = {
      switchLabel,
      description: sharing.getByText(
        "· Allow public, read-only links to datasets",
        { exact: false },
      ),
      expiry: sharing.getByText("Links expire after at most"),
      days: sharing.getByText("days"),
      expiryNote: sharing.getByText(
        "· links made before close at this limit too",
      ),
    };
    const shown = {};
    Object.entries(rows).forEach(([name, node]) => {
      shown[name] = shownOpacity(node, card);
    });
    expect(shown).toEqual({
      switchLabel: DIMMED,
      description: DIMMED,
      expiry: DIMMED,
      days: DIMMED,
      expiryNote: DIMMED,
    });

    expect(shownOpacity(sharing.getByText(NOT_IN_RELEASE), card)).toBe(1);
    expect(shownOpacity(sharing.getByText("Sharing"), card)).toBe(1);
  });

  it("titles the card like the section cards of the sibling Settings pages", () => {
    showCard();
    const title = screen.getByRole("heading", { level: 2, name: "Sharing" });
    const style = window.getComputedStyle(title);
    expect({
      fontSize: style.fontSize,
      fontWeight: style.fontWeight,
      color: style.color,
    }).toEqual({
      fontSize: theme.typography.s1.fontSize,
      fontWeight: String(theme.typography.fontWeightSemiBold),
      color: asComputed(theme.palette.text.primary),
    });
  });
});

describe("Sharing with the decision flipped to served", () => {
  it("renders the working card from the served policy and saves a change", async () => {
    h.get.mockResolvedValue({ data: policy() });
    h.put.mockResolvedValue({ data: policy({ public_links: false }) });
    showCard({ policyServed: true });

    const publicLinks = await screen.findByRole("checkbox", {
      name: "Public links",
    });
    expect(h.get).toHaveBeenCalledWith(POLICY_URL);
    expect(publicLinks).toBeEnabled();
    expect(publicLinks).toBeChecked();
    expect(
      screen.getByLabelText("Longest a public link lasts, in days"),
    ).toHaveTextContent("7");
    expect(screen.queryByText(NOT_IN_RELEASE)).toBeNull();

    fireEvent.click(publicLinks);
    await waitFor(() =>
      expect(h.put).toHaveBeenCalledWith(POLICY_URL, { public_links: false }),
    );
    await waitFor(() =>
      expect(h.snackbar).toHaveBeenCalledWith(
        "Public links are off for Design",
        { variant: "success" },
      ),
    );
  });

  it("shows the policy read-only, as before, to a reader who cannot change it", async () => {
    h.get.mockResolvedValue({ data: policy({ can_edit: false }) });
    showCard({ policyServed: true });
    expect(
      await screen.findByText("Only workspace admins can change this."),
    ).toBeVisible();
    expect(
      screen.getByRole("checkbox", { name: "Public links" }),
    ).toBeDisabled();
    expect(
      screen.getByLabelText("Longest a public link lasts, in days"),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("renders nothing, as before, while no policy has arrived", async () => {
    showCard({ policyServed: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledWith(POLICY_URL));
    expect(screen.queryByTestId("workspace-sharing-card")).toBeNull();
  });
});
