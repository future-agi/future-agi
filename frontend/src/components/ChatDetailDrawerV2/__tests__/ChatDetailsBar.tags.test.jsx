import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import ChatDetailsBar from "../ChatDetailsBar";

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
      <ChatDetailsBar data={data} />
    </QueryClientProvider>,
  );

describe("ChatDetailsBar tag targets", () => {
  it("shows chips but prevents editing when tag_target is explicitly null", () => {
    renderBar({
      trace_id: "trace-1",
      tag_target: null,
      tags: [{ name: "chat-tag" }],
    });
    expect(screen.getByText("chat-tag")).toHaveAttribute(
      "data-read-only",
      "true",
    );
    expect(
      screen.queryByRole("button", { name: "tag" }),
    ).not.toBeInTheDocument();
  });

  it("keeps legacy rows without tag_target editable", () => {
    renderBar({ trace_id: "trace-1", tags: [{ name: "chat-tag" }] });
    expect(screen.getByText("chat-tag")).toHaveAttribute(
      "data-read-only",
      "false",
    );
    expect(screen.getByRole("button", { name: "tag" })).toBeInTheDocument();
  });
});
