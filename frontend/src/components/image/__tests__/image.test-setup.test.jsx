// The test setup mocks IntersectionObserver for lazy components. The lazy
// image reads IntersectionObserverEntry.prototype before it observes, so a
// screen showing a provider's bundled logo (a key card) renders in tests only
// when the setup provides that type as well.
import { describe, expect, it } from "vitest";
import { render } from "src/utils/test-utils";
import Image from "src/components/image";

describe("a lazy image under the test setup", () => {
  it("renders a provider's bundled logo", () => {
    expect(() =>
      render(<Image src="/icons/settings/openai.png" alt="OpenAI" />),
    ).not.toThrow();
  });
});
