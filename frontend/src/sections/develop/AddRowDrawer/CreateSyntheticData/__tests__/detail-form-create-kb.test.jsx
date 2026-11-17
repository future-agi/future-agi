// FR-DATASETS-003: step 1 of "Generate synthetic rows" puts the knowledge base
// picker next to Name. In a new workspace its menu holds only "Create
// knowledge base", which used to navigate inside the app and drop the name,
// description, objective, pattern and row count without a prompt. Creating a
// knowledge base now opens a new tab, the form keeps every value, and the
// knowledge base list is read again when the user comes back to this tab.
import React from "react";
import { FormProvider, useForm } from "react-hook-form";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import DetailForm from "../DetailForm";

const h = vi.hoisted(() => ({
  navigate: vi.fn(),
  refetch: vi.fn(),
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => h.navigate,
}));
// A new workspace: the list loaded and holds no knowledge base.
vi.mock("src/api/knowledge-base/files", () => ({
  useKnowledgeBaseList: () => ({
    data: [],
    isPending: false,
    refetch: h.refetch,
  }),
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: () => {},
}));

const theme = createTheme({ palette: palette("light") });

const typed = {
  name: "Refund questions",
  description: "Questions customers ask about refunds, with answers",
  useCase: "Check the support assistant on refund policy",
  pattern: "Short, polite replies",
  rowNumber: 25,
  kb_id: "",
};

const form = { current: null };

function DetailFormInForm() {
  const methods = useForm({ defaultValues: typed });
  form.current = methods;
  return (
    <FormProvider {...methods}>
      <DetailForm
        control={methods.control}
        handleNextTab={() => {}}
        setFocusField={() => {}}
        disabledNext={false}
      />
    </FormProvider>
  );
}

function renderDetailForm() {
  render(
    <ThemeProvider theme={theme}>
      <DetailFormInForm />
    </ThemeProvider>,
  );
}

async function clickCreateKnowledgeBase() {
  fireEvent.click(screen.getByPlaceholderText("Select knowledge base here"));
  fireEvent.click(await screen.findByText("Create knowledge base"));
}

let knowledgeBaseTab;
let openTab;

beforeEach(() => {
  h.navigate.mockClear();
  h.refetch.mockClear();
  form.current = null;
  knowledgeBaseTab = { opener: window };
  openTab = vi.spyOn(window, "open").mockImplementation(() => knowledgeBaseTab);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FR-DATASETS-003 create a knowledge base from the synthetic form", () => {
  it("opens knowledge base creation in a new tab and keeps every typed value", async () => {
    renderDetailForm();

    await clickCreateKnowledgeBase();

    expect(h.navigate).not.toHaveBeenCalled();
    expect(openTab).toHaveBeenCalledTimes(1);
    expect(openTab).toHaveBeenCalledWith("/dashboard/knowledge", "_blank");
    // The new tab cannot reach back into this one.
    expect(knowledgeBaseTab.opener).toBeNull();
    expect(form.current.getValues()).toEqual(typed);
    expect(screen.getByDisplayValue(typed.name)).toBeInTheDocument();
  });

  it("reads the knowledge base list again when the user comes back to this tab", async () => {
    renderDetailForm();
    await clickCreateKnowledgeBase();
    h.refetch.mockClear();

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });

    expect(h.refetch).toHaveBeenCalledTimes(1);
  });

  it("labels the knowledge base field optional", () => {
    renderDetailForm();

    expect(
      screen.getAllByText("Knowledge base (optional)").length,
    ).toBeGreaterThan(0);
    expect(screen.queryAllByText("Select knowledge base")).toHaveLength(0);
  });
});
