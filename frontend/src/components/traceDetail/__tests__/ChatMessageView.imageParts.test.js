import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ChatMessageView from "../ChatMessageView";

vi.mock("src/components/iconify", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

const encodings = [
  { label: "decoded", encode: (value) => value },
  { label: "JSON-encoded", encode: JSON.stringify },
];
const question = "Where is order A-1001? Here is my receipt photo.";
const receiptUrl = "http://localhost:3100/receipt-photo.svg";
const pngData = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB";
const imagePart = (url) => ({ type: "image_url", image_url: { url } });
const request = (content) => ({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content }],
});
const view = (props) => render(createElement(ChatMessageView, props));

// Real Markdown, message cards and theme providers; only icons are stubbed.
describe("ChatMessageView image parts", () => {
  it.each(encodings)(
    "OBS-SW-126 shows a $label OpenAI image_url part as an image, not JSON",
    ({ encode }) => {
      const { container } = view({
        input: encode(
          request([{ type: "text", text: question }, imagePart(receiptUrl)]),
        ),
      });

      expect(screen.getByText(question)).toBeInTheDocument();
      expect(
        screen.getByRole("img", { name: "Image in this message" }),
      ).toHaveAttribute("src", receiptUrl);
      expect(container.textContent).not.toContain("image_url");
      expect(container.textContent).not.toContain(receiptUrl);
    },
  );

  it("OBS-SW-126 shows an image-only message without the (empty) note", () => {
    const { container } = view({ input: request([imagePart(pngData)]) });

    expect(
      screen.getByRole("img", { name: "Image in this message" }),
    ).toHaveAttribute("src", pngData);
    expect(container.textContent).not.toContain("(empty)");
    expect(container.textContent).not.toContain("image_url");
  });

  it("keeps an image part with an unsupported address as text", () => {
    const ftpUrl = "ftp://files.example.com/receipt.png";
    const { container } = view({ input: request([imagePart(ftpUrl)]) });

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(container.textContent).toContain(ftpUrl);
  });
});
