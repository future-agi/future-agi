import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import BackgroundNoiseLabel from "../BackgroundNoiseLabel";
import { backgroundNoiseFrom } from "../backgroundNoise";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

describe("backgroundNoiseFrom", () => {
  it("reads the key and readable label off an API payload", () => {
    expect(
      backgroundNoiseFrom({
        background_noise: "vehicle",
        background_noise_label: "In a car",
      }),
    ).toEqual({ key: "vehicle", label: "In a car" });
  });

  it("falls back to the key when the API sends no label", () => {
    expect(backgroundNoiseFrom({ background_noise: "street" })).toEqual({
      key: "street",
      label: "street",
    });
  });

  it.each([[{}], [{ background_noise: "" }], [null]])(
    "is null when the payload has no noise (%j)",
    (payload) => {
      expect(backgroundNoiseFrom(payload)).toBeNull();
    },
  );
});

describe("BackgroundNoiseLabel", () => {
  it("shows a noisy call's place with the sound icon", () => {
    render(
      <BackgroundNoiseLabel noise={{ key: "vehicle", label: "In a car" }} />,
    );

    expect(screen.getByText("In a car")).toBeInTheDocument();
    expect(screen.getByTestId("iconify")).toHaveAttribute(
      "data-icon",
      "solar:soundwave-linear",
    );
  });

  it("shows a quiet line with the muted icon", () => {
    render(
      <BackgroundNoiseLabel
        noise={{ key: "quiet line", label: "Quiet line" }}
      />,
    );

    expect(screen.getByText("Quiet line")).toBeInTheDocument();
    expect(screen.getByTestId("iconify")).toHaveAttribute(
      "data-icon",
      "solar:volume-cross-linear",
    );
  });

  it("shows a dash when the call's noise is unknown", () => {
    render(<BackgroundNoiseLabel noise={null} />);

    expect(screen.getByText("-")).toBeInTheDocument();
    expect(screen.queryByTestId("iconify")).not.toBeInTheDocument();
  });
});
