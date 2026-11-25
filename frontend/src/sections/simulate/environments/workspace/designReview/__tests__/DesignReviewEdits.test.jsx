import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "src/utils/test-utils";
import DesignReviewStage from "../DesignReviewStage";
import { REVIEW_COPY, REVIEW_STATUS } from "../designReview.constants";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  };
});

vi.mock("../../../hooks/useSimulationAccess", () => ({
  default: () => ({ canEdit: true, canRun: true }),
}));

const axios = (await import("src/utils/axios")).default;
const { enqueueSnackbar } = await import("notistack");

const base = {
  status: REVIEW_STATUS.AWAITING,
  created_at: new Date().toISOString(),
  tables: [],
  tools: [{ id: "t1", name: "lookup_order", source: "agent_prompt" }],
  rules: [{ id: "r1", text: "Greet the caller." }],
  use_cases: [{ id: "u1", text: "Check an order.", planned_scenarios: 1 }],
  actors: [],
  unsupported: [],
  brief: {},
  edits: [],
};

let review;

function renderStage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.propTypes = { children: PropTypes.node };
  return render(<DesignReviewStage envId="e1" />, { wrapper: Wrapper });
}

const approveButton = () =>
  screen.getByRole("button", { name: REVIEW_COPY.approve });

beforeEach(() => {
  vi.clearAllMocks();
  review = base;
  axios.get.mockImplementation(async (url) =>
    String(url).includes("impact")
      ? { data: { tools: [], rules: [] } }
      : { data: review },
  );
  axios.post.mockImplementation(async (url, edit) => {
    if (edit.op === "remove") {
      review = {
        ...review,
        tools: review.tools.map((tool) =>
          tool.id === edit.id ? { ...tool, removed: true } : tool,
        ),
        edits: [
          ...review.edits,
          { edit_id: `e${review.edits.length}`, ...edit },
        ],
      };
    } else {
      review = {
        ...review,
        rules: review.rules.map((rule) =>
          rule.id === edit.id
            ? { ...rule, text: edit.text, edited: true }
            : rule,
        ),
        edits: [
          ...review.edits.filter((one) => one.id !== edit.id),
          { edit_id: `e${review.edits.length}`, ...edit },
        ],
      };
    }
    return { data: review };
  });
});

async function editRule(user, text) {
  await user.click(screen.getByRole("button", { name: REVIEW_COPY.edit }));
  const field = screen.getByRole("textbox", { name: REVIEW_COPY.ruleLabel });
  await user.clear(field);
  await user.type(field, text);
  await user.click(screen.getByRole("button", { name: REVIEW_COPY.save }));
  await screen.findByText(text);
}

describe("DesignReviewStage edits", () => {
  it("drops the Undo bar once a later edit lands, so Undo cannot undo the wrong change", async () => {
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("Greet the caller.");

    await editRule(user, "Greet the caller by name.");

    await user.click(
      screen.getByRole("button", {
        name: REVIEW_COPY.removeNamed("lookup_order"),
      }),
    );
    expect(
      await screen.findByRole("button", { name: REVIEW_COPY.undo }),
    ).toBeInTheDocument();

    await editRule(user, "Greet the caller warmly.");

    expect(screen.queryByRole("button", { name: REVIEW_COPY.undo })).toBeNull();
    expect(axios.delete).not.toHaveBeenCalled();
  });

  it("undoes by the change's id and explains a change another tab already took back", async () => {
    const gone = "This change was already undone or replaced";
    axios.delete.mockRejectedValue({
      statusCode: 409,
      error: "design_edit_gone",
      message: gone,
    });
    const reviewReads = () =>
      axios.get.mock.calls.filter(([url]) => !String(url).includes("impact"))
        .length;
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("Greet the caller.");

    await user.click(
      screen.getByRole("button", {
        name: REVIEW_COPY.removeNamed("lookup_order"),
      }),
    );
    const editId = review.edits[review.edits.length - 1].edit_id;
    const readsBefore = reviewReads();
    await user.click(
      await screen.findByRole("button", { name: REVIEW_COPY.undo }),
    );

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(gone, { variant: "error" }),
    );
    expect(axios.delete).toHaveBeenCalledTimes(1);
    expect(String(axios.delete.mock.calls[0][0])).toContain(
      `/design-review/edits/${editId}/`,
    );
    await waitFor(() => expect(reviewReads()).toBeGreaterThan(readsBefore));
  });

  it("disables the last use case's Remove and says why", async () => {
    const user = userEvent.setup();
    renderStage();
    const row = (await screen.findByText("Check an order.")).closest(
      ".MuiStack-root",
    );
    const removeUseCase = within(row).getByRole("button", {
      name: REVIEW_COPY.remove,
    });

    expect(removeUseCase).toBeDisabled();
    await user.hover(removeUseCase.parentElement);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      REVIEW_COPY.keepOneUseCase,
    );
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("holds Approve while a rule is being changed", async () => {
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("Greet the caller.");
    expect(approveButton()).toBeEnabled();

    await user.click(screen.getByRole("button", { name: REVIEW_COPY.edit }));
    expect(approveButton()).toBeDisabled();

    await user.click(screen.getByText(REVIEW_COPY.cancel));
    expect(approveButton()).toBeEnabled();
  });

  it("saves a rule once when Enter is pressed again while the save is in flight", async () => {
    let finish;
    axios.post.mockImplementationOnce(
      (url, edit) =>
        new Promise((resolve) => {
          finish = () => {
            review = {
              ...review,
              rules: [{ id: "r1", text: edit.text, edited: true }],
            };
            resolve({ data: review });
          };
        }),
    );
    const user = userEvent.setup();
    renderStage();
    await screen.findByText("Greet the caller.");

    await user.click(screen.getByRole("button", { name: REVIEW_COPY.edit }));
    const field = screen.getByRole("textbox", { name: REVIEW_COPY.ruleLabel });
    await user.type(field, " Always.{enter}");
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    await user.type(field, "{enter}");
    expect(axios.post).toHaveBeenCalledTimes(1);

    finish();
    await screen.findByText("Greet the caller. Always.");
  });
});
