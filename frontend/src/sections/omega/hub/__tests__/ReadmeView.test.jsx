import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ReadmeView, { isRemoteSrc } from "../components/ReadmeView";

describe("README rendering (SPEC §1.2: remote images render as links)", () => {
  it("turns a remote image into a link and loads nothing", () => {
    const { container } = render(<ReadmeView markdown={"# Title\n\n![tracker](https://tracker.example/p.png)"} />);
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByTestId("hub-readme-image-link");
    expect(link).toHaveAttribute("href", "https://tracker.example/p.png");
    expect(link).toHaveAttribute("rel", "nofollow noopener noreferrer");
    expect(link).toHaveTextContent("Image: tracker");
  });

  it("treats protocol-relative and data: sources as remote too", () => {
    expect(isRemoteSrc("//cdn.example/x.png")).toBe(true);
    expect(isRemoteSrc("data:image/png;base64,AAAA")).toBe(true);
    expect(isRemoteSrc("docs/shot.png")).toBe(false);
    const { container } = render(<ReadmeView markdown={"![x](//cdn.example/x.png)"} resolveImage={() => "/local.png"} />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows a bundle image only through the registry's own file URL", () => {
    const { container } = render(
      <ReadmeView markdown={"![shot](docs/shot.png)"} resolveImage={(src) => `/omega-hub/v1/entries/a/b/versions/1.0.0/files/${src}`} />,
    );
    expect(container.querySelector("img")).toHaveAttribute("src", "/omega-hub/v1/entries/a/b/versions/1.0.0/files/docs/shot.png");
  });

  it("never renders raw HTML from the README", () => {
    const { container } = render(<ReadmeView markdown={'<img src="https://x.example/a.png"><script>window.bad=1</script>\n\ntext'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });
});
