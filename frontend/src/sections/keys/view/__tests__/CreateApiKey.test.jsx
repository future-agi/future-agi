import { beforeEach, describe, expect, it, vi } from "vitest";
import PropTypes from "prop-types";
import { endOfDay } from "date-fns";
import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { handleError } from "src/utils/queryErrorHandler";
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
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: { mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CreateApiKey open onClose={vi.fn()} refreshGrid={vi.fn()} />
    </QueryClientProvider>,
  );
};

describe("CreateApiKey", () => {
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

  it("explains when to copy both keys after creation", async () => {
    renderDialog();
    const notice = /copy the API key and secret key before closing/i;
    expect(screen.queryByText(notice)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Key name/), {
      target: { value: "ci key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText(notice)).toHaveTextContent(
      /only copy them here/i,
    );
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
  });

  it("marks only a rejected duplicate name and clears it on edit", async () => {
    const expiryError = "Expiry must be in the future.";
    mocks.post
      .mockRejectedValueOnce({
        statusCode: 400,
        code: "invalid",
        result: "Keys with this already name exists.",
      })
      .mockRejectedValueOnce({
        statusCode: 400,
        code: "invalid",
        result: expiryError,
      });
    renderDialog();
    const name = screen.getByRole("textbox", { name: /Key name/ });
    const next = screen.getByRole("button", { name: "Next" });
    fireEvent.change(name, { target: { value: "ci key" } });
    fireEvent.click(next);

    expect(await screen.findByRole("alert")).toHaveTextContent(/another name/i);
    expect(name).toBeInvalid();
    expect(name).toHaveAccessibleDescription(/name already exists/i);
    expect(enqueueSnackbar).not.toHaveBeenCalled();

    fireEvent.change(name, { target: { value: "new name" } });
    expect(name).toBeValid();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(next);
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(expiryError, {
        variant: "error",
      }),
    );
    expect(name).toBeValid();
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
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
