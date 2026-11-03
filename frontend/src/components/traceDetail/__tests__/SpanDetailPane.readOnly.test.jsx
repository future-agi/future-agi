import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import SpanDetailPane from "../SpanDetailPane";

vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: ({ renderActions }) => (
    <div data-testid="scores-list">{renderActions}</div>
  ),
}));

const evalsProps = vi.hoisted(() => vi.fn());

vi.mock("../EvalsTabView", async (importOriginal) => ({
  ...(await importOriginal()),
  default: (props) => {
    evalsProps(props);
    return <div data-testid="evals-tab" />;
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

const entry = {
  observation_span: {
    id: "span-1",
    trace: "trace-1",
    name: "llm call",
    tags: [{ name: "billing", color: "#2563EB" }],
  },
};

const renderPane = (props) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SpanDetailPane
        entry={entry}
        projectId="project-1"
        onClose={() => {}}
        {...props}
      />
    </QueryClientProvider>,
  );

describe("SpanDetailPane read-only", () => {
  it("offers actions, tag editing and labelling when it can act", async () => {
    const user = userEvent.setup();
    renderPane({ onAction: vi.fn() });

    expect(screen.getByRole("button", { name: "Actions" })).toBeInTheDocument();
    expect(screen.getByText("tag")).toBeInTheDocument();

    await user.click(screen.getByText("Annotations"));
    expect(
      screen.getByRole("button", { name: "Add Label" }),
    ).toBeInTheDocument();
  });

  it("hides the tag input and the Annotations tab when read-only", () => {
    renderPane({ readOnly: true, onAction: vi.fn() });

    expect(screen.getByText("billing")).toBeInTheDocument();
    expect(screen.queryByText("tag")).toBeNull();
    expect(screen.queryByText("Annotations")).toBeNull();
    expect(screen.queryByTestId("scores-list")).toBeNull();
  });

  it("offers Fix with Falcon in the Evals tab only when it can act", async () => {
    const user = userEvent.setup();
    const { unmount } = renderPane({ onAction: vi.fn() });
    await user.click(screen.getByText("Evals"));
    expect(evalsProps.mock.lastCall[0].showFixWithFalcon).toBe(true);
    unmount();

    renderPane({ readOnly: true });
    await user.click(screen.getByText("Evals"));
    expect(evalsProps.mock.lastCall[0].showFixWithFalcon).toBe(false);
  });
});
