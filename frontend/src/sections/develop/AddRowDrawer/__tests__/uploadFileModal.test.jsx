import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { palette } from "src/theme/palette";
import UploadFileModal, { roleForHeader } from "../UploadFileModal";

vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));

const theme = createTheme({ palette: palette("light") });

const renderModal = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ThemeProvider theme={theme}>
        <UploadFileModal
          open
          onClose={() => {}}
          refreshGrid={() => {}}
          datasetId="ds-1"
          closeDrawer={() => {}}
        />
      </ThemeProvider>
    </QueryClientProvider>,
  );

const CSV = [
  "question,expected_answer,split",
  ...Array.from(
    { length: 12 },
    (_, i) =>
      `"Where is order ${i + 1}?","It ships Friday",${i % 4 ? "train" : "test"}`,
  ),
].join("\n");

describe("UploadFileModal (IT-R4 G5 P4)", () => {
  it("guesses a role per header", () => {
    expect(roleForHeader("question")).toBe("input");
    expect(roleForHeader("expected_answer")).toBe("expected");
    expect(roleForHeader("split")).toBe("split");
    // W3-1: one role list for both upload dialogs; `Skip` is now `Ignore`.
    expect(roleForHeader("id")).toBe("ignore");
    expect(roleForHeader("customer_tier")).toBe("metadata");
  });

  it("before a file: no primary button, a Format details popover, rows wording", () => {
    renderModal();
    expect(screen.queryByRole("button", { name: /^Add rows$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Add .* rows/ })).toBeNull();
    expect(screen.queryByText(/datapoint/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Format details" }));
    expect(screen.getByText("Download a sample")).toBeInTheDocument();
  });

  it("a 3-column CSV shows its rows, a role per column and the add button", async () => {
    const { container } = renderModal();
    const file = new File([CSV], "questions.csv", { type: "text/csv" });
    // jsdom's File has no text(); browsers do.
    if (typeof file.text !== "function") file.text = async () => CSV;
    const input = container.ownerDocument.querySelector('input[type="file"]');
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() =>
      expect(screen.getByTestId("upload-row-count")).toHaveTextContent(
        "12 rows",
      ),
    );
    expect(screen.getByText("question")).toBeInTheDocument();
    expect(screen.getByText("Where is order 1?")).toBeInTheDocument();
    expect(screen.getByText("Where is order 8?")).toBeInTheDocument();
    expect(screen.queryByText("Where is order 9?")).toBeNull();
    expect(screen.getByLabelText("split role")).toHaveTextContent("Split");
    expect(screen.getByLabelText("question role")).toHaveTextContent("Input");
    expect(
      screen.getByRole("button", { name: "Add 12 rows to pending changes" }),
    ).toBeEnabled();
  });
});
