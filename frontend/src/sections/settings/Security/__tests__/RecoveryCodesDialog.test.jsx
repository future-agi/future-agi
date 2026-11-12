import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  fireEvent,
  within,
  act,
} from "src/utils/test-utils";
import RecoveryCodesDialog from "../RecoveryCodesDialog";
import PasskeySection from "../PasskeySection";
import RecoveryCodesSection from "../RecoveryCodesSection";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  onStatusChange: vi.fn(),
  registerDialogProps: null,
  codes: ["aaaa-1111", "bbbb-2222", "cccc-3333", "dddd-4444"],
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    delete: vi.fn(),
  },
  endpoints: {
    passkey: {
      list: "/accounts/passkeys/",
      detail: (id) => `/accounts/passkeys/${id}/`,
    },
    twoFactor: {
      recoveryCodes: {
        regenerate: "/accounts/2fa/recovery-codes/regenerate/",
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/iconify", () => ({
  default: () => <span data-testid="iconify" />,
}));

vi.mock("src/components/content-skeleton", () => ({
  default: () => <div data-testid="content-skeleton" />,
}));

vi.mock("@simplewebauthn/browser", () => ({
  browserSupportsWebAuthn: () => true,
}));

// The real dialog drives the WebAuthn ceremony; the test only needs the
// callback PasskeySection hands it, so the stub records its props.
vi.mock("../PasskeyRegisterDialog", () => ({
  default: (props) => {
    mocks.registerDialogProps = props;
    return null;
  },
}));

const TOTP_BANNER = "Authenticator app configured.";
const PASSKEY_BANNER = "Passkey added.";
const REGENERATED_BANNER =
  "Recovery codes regenerated. Your old codes no longer work.";
const WRONG_BANNER = /Authenticator app configured/;

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("RecoveryCodesDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.registerDialogProps = null;
  });

  it.each([
    ["totp", TOTP_BANNER],
    ["passkey", PASSKEY_BANNER],
    ["regenerated", REGENERATED_BANNER],
  ])("names what just happened when reason is %s", (reason, banner) => {
    render(
      <RecoveryCodesDialog
        open
        onClose={vi.fn()}
        codes={mocks.codes}
        reason={reason}
      />,
    );

    expect(screen.getByText(banner)).toBeInTheDocument();
    if (reason !== "totp") {
      expect(screen.queryByText(WRONG_BANNER)).not.toBeInTheDocument();
    }
  });

  it("explains recovery codes without assuming an authenticator app", () => {
    render(
      <RecoveryCodesDialog
        open
        onClose={vi.fn()}
        codes={mocks.codes}
        reason="passkey"
      />,
    );

    expect(
      screen.getByText(
        /If you lose access to your authenticator app or passkey, you can sign in with one of these codes\. Each code works once\./,
      ),
    ).toBeInTheDocument();
  });

  it("says a passkey was added after the first passkey is registered", async () => {
    mocks.get.mockResolvedValue({ data: [] });

    renderWithQueryClient(
      <PasskeySection onStatusChange={mocks.onStatusChange} />,
    );

    expect(mocks.registerDialogProps).not.toBeNull();
    act(() => {
      mocks.registerDialogProps.onSuccess({
        id: "pk-1",
        name: "Laptop",
        recovery_codes: mocks.codes,
      });
    });

    expect(await screen.findByText(PASSKEY_BANNER)).toBeInTheDocument();
    expect(screen.getByText("aaaa-1111")).toBeInTheDocument();
    expect(screen.queryByText(WRONG_BANNER)).not.toBeInTheDocument();
  });

  it("says the codes were regenerated after Regenerate", async () => {
    mocks.post.mockResolvedValue({ data: { recovery_codes: mocks.codes } });

    renderWithQueryClient(
      <RecoveryCodesSection
        remaining={3}
        hasTotp={false}
        onStatusChange={mocks.onStatusChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    const confirmDialog = await screen.findByRole("dialog");
    fireEvent.change(within(confirmDialog).getByLabelText("Password"), {
      target: { value: "pw-fixture" },
    });
    fireEvent.click(
      within(confirmDialog).getByRole("button", { name: "Regenerate" }),
    );

    expect(await screen.findByText(REGENERATED_BANNER)).toBeInTheDocument();
    expect(screen.queryByText(WRONG_BANNER)).not.toBeInTheDocument();
  });
});
