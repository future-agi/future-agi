import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "src/utils/test-utils";
import CustomJsonViewer from "../CustomJsonViewer";

// Mock useDebounce to return value immediately for test determinism
vi.mock("src/hooks/use-debounce", () => ({
  useDebounce: (value) => value,
}));

const sampleObject = {
  model: "gpt-4",
  temperature: 0.7,
  metadata: {
    source: "api",
    tags: ["prod", "v2"],
  },
};

// JsonViewer renders data-testid="data-key-pair{keypath}" for each entry
const getKeyPair = (key) => screen.queryByTestId(`data-key-pair${key}`);

// Built at runtime so the no-script-url lint rule doesn't flag the fixture.
const SCRIPT_URL = ["javascript", "alert(1)//cat.png"].join(":");

describe("CustomJsonViewer", () => {
  describe("rendering", () => {
    it("renders json content without search field by default", () => {
      render(<CustomJsonViewer object={sampleObject} />);
      expect(screen.queryByPlaceholderText("Search")).not.toBeInTheDocument();
      expect(getKeyPair("model")).toBeInTheDocument();
    });

    it("renders search field when searchable is true", () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      expect(screen.getByPlaceholderText("Search")).toBeInTheDocument();
    });

    it("uses custom search placeholder", () => {
      render(
        <CustomJsonViewer
          object={sampleObject}
          searchable
          searchPlaceholder="Search attributes..."
        />,
      );
      expect(
        screen.getByPlaceholderText("Search attributes..."),
      ).toBeInTheDocument();
    });

    it("renders empty object when object is null", () => {
      const { container } = render(<CustomJsonViewer object={null} />);
      expect(container).toBeTruthy();
    });
  });

  describe("search filtering", () => {
    it("shows all data when search is empty", () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      expect(getKeyPair("model")).toBeInTheDocument();
      expect(getKeyPair("temperature")).toBeInTheDocument();
      expect(getKeyPair("metadata")).toBeInTheDocument();
    });

    it("filters by key name", async () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      const searchInput = screen.getByPlaceholderText("Search");

      fireEvent.change(searchInput, { target: { value: "model" } });

      await waitFor(() => {
        expect(getKeyPair("model")).toBeInTheDocument();
        expect(getKeyPair("temperature")).not.toBeInTheDocument();
      });
    });

    it("filters by nested key name", async () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      const searchInput = screen.getByPlaceholderText("Search");

      fireEvent.change(searchInput, { target: { value: "source" } });

      await waitFor(() => {
        // "metadata" parent should remain since it contains the match
        expect(getKeyPair("metadata")).toBeInTheDocument();
        expect(getKeyPair("metadata.source")).toBeInTheDocument();
        expect(getKeyPair("model")).not.toBeInTheDocument();
      });
    });

    it("filters by value", async () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      const searchInput = screen.getByPlaceholderText("Search");

      fireEvent.change(searchInput, { target: { value: "gpt-4" } });

      await waitFor(() => {
        expect(getKeyPair("model")).toBeInTheDocument();
        expect(getKeyPair("temperature")).not.toBeInTheDocument();
      });
    });

    it("shows empty result when no match found", async () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      const searchInput = screen.getByPlaceholderText("Search");

      fireEvent.change(searchInput, {
        target: { value: "nonexistentkeyxyz" },
      });

      await waitFor(() => {
        expect(getKeyPair("model")).not.toBeInTheDocument();
        expect(getKeyPair("temperature")).not.toBeInTheDocument();
        expect(getKeyPair("metadata")).not.toBeInTheDocument();
      });
    });

    it("search is case insensitive", async () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      const searchInput = screen.getByPlaceholderText("Search");

      fireEvent.change(searchInput, { target: { value: "MODEL" } });

      await waitFor(() => {
        expect(getKeyPair("model")).toBeInTheDocument();
      });
    });

    it("restores all data when search is cleared", async () => {
      render(<CustomJsonViewer object={sampleObject} searchable />);
      const searchInput = screen.getByPlaceholderText("Search");

      fireEvent.change(searchInput, { target: { value: "model" } });
      await waitFor(() => {
        expect(getKeyPair("temperature")).not.toBeInTheDocument();
      });

      fireEvent.change(searchInput, { target: { value: "" } });
      await waitFor(() => {
        expect(getKeyPair("model")).toBeInTheDocument();
        expect(getKeyPair("temperature")).toBeInTheDocument();
        expect(getKeyPair("metadata")).toBeInTheDocument();
      });
    });
  });

  describe("media previews", () => {
    it("shows an image URL as a thumbnail that opens the full-size image", () => {
      const src = "https://cdn.example.com/photos/cat.png?width=800";
      render(<CustomJsonViewer object={{ image_url: src }} />);

      fireEvent.click(
        screen.getByRole("button", { name: "Open image preview" }),
      );

      expect(
        screen.getByRole("img", { name: "Full size image" }),
      ).toHaveAttribute("src", src);
    });

    it("keeps base64 images previewable", () => {
      render(
        <CustomJsonViewer
          object={{ image: "data:image/png;base64,iVBORw0KGgo=" }}
        />,
      );

      expect(
        screen.getByRole("button", { name: "Open image preview" }),
      ).toBeInTheDocument();
    });

    it.each([
      ["an audio URL", "https://cdn.example.com/calls/call-42.mp3"],
      ["a base64 audio clip", "data:audio/wav;base64,UklGRg=="],
    ])("shows %s as a chip that opens an audio player", (_label, src) => {
      render(<CustomJsonViewer object={{ recording: src }} />);

      fireEvent.click(screen.getByRole("button", { name: "Play audio" }));

      expect(document.querySelector("audio")).toHaveAttribute("src", src);
    });

    it.each([
      ["a web page URL", "https://example.com/docs/page"],
      ["a relative image path", "images/cat.png"],
      ["a blob audio URL", "blob:https://example.com/call.mp3"],
      ["a data URI without a media type", "data:image;base64,AAAA"],
      ["a script URL", SCRIPT_URL],
    ])("keeps %s as plain text", (_label, value) => {
      render(<CustomJsonViewer object={{ field: value }} />);

      expect(getKeyPair("field")).toHaveTextContent(value);
      expect(
        screen.queryByRole("button", { name: "Open image preview" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Play audio" }),
      ).not.toBeInTheDocument();
    });
  });
});
