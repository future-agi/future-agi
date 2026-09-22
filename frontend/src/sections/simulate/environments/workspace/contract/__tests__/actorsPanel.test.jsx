import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ActorsPanel from "../ActorsPanel";
import RlContractPanel from "../RlContractPanel";

// A voice environment. castFor(voice) seeds the first three voice-capable
// actors: the competing colleague, the supervisor and the fraud desk.
const voiceEnv = {
  name: "Refund Copilot",
  surface: "voice",
  tools: [{ name: "lookup_account", desc: "Reads the caller's account." }],
  rules: ["Only refund verified callers."],
};

describe("ActorsPanel", () => {
  it("renders the seeded cast for a voice environment", () => {
    render(<ActorsPanel env={voiceEnv} envState={{}} onGo={vi.fn()} />);
    expect(screen.getByText("Colleague with a different plan")).toBeInTheDocument();
    expect(screen.getByText("Supervisor")).toBeInTheDocument();
    expect(screen.getByText("Fraud desk")).toBeInTheDocument();
    // Three actors in this environment.
    expect(screen.getByText(/In this environment \(3\)/)).toBeInTheDocument();
  });

  it("shows each actor's role — the pressure kind it applies", () => {
    render(<ActorsPanel env={voiceEnv} envState={{}} onGo={vi.fn()} />);
    expect(screen.getByText("Competing goal")).toBeInTheDocument();
    expect(screen.getByText("Authority")).toBeInTheDocument();
    expect(screen.getByText("Gatekeeper")).toBeInTheDocument();
  });

  it("shows each actor's modalities on the row", () => {
    render(<ActorsPanel env={voiceEnv} envState={{}} onGo={vi.fn()} />);
    // All three seeded actors are voice + chat capable.
    expect(screen.getAllByText("voice")).toHaveLength(3);
    expect(screen.getAllByText("chat")).toHaveLength(3);
  });

  it("reveals the per-actor detail when a row is expanded", () => {
    render(<ActorsPanel env={voiceEnv} envState={{}} onGo={vi.fn()} />);
    // The row leads with the goal; the blurb lives in the unmountOnExit Collapse
    // and is not in the DOM until the row is opened.
    expect(screen.queryByText(/openly arguing for a different outcome/)).toBeNull();
    fireEvent.click(screen.getByText("Colleague with a different plan"));
    expect(screen.getByText(/openly arguing for a different outcome/)).toBeInTheDocument();
  });

  it("honours an explicit cast from envState.actors (id strings)", () => {
    render(
      <ActorsPanel env={voiceEnv} envState={{ actors: ["act-supervisor"] }} onGo={vi.fn()} />,
    );
    expect(screen.getByText("Supervisor")).toBeInTheDocument();
    expect(screen.queryByText("Fraud desk")).toBeNull();
    expect(screen.getByText(/In this environment \(1\)/)).toBeInTheDocument();
  });

  it("shows an empty state when the environment has no actors", () => {
    render(<ActorsPanel env={voiceEnv} envState={{ actors: [] }} onGo={vi.fn()} />);
    expect(screen.getByText("No actors yet")).toBeInTheDocument();
  });

  it("routes the persona cross-link to the scenarios tab", () => {
    const onGo = vi.fn();
    render(<ActorsPanel env={voiceEnv} envState={{}} onGo={onGo} />);
    fireEvent.click(screen.getByText(/See scenarios/i));
    expect(onGo).toHaveBeenCalledWith("scenarios");
  });
});

describe("RlContractPanel actors slot", () => {
  it("no longer mounts the dummy Actors section (commented out on the panel)", () => {
    render(<RlContractPanel env={voiceEnv} envState={{ evals: [] }} patch={vi.fn()} />);
    expect(screen.queryByText("Colleague with a different plan")).toBeNull();
  });
});

describe("ActorsPanel create / edit / remove flow", () => {
  it("hides create/edit/remove when no patch is provided (read-only)", () => {
    render(<ActorsPanel env={voiceEnv} envState={{}} onGo={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /create actor/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /remove from this environment/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit — saving creates a new version/i })).toBeNull();
  });

  it("shows the Create actor button and opens the editor drawer when patch is provided", () => {
    render(<ActorsPanel env={voiceEnv} envState={{}} patch={vi.fn()} onGo={vi.fn()} />);
    const create = screen.getByRole("button", { name: /create actor/i });
    expect(create).toBeEnabled();
    fireEvent.click(create);
    // The editor drawer opens with its fields.
    expect(screen.getByRole("textbox", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByText(/must not be the task's goal/i)).toBeInTheDocument();
  });

  it("removes an actor from the environment via patch", () => {
    const patch = vi.fn();
    render(<ActorsPanel env={voiceEnv} envState={{ actors: ["act-competing-colleague", "act-supervisor"] }} patch={patch} onGo={vi.fn()} />);
    fireEvent.click(screen.getAllByRole("button", { name: /remove from this environment/i })[0]);
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0][0].actors).not.toContain("act-competing-colleague");
    expect(patch.mock.calls[0][0].actors).toContain("act-supervisor");
  });

  it("disables Create and drops row edit/remove when locked", () => {
    render(<ActorsPanel env={voiceEnv} envState={{ actors: ["act-competing-colleague"] }} patch={vi.fn()} onGo={vi.fn()} locked />);
    expect(screen.getByRole("button", { name: /create actor/i })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /remove from this environment/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit — saving creates a new version/i })).toBeNull();
  });
});
