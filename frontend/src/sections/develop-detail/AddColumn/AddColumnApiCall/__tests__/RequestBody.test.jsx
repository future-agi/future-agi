/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it } from "vitest";
import { useForm } from "react-hook-form";
import { render } from "src/utils/test-utils";
import RequestBody from "../RequestBody";

const Harness = ({ sx }) => {
  const { control } = useForm({ defaultValues: { body: "" } });
  return (
    <RequestBody
      control={control}
      contentFieldName="body"
      allColumns={[]}
      sx={sx}
    />
  );
};

describe("RequestBody textarea background", () => {
  it("stays transparent even when a caller supplies a background via sx", () => {
    const { container } = render(
      <Harness sx={{ backgroundColor: "rgb(255, 0, 0)" }} />,
    );

    const textarea = container.querySelector("textarea");
    expect(textarea).toBeInTheDocument();

    // The backdrop layer provides the visible background and syntax
    // highlighting; the textarea must stay transparent so it shows through.
    // Pre-fix, `...sx` was applied after backgroundColor, so a caller-supplied
    // color covered the backdrop and hid the typed text (#1586).
    expect(textarea.style.backgroundColor).toBe("transparent");
  });
});
