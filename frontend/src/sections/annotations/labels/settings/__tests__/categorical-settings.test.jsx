import { describe, it, expect } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { useForm, FormProvider } from "react-hook-form";
import PropTypes from "prop-types";
import CategoricalSettings from "../categorical-settings";

// Wrapper that provides react-hook-form control
function Wrapper({ defaultOptions = [{ label: "A" }, { label: "B" }] }) {
  const methods = useForm({
    defaultValues: {
      settings: { options: defaultOptions, multi_choice: false },
    },
  });
  return (
    <FormProvider {...methods}>
      <CategoricalSettings control={methods.control} />
    </FormProvider>
  );
}

Wrapper.propTypes = {
  defaultOptions: PropTypes.arrayOf(PropTypes.object),
};

describe("CategoricalSettings", () => {
  describe("delete option behavior", () => {
    it("shows delete buttons when there are 2 options", () => {
      render(<Wrapper defaultOptions={[{ label: "A" }, { label: "B" }]} />);

      const inputs = screen.getAllByPlaceholderText(/Option/);
      expect(inputs).toHaveLength(2);

      // Delete buttons should be visible for each option
      const deleteButtons = screen.getAllByRole("button", { name: "" });
      expect(deleteButtons.length).toBeGreaterThanOrEqual(2);
    });

    it("allows deleting an option when there are 2 options", async () => {
      const user = userEvent.setup();
      render(<Wrapper defaultOptions={[{ label: "A" }, { label: "B" }]} />);

      const inputs = screen.getAllByPlaceholderText(/Option/);
      expect(inputs).toHaveLength(2);

      // Find and click the first delete button (IconButton in the option row)
      const firstRow = inputs[0].closest(".MuiStack-root");
      const deleteBtn = firstRow.querySelector("button");
      await user.click(deleteBtn);

      // Should now have 1 option
      const remaining = screen.getAllByPlaceholderText(/Option/);
      expect(remaining).toHaveLength(1);
    });

    it("hides delete button when only 1 option remains", async () => {
      render(<Wrapper defaultOptions={[{ label: "A" }]} />);

      const inputs = screen.getAllByPlaceholderText(/Option/);
      expect(inputs).toHaveLength(1);

      // The delete button should not be present in the option row
      const row = inputs[0].closest(".MuiStack-root");
      const deleteBtn = row.querySelector("button");
      expect(deleteBtn).toBeNull();
    });

    it("can add a new option via the Add button", async () => {
      const user = userEvent.setup();
      render(<Wrapper defaultOptions={[{ label: "A" }]} />);

      expect(screen.getAllByPlaceholderText(/Option/)).toHaveLength(1);

      await user.click(screen.getByRole("button", { name: /add option/i }));

      expect(screen.getAllByPlaceholderText(/Option/)).toHaveLength(2);
    });
  });
});
