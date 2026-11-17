/* eslint-disable react/prop-types */
import React from "react";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import OrgSettings from "../OrgSettings";

// Renaming the organization reloads the page so every part of the app reads
// the new name. The confirmation must survive that reload: the page shows it
// once after the reload instead of just before it, where nobody can read it.
// It belongs to the person who renamed and to that organization: anyone else
// opening Org Settings in the same tab, or the same person in another
// organization, is never told a rename succeeded.

const h = vi.hoisted(() => ({
  patch: vi.fn(),
  snackbar: vi.fn(),
  reload: vi.fn(),
  userId: "user-1",
  orgId: "org-1",
}));

vi.mock("src/utils/axios", () => ({
  default: { patch: h.patch },
  endpoints: { organizations: { update: "/accounts/organizations/update/" } },
}));
vi.mock("notistack", () => ({ enqueueSnackbar: h.snackbar }));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: h.userId === null ? null : { id: h.userId },
  }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: h.orgId,
    currentOrganizationName: "Acme",
    currentOrganizationDisplayName: "Acme",
  }),
}));
vi.mock("src/sections/settings/Security/OrgTwoFactorPolicySection", () => ({
  default: () => null,
}));
// The icon element as the page draws it (Iconify forwards its props and ref,
// so the tooltip names it); only the icon artwork is left out.
vi.mock("src/components/iconify", () => ({
  default: React.forwardRef(function Icon(
    { icon: _icon, sx: _sx, color: _color, ...props },
    ref,
  ) {
    return <svg ref={ref} {...props} />;
  }),
}));

const CONFIRMATION = "Organization name updated successfully.";
const EDIT = "Edit organization name";
// The pencil's name, whatever its case (the wording test pins the case).
const EDIT_CONTROL = /^edit organization name$/i;
const originalLocation = Object.getOwnPropertyDescriptor(window, "location");

function showOrgSettings() {
  return render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { mutations: { retry: false } } })
        }
      >
        <ThemeProvider
          theme={createTheme({ palette: palette("light"), typography })}
        >
          <OrgSettings />
        </ThemeProvider>
      </QueryClientProvider>
    </HelmetProvider>,
  );
}

const shownMessages = () => h.snackbar.mock.calls.map(([message]) => message);

// The page after the reload (jsdom cannot reload: the page is mounted again).
async function reloadedPage() {
  cleanup();
  h.snackbar.mockReset();
  showOrgSettings();
  await act(async () => {});
}

beforeEach(() => {
  sessionStorage.clear();
  h.userId = "user-1";
  h.orgId = "org-1";
  h.patch.mockReset().mockResolvedValue({ data: { status: true } });
  h.snackbar.mockReset();
  h.reload.mockReset();
  // jsdom cannot reload; the page's reload is observed instead.
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: {
      href: "http://localhost/dashboard/settings/org-settings",
      pathname: "/dashboard/settings/org-settings",
      reload: h.reload,
    },
  });
});
afterEach(() => {
  cleanup();
  Object.defineProperty(window, "location", originalLocation);
});

async function rename(to) {
  fireEvent.click(screen.getByLabelText(EDIT_CONTROL));
  const field = screen.getByRole("textbox");
  fireEvent.change(field, { target: { value: to } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
  });
  await waitFor(() => expect(h.reload).toHaveBeenCalledTimes(1));
}

describe("Org Settings rename confirmation", () => {
  it("still reloads after a rename, so the whole app reads the new name", async () => {
    showOrgSettings();
    await rename("Acme Labs");
    expect(h.patch).toHaveBeenCalledWith("/accounts/organizations/update/", {
      name: "Acme Labs",
      display_name: "Acme Labs",
    });
    expect(sessionStorage.getItem("organizationName")).toBe("Acme Labs");
    expect(sessionStorage.getItem("organizationDisplayName")).toBe("Acme Labs");
  });

  it("shows the confirmation after the reload, once", async () => {
    showOrgSettings();
    await rename("Acme Labs");
    // Nothing is shown in the instant before the reload.
    expect(shownMessages()).not.toContain(CONFIRMATION);

    await reloadedPage();
    await waitFor(() => expect(shownMessages()).toEqual([CONFIRMATION]));
    expect(h.snackbar).toHaveBeenCalledWith(CONFIRMATION, {
      variant: "success",
    });

    // A later visit or reload does not repeat it.
    await reloadedPage();
    expect(shownMessages()).toEqual([]);
  });

  it("waits for the signed-in person and organization before showing it", async () => {
    showOrgSettings();
    await rename("Acme Labs");

    h.orgId = null;
    await reloadedPage();
    expect(shownMessages()).toEqual([]);

    h.orgId = "org-1";
    await reloadedPage();
    await waitFor(() => expect(shownMessages()).toEqual([CONFIRMATION]));
  });

  it("is not shown to another person who opens Org Settings in the same tab", async () => {
    showOrgSettings();
    await rename("Acme Labs");

    h.userId = "user-2";
    await reloadedPage();
    expect(shownMessages()).toEqual([]);

    // Discarded, not kept for later.
    h.userId = "user-1";
    await reloadedPage();
    expect(shownMessages()).toEqual([]);
  });

  it("is not shown in another organization opened in the same tab", async () => {
    showOrgSettings();
    await rename("Acme Labs");

    h.orgId = "org-2";
    await reloadedPage();
    expect(shownMessages()).toEqual([]);

    // Discarded, not kept for later.
    h.orgId = "org-1";
    await reloadedPage();
    expect(shownMessages()).toEqual([]);
  });

  it("shows no confirmation when the rename fails, and does not reload", async () => {
    h.patch.mockRejectedValue({
      response: { data: { result: "Name is taken." } },
    });
    showOrgSettings();
    fireEvent.click(screen.getByLabelText(EDIT_CONTROL));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Taken" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
    });
    await waitFor(() => expect(shownMessages()).toEqual(["Name is taken."]));
    expect(h.reload).not.toHaveBeenCalled();

    await reloadedPage();
    expect(shownMessages()).toEqual([]);
  });
});

describe("Org Settings wording", () => {
  it("names the page, the field and the edit action in sentence case", async () => {
    showOrgSettings();
    expect(
      screen.getByRole("heading", { name: "Organization settings" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Organization settings"));
    expect(screen.getByText("Organization name")).toBeInTheDocument();

    fireEvent.mouseOver(screen.getByLabelText(EDIT));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(EDIT);

    expect(screen.queryByText("Organization Settings")).toBeNull();
    expect(screen.queryByText("Organization Name")).toBeNull();
  });
});
