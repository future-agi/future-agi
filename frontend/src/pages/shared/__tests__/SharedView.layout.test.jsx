import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createTheme } from "@mui/material/styles";
import { enqueueSnackbar } from "notistack";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { copyToClipboard } from "src/utils/utils";
import SharedView from "../SharedView";

// C6: the shared trace page (a View only link) at 1024, 1280 and 1440.
// - The header cut the trace id to its first 12 characters plus "..." though
//   about 1,100 px of the bar was empty, and the full id was nowhere on the
//   page. It now shows the whole id (cut with an ellipsis only when the bar
//   is too narrow) with a Copy ID button beside it.
// - The Trace Tree label and Search box sat 8 px from the window's left
//   edge, against the header's 16 px. The tree's toolbar pads itself 8 px
//   (theme.spacing(1)); the pane around it now adds 8 px more.
// - "View only" (and the id) used text.disabled, about 2.6:1 on the dark
//   page. They now use text.secondary.
// The checks read the styles and classes the page sets, never jsdom layout.

const mocks = vi.hoisted(() => ({ useResolveSharedLink: vi.fn() }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ token: "share-token" }),
}));
vi.mock("react-helmet-async", async (importOriginal) => ({
  ...(await importOriginal()),
  // eslint-disable-next-line react/prop-types
  Helmet: ({ children }) => <>{children}</>,
}));
vi.mock("src/api/shared-links", async (importOriginal) => ({
  ...(await importOriginal()),
  useResolveSharedLink: mocks.useResolveSharedLink,
}));
vi.mock("src/components/traceDetail/TraceTreeV2", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => <div data-testid="trace-tree" />,
}));
vi.mock(
  "src/components/traceDetail/SpanDetailPane",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
// The trace page never renders the issue, voice or simulation views. Full
// stubs keep their import trees (the Error Feed issue page, the audio player)
// out of this file; SharedView.test.jsx covers them.
vi.mock("../SharedErrorIssueView", () => ({ default: () => null }));
vi.mock("../SharedVoiceView", () => ({ default: () => null }));
vi.mock("../SharedSimulationCallView", () => ({ default: () => null }));
vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard: vi.fn(async () => true),
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

// The header glyph of a shared trace, the copy glyph and the empty right
// pane's glyph.
for (const name of [
  "solar:share-linear",
  "solar:copy-linear",
  "solar:cursor-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

const TRACE_ID = "63f02f92-aaf1-4c3e-9b7d-5e2a1f0c8d47";
const light = createTheme({ palette: palette("light") });
const dark = createTheme({ palette: palette("dark") });

const resolvedTrace = () => ({
  data: {
    resource_type: "trace",
    resource_id: TRACE_ID,
    access_type: "public",
    data: {
      observation_spans: [
        {
          observation_span: {
            id: "span-1",
            trace: TRACE_ID,
            name: "ChatOpenAI",
          },
          children: [],
        },
      ],
    },
  },
  isLoading: false,
  isError: false,
  error: null,
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.useResolveSharedLink.mockReturnValue(resolvedTrace());
});

describe("SharedView View only trace page layout (C6)", () => {
  it("shows the whole trace id in the header, cut with an ellipsis only when it does not fit", () => {
    render(<SharedView />, { theme: light });

    const id = screen.getByText(TRACE_ID);
    // noWrap: one line, ellipsis only when the bar is too narrow for it.
    expect(id).toHaveClass("MuiTypography-noWrap");
    // No hard cut to "63f02f92-aaf..." anywhere on the page.
    expect(screen.queryByText(/\.\.\.$/)).not.toBeInTheDocument();
  });

  it("copies the whole id from the Copy ID button beside it", async () => {
    const user = userEvent.setup();
    render(<SharedView />, { theme: light });

    await user.click(screen.getByRole("button", { name: "Copy ID" }));

    expect(copyToClipboard).toHaveBeenCalledWith(TRACE_ID);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Copied",
        expect.objectContaining({ variant: "info" }),
      ),
    );
  });

  it("insets the Trace Tree pane so its label and Search box sit on the header's 16 px gutter", () => {
    render(<SharedView />, { theme: light });

    // "View only" sits directly in the header bar.
    const header = screen.getByText("View only").parentElement;
    expect(header).toHaveStyle({ paddingLeft: light.spacing(2) });
    // The tree's toolbar adds its own theme.spacing(1); the pane adds the
    // other theme.spacing(1), so the label starts 16 px in like the header.
    const treePane = screen.getByTestId("trace-tree").parentElement;
    expect(treePane).toHaveStyle({ paddingLeft: light.spacing(1) });
  });

  it("draws View only and the id in the readable secondary text colour in dark theme", () => {
    render(<SharedView />, { theme: dark });

    const viewOnly = screen.getByText("View only");
    expect(viewOnly).toHaveStyle({ color: dark.palette.text.secondary });
    expect(viewOnly).not.toHaveStyle({ color: dark.palette.text.disabled });

    const id = screen.getByText(TRACE_ID);
    expect(id).toHaveStyle({ color: dark.palette.text.secondary });
    expect(id).not.toHaveStyle({ color: dark.palette.text.disabled });
  });
});
