import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: vi.fn(), post: vi.fn() },
}));

const axios = (await import("src/utils/axios")).default;
const { default: KnowledgeBasePicker } = await import("../KnowledgeBasePicker");

const page = (rows) => ({
  data: { result: { table_data: rows, total_rows: rows.length } },
});

describe("KnowledgeBasePicker", () => {
  it("offers only ready Knowledge bases and files, and adds the ticked ones", async () => {
    axios.get.mockResolvedValue(
      page([
        { id: "kb-a", name: "Policies", status: "Completed" },
        {
          id: "00000000-0000-4000-8000-00000000000b",
          name: "Orders",
          status: "PartialCompleted",
        },
        { id: "kb-c", name: "Tickets", status: "Processing" },
        { id: "kb-d", name: "Broken", status: "Failed" },
        { id: "kb-e", name: "Leaving", status: "Deleting" },
      ]),
    );
    axios.post.mockResolvedValue(
      page([
        { id: "f-1", name: "orders.csv", file_size: 10, status: "Completed" },
        { id: "f-2", name: "scan.pdf", file_size: 20, status: "Failed" },
        { id: "f-3", name: "new.csv", file_size: 30, status: "Processing" },
      ]),
    );
    const onPick = vi.fn();
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <KnowledgeBasePicker
          envId="env-1"
          open
          onClose={vi.fn()}
          onPick={onPick}
        />
      </QueryClientProvider>,
    );

    await screen.findByText("Policies");
    expect(screen.queryByText("Broken")).toBeNull();
    expect(screen.queryByText("Leaving")).toBeNull();
    expect(screen.getByRole("button", { name: /Tickets/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /Orders/ }));
    await screen.findByText("orders.csv");
    expect(screen.getByText("Couldn't be processed")).toBeInTheDocument();
    const [ready, failed, processing] = screen.getAllByRole("checkbox");
    expect(failed).toBeDisabled();
    expect(processing).toBeDisabled();

    fireEvent.click(ready);
    fireEvent.click(screen.getByRole("button", { name: "Add selected (1)" }));
    await waitFor(() =>
      expect(onPick).toHaveBeenCalledWith({
        kind: "knowledge_base",
        kb: expect.objectContaining({
          id: "00000000-0000-4000-8000-00000000000b",
          name: "Orders",
        }),
        files: [expect.objectContaining({ id: "f-1", name: "orders.csv" })],
      }),
    );
  });
});
