import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  formatNumber,
  numberProblem,
  ruleText,
} from "src/sections/annotations/queues/utils/label-rules";

const shown = (value) =>
  value === null || value === undefined || value === ""
    ? ""
    : formatNumber(value);

/**
 * A typed number is a draft until it passes the label's rule (KH-SCOPE §6
 * B1, mount M-B3). The field shows what was typed; only a number that fits
 * the rule reaches `onChange`, so the score keeps its previous value while
 * the reason shows inline (`Helpfulness must be between 0 and 1 (you entered
 * 1.4).`). Leaving the field puts the kept value back and leaves the reason
 * up; nothing is ever clamped silently.
 *
 * The refusal stays pending until the reviewer enters a number the rule
 * accepts or the score changes elsewhere (the slider, another item). The
 * form reads it through `entryRef` (`{refusal() → problem|null}`, see
 * useEntryRefusals) and refuses to submit while it is pending, so the kept
 * score is never saved in place of a refused entry without a word. A
 * half-typed number ("-", "1e": the field reads "" with badInput) is pending
 * too; leaving the field shows its reason.
 *
 * `kept`: the value is still the answer the item was opened with. If the
 * label's limits refuse it (they changed since it was saved), the line under
 * the field says why in place of the rule, quietly, and leaving the field
 * does not turn that into a refusal.
 *
 * @returns {{text: string, error: string|null, hint: string|null,
 *   onChange: Function, onBlur: Function}}
 */
export function useNumberDraft({
  name,
  settings,
  value,
  onChange,
  entryRef,
  kept = false,
}) {
  const label = { name, type: "numeric", settings: settings ?? {} };
  const keptProblem =
    kept && value !== null && value !== undefined
      ? numberProblem(label, value)
      : null;
  const [text, setText] = useState(() => shown(value));
  const [refusal, setRefusal] = useState(null);
  // Mirrors of the pending refusal, read at submit time (⌘↵ may come before
  // React renders the last keystroke).
  const refusalRef = useRef(null);
  const partialRef = useRef(false);
  const typedRef = useRef(false);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const valueRef = useRef(value);
  valueRef.current = value;
  const labelRef = useRef(label);
  labelRef.current = label;

  const refuse = useCallback((problem) => {
    refusalRef.current = problem;
    setRefusal(problem);
  }, []);

  useImperativeHandle(
    entryRef,
    () => ({
      refusal: () =>
        refusalRef.current ??
        (partialRef.current ? numberProblem(labelRef.current, "") : null),
    }),
    [],
  );

  // A value set elsewhere (the slider, a button, another item) replaces
  // the field and its reason.
  useEffect(() => {
    if (typedRef.current) {
      typedRef.current = false;
      return;
    }
    partialRef.current = false;
    setText(shown(value));
    refuse(null);
  }, [value, refuse]);

  const handleChange = useCallback(
    (event) => {
      const raw = event.target.value;
      if (raw === "") {
        setText("");
        // "-" or "0." on the way to a number reads "" with badInput: keep the
        // score until the number is complete (pending until then).
        if (event.target.validity?.badInput) {
          partialRef.current = true;
          return;
        }
        partialRef.current = false;
        refuse(null);
        typedRef.current = valueRef.current != null;
        onChangeRef.current(null);
        return;
      }
      const n = Number(raw);
      if (Number.isNaN(n)) return;
      partialRef.current = false;
      // "05" reads 5 (as the input always did).
      setText(/^-?0\d/.test(raw) ? String(n) : raw);
      const bad = numberProblem(labelRef.current, n);
      if (bad) {
        refuse(bad);
        return;
      }
      refuse(null);
      // Our own value coming back must not rewrite what was typed ("0.10").
      typedRef.current = n !== valueRef.current;
      onChangeRef.current(n);
    },
    [refuse],
  );

  const handleBlur = useCallback(() => {
    // A half-typed number: say what the field needs.
    if (partialRef.current) {
      partialRef.current = false;
      refuse(numberProblem(labelRef.current, ""));
      setText(shown(value));
      return;
    }
    // A refused entry: the field shows the kept value again, the reason stays.
    if (refusal) {
      setText(shown(value));
      return;
    }
    // A stored value the rule refuses (the label's range changed since):
    // say so instead of changing it. The answer the item was opened with
    // already says so quietly and is never refused.
    if (value !== null && value !== undefined && !kept) {
      const bad = numberProblem(labelRef.current, value);
      if (bad) refuse(bad);
    }
  }, [refusal, value, refuse, kept]);

  return {
    text,
    error: refusal?.text ?? null,
    hint: keptProblem?.text ?? ruleText(label),
    onChange: handleChange,
    onBlur: handleBlur,
  };
}

export default useNumberDraft;
