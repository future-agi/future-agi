import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, userEvent } from "src/utils/test-utils";
import SpanDetailPane from "../SpanDetailPane";

// The same trace / span id can exist in several projects: the drawer's
// Annotations tab must read the scores of the project the drawer shows.
const { scoresListProps } = vi.hoisted(() => ({ scoresListProps: [] }));

vi.mock("src/components/ScoresListSection/ScoresListSection", () => ({
  default: (props) => {
    scoresListProps.push(props);
    return <div data-testid="scores-list" />;
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

describe("SpanDetailPane annotations tab", () => {
  it("reads the scores of the drawer's project", async () => {
    const user = userEvent.setup();

    render(
      <QueryClientProvider client={new QueryClient()}>
        <SpanDetailPane
          entry={{
            observation_span: {
              id: "span-1",
              trace: "trace-1",
              name: "llm call",
            },
          }}
          projectId="project-1"
          onClose={() => {}}
        />
      </QueryClientProvider>,
    );

    await user.click(screen.getByText("Annotations"));

    expect(screen.getByTestId("scores-list")).toBeInTheDocument();
    expect(scoresListProps.at(-1)).toEqual(
      expect.objectContaining({
        sourceType: "observation_span",
        sourceId: "span-1",
        secondarySourceType: "trace",
        secondarySourceId: "trace-1",
        projectId: "project-1",
      }),
    );
  });
});
