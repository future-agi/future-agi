import PropTypes from "prop-types";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import EnvironmentValues from "../panels/EnvironmentValues";

const renderWithQuery = (ui) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
};

// Owns secretFiles the way the embedding panels do, so the confirmation row and
// the remove control exercise the real functional-updater contract.
function Harness({ envText = "", onEnvText = vi.fn() }) {
  const [secretFiles, setSecretFiles] = useState([]);
  return (
    <EnvironmentValues
      envText={envText}
      onEnvText={onEnvText}
      egress=""
      onEgress={vi.fn()}
      secretFiles={secretFiles}
      onSecretFiles={setSecretFiles}
    />
  );
}
Harness.propTypes = { envText: PropTypes.string, onEnvText: PropTypes.func };

describe("EnvironmentValues", () => {
  it("is collapsed by default", () => {
    renderWithQuery(<Harness />);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("button", { name: /Environment values \(optional\)/ })).toBeInTheDocument();
  });

  it("keeps the credential-file upload and its confirmation visible while collapsed", async () => {
    const { container } = renderWithQuery(<Harness />);
    // Collapsed: no .env textarea yet…
    expect(screen.queryByPlaceholderText(/OPENAI_API_KEY/)).toBeNull();
    // …but the credential-file upload sits at the toggle's level, always visible.
    expect(screen.getByRole("button", { name: /Upload credential file/ })).toBeInTheDocument();

    // Uploading without expanding still confirms, and never opens the body.
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(['{"k":"v"}'], "creds.json", { type: "application/json" })] },
    });
    await waitFor(() =>
      expect(screen.getByText(/creds\.json uploaded/)).toBeInTheDocument(),
    );
    expect(screen.queryByPlaceholderText(/OPENAI_API_KEY/)).toBeNull();
  });

  it("reveals the textarea and egress field when expanded", () => {
    renderWithQuery(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /Environment values \(optional\)/ }));

    expect(screen.getByPlaceholderText(/OPENAI_API_KEY/)).toBeInTheDocument();
    expect(screen.getByText("Additional egress domains")).toBeInTheDocument();
  });

  it("reports typed env text", () => {
    const onEnvText = vi.fn();
    renderWithQuery(<Harness onEnvText={onEnvText} />);
    fireEvent.click(screen.getByRole("button", { name: /Environment values \(optional\)/ }));

    fireEvent.change(screen.getByPlaceholderText(/OPENAI_API_KEY/), { target: { value: "X=1" } });
    expect(onEnvText).toHaveBeenCalledWith("X=1");
  });

  it("lists an uploaded credential file as a secure reference without touching the .env textarea", async () => {
    const onEnvText = vi.fn();
    const { container } = renderWithQuery(<Harness onEnvText={onEnvText} />);
    fireEvent.click(screen.getByRole("button", { name: /Environment values \(optional\)/ }));

    const input = container.querySelector('input[type="file"]');
    fireEvent.change(input, {
      target: { files: [new File(['{"k":"v"}'], "creds.json", { type: "application/json" })] },
    });

    await waitFor(() =>
      expect(
        screen.getByText(/creds\.json uploaded · mounted per run, never written to the job/),
      ).toBeInTheDocument(),
    );
    // Contents never enter the textarea / draft.
    expect(onEnvText).not.toHaveBeenCalled();
    expect(screen.getByPlaceholderText(/OPENAI_API_KEY/).value).toBe("");
  });

  it("removes an uploaded credential reference", async () => {
    const { container } = renderWithQuery(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: /Environment values \(optional\)/ }));

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(["x"], "creds.json", { type: "application/json" })] },
    });
    const row = await screen.findByText(/creds\.json uploaded/);
    expect(row).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Close/ }));
    await waitFor(() => expect(screen.queryByText(/creds\.json uploaded/)).toBeNull());
  });
});
