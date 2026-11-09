import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderPage } from "../../__tests__/helpers";
import { catalogEntry, needsSetup } from "../../catalog";
import {
  actionOf,
  draftCheck,
  mapEditForSave,
  normalizeGuardrails,
  ruleBodyForSave,
  ruleRows,
} from "../../catalog/rule-model";
import { formIsComplete, formValuesFor } from "../../catalog/form-model";
import RuleDrawer from "../RuleDrawer";

const cases = [
  {
    name: "bedrock-guardrails",
    field: "region",
    label: "Region",
    value: "us-east-2",
    config: {
      provider: "bedrock_guardrails",
      guardrail_id: "fixture-only",
      guardrail_version: "DRAFT",
      region: "us-east-2",
      endpoint: "http://localhost:5011",
      access_key: "__encrypted__",
      secret_key: "__encrypted__",
    },
  },
  {
    name: "presidio-pii",
    field: "language",
    label: "Language",
    value: "ko",
    config: {
      provider: "presidio",
      endpoint: "http://localhost:5011",
      language: "ko",
      entities: ["PERSON"],
      score_threshold: 0.5,
    },
  },
];

const checkFor = (config) => ({
  config,
  enabled: true,
  action: "block",
  confidence_threshold: 0.8,
});

describe("stored provider values outside catalogue suggestions", () => {
  it.each(cases)(
    "keeps $name complete, active and unchanged across save paths",
    ({ name, field, value, config }) => {
      const meta = catalogEntry(name);
      const check = checkFor(config);
      const form = formValuesFor(meta, check, actionOf);
      expect(form.fields[field]).toBe(value);
      expect(formIsComplete(meta, form)).toBe(true);
      expect(needsSetup(meta, check)).toBe(false);
      const rows = ruleRows({ checks: { [name]: check } });
      expect(rows.filter((row) => row.enabled && !row.needsSetup)).toHaveLength(
        1,
      );
      expect(draftCheck(meta, form, check).config).toEqual(config);
      expect(
        mapEditForSave({ check, meta, catalogName: name, form }),
      ).toBeNull();
      const edited = { ...form, action: "warn" };
      expect(
        mapEditForSave({ check, meta, catalogName: name, form: edited }),
      ).toEqual({ name, config: { action: "warn" } });
      const rule = {
        name,
        mode: "sync",
        enabled: true,
        action: "block",
        threshold: 0.8,
        config,
      };
      const saved = { rules: [rule] };
      const normalized = normalizeGuardrails(saved).checks[name];
      expect(
        ruleBodyForSave({
          saved,
          check: normalized,
          meta,
          catalogName: name,
          form,
        }).config,
      ).toEqual(rule);
    },
  );

  it.each(cases)(
    "displays $name's stored value and preserves it on Save and reopen",
    async ({ name, field, label, value, config }) => {
      const meta = catalogEntry(name);
      const [row] = ruleRows({ checks: { [name]: checkFor(config) } });
      const onSave = vi.fn().mockResolvedValue({});
      const onClose = vi.fn();
      const view = renderPage(
        <RuleDrawer
          open
          meta={meta}
          row={row}
          onClose={onClose}
          onSave={onSave}
        />,
      );
      const drawer = await screen.findByRole("dialog", { name: meta.label });
      const save = within(drawer).getByRole("button", { name: "Save" });
      expect(save).toBeEnabled();
      expect(within(drawer).queryByText(/Invalid stored setting/)).toBeNull();
      const select = within(drawer).getByRole("combobox", { name: label });
      expect(select).toHaveTextContent(value);
      fireEvent.mouseDown(select);
      fireEvent.click(
        await screen.findByRole("option", { name: value, exact: true }),
      );
      fireEvent.click(save);
      await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
      expect(onSave).toHaveBeenCalledOnce();
      const form = onSave.mock.calls[0][0];
      expect(form.fields[field]).toBe(value);
      const stored = draftCheck(meta, form, row.check);
      expect(stored.config).toEqual(config);
      view.unmount();
      const [reopened] = ruleRows({ checks: { [name]: stored } });
      renderPage(
        <RuleDrawer
          open
          meta={meta}
          row={reopened}
          onClose={() => {}}
          onSave={vi.fn()}
        />,
      );
      const reopenedDrawer = await screen.findByRole("dialog", {
        name: meta.label,
      });
      expect(
        within(reopenedDrawer).getByRole("combobox", { name: label }),
      ).toHaveTextContent(value);
      expect(
        within(reopenedDrawer).getByRole("button", { name: "Save" }),
      ).toBeEnabled();
    },
  );
});
