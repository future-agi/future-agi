import { createTheme, ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { button } from "src/theme/overrides/components/button";
import { loadingButton } from "src/theme/overrides/components/loading-button";

import SaveAndCommit from "../SaveAndCommit";

// The app's light palette with the button overrides that decide how an
// outlined LoadingButton is coloured, built the way src/theme/index.jsx does.
const theme = createTheme({ palette: palette("light") });
theme.components = { ...button(theme), ...loadingButton(theme) };

function renderDialog() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider theme={theme}>
        <MemoryRouter>
          <SaveAndCommit
            open
            onClose={vi.fn()}
            data={{ version: "v2", isDraft: true }}
            promptName="Prompt"
          />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("SaveAndCommit Commit button", () => {
  it("stops looking disabled once a commit message is typed (#1384)", () => {
    renderDialog();
    const commit = screen.getByRole("button", { name: "Commit" });

    // Empty message: the button is really disabled.
    expect(commit).toBeDisabled();

    fireEvent.change(
      screen.getByPlaceholderText("Enter a commit message for this version..."),
      { target: { value: "Tighten the system prompt" } },
    );

    // With a message the button is clickable and renders as an active
    // outlined button, not in the disabled grey.
    expect(commit).toBeEnabled();
    expect(commit).toHaveStyle({ color: theme.palette.text.primary });
    expect(commit).not.toHaveStyle({ color: theme.palette.text.disabled });
  });
});
