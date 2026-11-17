import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { select } from "src/theme/overrides/components/select";

import EditKeyDialog from "../EditKeyDialog";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("../hooks/useApiKeys", () => ({
  useUpdateApiKey: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../useKeyAccessOptions", () => ({
  useKeyAccessOptions: () => ({ modelOptions: [] }),
}));
vi.mock("../hooks/useKeyProviderOptions", () => ({
  useKeyProviderOptions: () => ({ options: ["openai"], labelFor: (id) => id }),
}));

// The app's shared override (theme/overrides/components/select.js) paints a
// focused, raised label with the paper colour and 5 px of side padding, a
// mask for a label inside the field's outline. Render with it, in dark mode,
// where the paper colour shows.
const base = createTheme({ palette: palette("dark"), typography });
const darkTheme = createTheme({
  palette: palette("dark"),
  typography,
  components: select(base),
});

const keyData = {
  id: "key-a",
  name: "CI key",
  owner: "ci-service",
  expiresAt: "2099-06-01T10:20:30.000Z",
};

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={darkTheme}>
        <MemoryRouter>
          <EditKeyDialog
            open
            onClose={() => {}}
            keyData={keyData}
            gatewayId="gw-1"
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

// In the Edit API key dialog the focused Name, Owner and Expiry labels sat on
// a full-width strip of the paper colour, a dark bar in dark mode, pushed
// 5 px right of the other labels. These labels sit above their fields, as in
// Create API key and the dialog's two access fields.
describe("Edit API key dialog labels (layout)", () => {
  it.each([
    ["Name", () => screen.getByRole("textbox", { name: /^Name/ }), /^Name/],
    ["Owner", () => screen.getByRole("textbox", { name: "Owner" }), "Owner"],
    ["Expiry", () => screen.getByLabelText("Expiry (UTC)"), "Expiry (UTC)"],
  ])("draws no band behind the focused %s label", async (_n, field, text) => {
    renderDialog();
    const input = field();
    act(() => input.focus());
    const label = screen.getByText(text, { selector: "label" });
    await waitFor(() => expect(label).toHaveClass("Mui-focused"));
    const style = window.getComputedStyle(label);
    expect(style.backgroundColor).toMatch(/^(transparent|rgba\(0, 0, 0, 0\))$/);
    expect(style.paddingLeft).toMatch(/^0(px)?$/);
  });
});
