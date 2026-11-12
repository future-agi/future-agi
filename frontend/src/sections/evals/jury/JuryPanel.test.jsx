import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import JuryPanel from "./JuryPanel";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn() },
}));
vi.mock("../components/ModelSelector", () => ({
  default: () => <span>Saved child model</span>,
}));
const child = {
  child_id: "9ad997e3-977a-4fd0-b7ee-07d83176b808",
  child_name: "Accuracy",
};
const seat = {
  id: "judge-a",
  kind: "eval",
  child_id: child.child_id,
  level: "row",
  weight: 1,
};
const path = "/model-hub/eval-templates/parent/composite/";

function mount(definition = { seats: [] }) {
  axios.get.mockImplementation(async (url) => ({
    data: {
      result: url.endsWith("config/")
        ? definition
        : { runs: [], run_id: null, seats: [] },
    },
  }));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <JuryPanel templateId="parent" childrenList={[child]} editable />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("saved composite jury", () => {
  it("saves new seats to the strict jury configuration route", async () => {
    axios.put.mockImplementation(async (_, body) => ({
      data: { result: body },
    }));
    mount();
    await screen.findByText(
      "No jury configured. Add a judge or check to get started.",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Add judge", exact: true }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Save jury", exact: true }),
    );
    await waitFor(() => expect(axios.put).toHaveBeenCalledTimes(1));
    expect(axios.put.mock.calls[0][0]).toBe(`${path}jury/config/`);
    expect(axios.put.mock.calls[0][1].seats).toEqual([
      expect.objectContaining({
        child_id: child.child_id,
        kind: "eval",
        level: "row",
        weight: 1,
      }),
    ]);
  });

  it("blocks execution with unsaved edits", async () => {
    mount({ seats: [seat] });
    await screen.findByText("judge-a");
    fireEvent.change(screen.getByLabelText("Seat weight"), {
      target: { value: "2" },
    });
    expect(
      screen.getByRole("button", { name: "Run saved jury", exact: true }),
    ).toBeDisabled();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("rejects invalid mapping without making a request", async () => {
    mount({ seats: [seat] });
    await screen.findByText("judge-a");
    fireEvent.change(screen.getByLabelText("Mapped inputs (JSON)"), {
      target: { value: "[]" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Run saved jury", exact: true }),
    );
    await screen.findByRole("alert");
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("submits saved inputs through the existing evaluator, not a fabricated result", async () => {
    axios.post.mockResolvedValue({
      data: { result: { evaluation_id: "run-id" } },
    });
    mount({ seats: [seat] });
    await screen.findByText("judge-a");
    fireEvent.change(screen.getByLabelText("Mapped inputs (JSON)"), {
      target: { value: '{"output":"sample"}' },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Run saved jury", exact: true }),
    );
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(`${path}execute/`, {
        mapping: { output: "sample" },
      }),
    );
  });
});
