import React from "react";
import { Box } from "@mui/material";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";
import { NewTaskValidationSchema } from "../../schema";
import TaskProjectField from "../TaskProjectField";
import { productionTheme } from "./samplingContrastTestUtils";

const submitted = vi.fn();
function ProjectForm() {
  const { control, handleSubmit } = useForm({
    resolver: zodResolver(NewTaskValidationSchema()),
    defaultValues: {
      name: "Required project regression",
      project: "",
      spansLimit: 100,
      samplingRate: 50,
      evalsDetails: [{ id: "eval-1" }],
      startDate: "2026-10-01",
      endDate: "2026-10-02",
      runType: "historical",
      rowType: "spans",
      filters: [],
    },
  });
  return (
    <form noValidate onSubmit={handleSubmit(submitted)}>
      <TaskProjectField
        control={control}
        options={[{ value: "p1", label: "Alpha" }]}
        onChange={() => {}}
      />
      <button type="submit">Create Task</button>
    </form>
  );
}

describe.each(["light", "dark"])(
  "required Project in production %s theme",
  (mode) => {
    it.each(["default", "paper", "neutral"])(
      "keeps required validation associated and keyboard-operable on %s",
      async (surface) => {
        submitted.mockClear();
        const user = userEvent.setup();
        render(
          <Box sx={{ bgcolor: `background.${surface}` }}>
            <ProjectForm />
          </Box>,
          { theme: productionTheme(mode) },
        );
        const input = screen.getByRole("combobox", { name: /Project/ });
        const id = input.id;
        expect(id).not.toBe("");
        expect(input).toBeRequired();
        await user.click(screen.getByRole("button", { name: "Create Task" }));
        const helper = await screen.findByText("Project is required");
        const label = input
          .closest(".MuiFormControl-root")
          .querySelector("label");
        expect(submitted).not.toHaveBeenCalled();
        expect(input).toHaveAttribute("aria-invalid", "true");
        expect(input).toHaveAttribute("aria-describedby", helper.id);
        expect(helper).toHaveAttribute("id", `${id}-helper-text`);
        expect(label).toHaveAttribute("id", `${id}-label`);
        expect(label).toHaveAttribute("for", id);
        expect(label).toHaveAttribute("data-shrink", "true");
        expect(label).toHaveClass("Mui-error");
        await user.click(input);
        await user.click(await screen.findByRole("option", { name: "Alpha" }));
        await waitFor(() =>
          expect(input).toHaveAttribute("aria-invalid", "false"),
        );
        expect(
          screen.queryByText("Project is required"),
        ).not.toBeInTheDocument();
        expect(input).toHaveValue("Alpha");
        expect(input).toHaveAttribute("id", id);
        expect(label).toHaveAttribute("data-shrink", "true");
      },
    );
  },
);
