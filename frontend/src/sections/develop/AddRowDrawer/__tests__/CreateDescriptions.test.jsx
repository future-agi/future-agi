import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CreateDescriptions from "../CreateDescriptions";

const mockRequestBody = vi.fn();

vi.mock("react-hook-form", async () => {
  const actual = await vi.importActual("react-hook-form");
  return {
    ...actual,
    useWatch: vi.fn(() => [{ name: "Question" }]),
  };
});

vi.mock(
  "src/sections/develop-detail/AddColumn/AddColumnApiCall/RequestBody",
  () => ({
    default: (props) => {
      mockRequestBody(props);
      return <div data-testid="request-body" />;
    },
  }),
);

vi.mock("src/components/iconify", () => ({
  default: () => <span data-testid="iconify" />,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => <div>{children}</div>,
}));

describe("CreateDescriptions", () => {
  it("does not pass an opaque background color to the shared RequestBody textarea", () => {
    render(
      <CreateDescriptions
        control={{}}
        fields={[
          {
            data_type: "text",
            property: [],
          },
        ]}
      />,
    );

    expect(screen.getByText("Column 1:")).toBeInTheDocument();
    expect(mockRequestBody).toHaveBeenCalledTimes(1);
    expect(mockRequestBody.mock.calls[0][0].sx).toEqual({
      borderRadius: "4px",
    });
  });
});
