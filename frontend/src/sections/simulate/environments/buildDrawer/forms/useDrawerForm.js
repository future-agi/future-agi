import { useState } from "react";

/**
 * A setup form's fields. `set(field)(value)` updates one and drops any
 * preflight result, so a pass for one source never builds another.
 */
export default function useDrawerForm(initial, build) {
  const [form, setForm] = useState(initial);
  const set = (field) => (value) => {
    setForm((s) => ({
      ...s,
      [field]: typeof value === "function" ? value(s[field]) : value,
    }));
    build.resetPreflight();
  };
  return [form, set, setForm];
}
