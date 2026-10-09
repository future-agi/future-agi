import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import CallDetailsBar from "../CallDetailsBar";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));

vi.mock("src/components/traceDetail/TagChip", () => ({
  default: ({ name }) => <span>{name}</span>,
}));

vi.mock("src/components/traceDetail/TagInput", () => ({
  default: () => <input aria-label="tag name" />,
}));

const renderWithClient = (ui) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
};

describe("CallDetailsBar", () => {
  it("omits hidden actions from the voice actions menu", async () => {
    renderWithClient(
      <CallDetailsBar
        data={{
          trace_id: "trace-1",
          call_type: "voice",
          status: "completed",
        }}
        onAction={vi.fn()}
        hiddenActionIds={["queue", "tags"]}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /actions/i }));

    const menu = screen.getByRole("menu");
    expect(
      within(menu).queryByRole("menuitem", {
        name: /add to annotation queue/i,
      }),
    ).not.toBeInTheDocument();
    expect(
      within(menu).queryByRole("menuitem", { name: /add tags/i }),
    ).not.toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: /annotate/i }),
    ).toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: /move to dataset/i }),
    ).toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: /download raw data/i }),
    ).toBeInTheDocument();
  });

  describe("background noise chip", () => {
    const chip = () => screen.queryByText(/^Background :/);

    it("shows where the scenario puts the caller", () => {
      renderWithClient(
        <CallDetailsBar
          data={{
            call_type: "voice",
            background_noise: "vehicle",
            background_noise_label: "In a car",
          }}
        />,
      );

      expect(chip()).toHaveTextContent("Background : In a car");
      expect(within(chip()).getByTestId("iconify")).toHaveAttribute(
        "data-icon",
        "solar:soundwave-linear",
      );
    });

    it("mutes a quiet line", () => {
      renderWithClient(
        <CallDetailsBar
          data={{
            call_type: "voice",
            background_noise: "quiet line",
            background_noise_label: "Quiet line",
          }}
        />,
      );

      expect(chip()).toHaveTextContent("Background : Quiet line");
      expect(within(chip()).getByTestId("iconify")).toHaveAttribute(
        "data-icon",
        "solar:volume-cross-linear",
      );
    });

    it("leaves the chip out when the call has no background noise", () => {
      renderWithClient(
        <CallDetailsBar data={{ call_type: "voice", status: "completed" }} />,
      );

      expect(chip()).toBeNull();
    });

    it("explains the chip on hover", async () => {
      renderWithClient(
        <CallDetailsBar
          data={{
            background_noise: "street",
            background_noise_label: "Street",
          }}
        />,
      );

      await userEvent.hover(chip());

      expect(await screen.findByRole("tooltip")).toHaveTextContent(
        "Where the scenario puts the caller",
      );
    });
  });
});
