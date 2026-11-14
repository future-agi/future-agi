import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import CallDetailsBar from "../CallDetailsBar";

vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: () => ({ data: null }),
}));
vi.mock("src/components/traceDetail/TagChip", () => ({
  default: ({ name, readOnly }) => (
    <span data-read-only={String(Boolean(readOnly))}>{name}</span>
  ),
}));

const renderBar = (data) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CallDetailsBar
        data={{ call_type: "voice", status: "completed", ...data }}
        onAction={() => {}}
      />
    </QueryClientProvider>,
  );

describe("CallDetailsBar tag targets", () => {
  it("shows chips but prevents editing when tag_target is explicitly null", () => {
    renderBar({
      trace_id: "trace-1",
      tag_target: null,
      tags: [{ name: "voice-tag" }],
    });
    expect(screen.getByText("voice-tag")).toHaveAttribute(
      "data-read-only",
      "true",
    );
    expect(
      screen.queryByRole("button", { name: "tag" }),
    ).not.toBeInTheDocument();
  });

  it("keeps legacy rows without tag_target editable", () => {
    renderBar({ trace_id: "trace-1", tags: [{ name: "voice-tag" }] });
    expect(screen.getByText("voice-tag")).toHaveAttribute(
      "data-read-only",
      "false",
    );
    expect(screen.getByRole("button", { name: "tag" })).toBeInTheDocument();
  });
});
