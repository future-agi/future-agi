import { beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { endOfDay } from "date-fns";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import CreateApiKey from "../CreateApiKey";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { post: mocks.post },
  endpoints: {
    keys: { generateSecretKey: "/accounts/key/generate_secret_key/" },
  },
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg-color" />,
}));

// Stand-in for the MUI picker: the dialog's job is turning the picked date
// into the request, not the calendar UI itself.
vi.mock("@mui/x-date-pickers/DatePicker", () => {
  const DatePicker = ({ label, onChange }) => (
    <input
      aria-label={label}
      onChange={(e) =>
        onChange(e.target.value ? new Date(`${e.target.value}T12:00:00`) : null)
      }
    />
  );
  DatePicker.propTypes = { label: PropTypes.string, onChange: PropTypes.func };
  return { DatePicker };
});

const renderDialog = () => {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CreateApiKey open onClose={vi.fn()} refreshGrid={vi.fn()} />
    </QueryClientProvider>,
  );
};

describe("CreateApiKey expiry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.post.mockResolvedValue({
      data: {
        status: true,
        result: {
          key_id: "k1",
          key_name: "ci key",
          api_key: "api",
          masked_api_key: "a**i",
          secret_key: "secret",
          masked_secret_key: "s**t",
          expires_at: null,
        },
      },
    });
  });

  it("creates a key without expires_at when no date is picked", async () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText(/Key name/), {
      target: { value: "ci key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith(
      "/accounts/key/generate_secret_key/",
      { key_name: "ci key" },
    );
  });

  it("sends the end of the picked day as expires_at", async () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText(/Key name/), {
      target: { value: "ci key" },
    });
    fireEvent.change(screen.getByLabelText("Expires on (optional)"), {
      target: { value: "2099-01-15" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith(
      "/accounts/key/generate_secret_key/",
      {
        key_name: "ci key",
        expires_at: endOfDay(new Date(2099, 0, 15)).toISOString(),
      },
    );
  });

  it("blocks creation for a past expiry instead of dropping it", () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText(/Key name/), {
      target: { value: "ci key" },
    });
    fireEvent.change(screen.getByLabelText("Expires on (optional)"), {
      target: { value: "2000-01-15" },
    });

    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText(/Key name/), { key: "Enter" });
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
