import { useMemo } from "react";
import { useOptionalSimStore } from "../store";
import { monthUsage } from "../_mock/usageLedger";
import { allowanceOf } from "../_mock/pricing";

/*
  The workspace's credit balance this month — the admin's limit and what the
  usage ledger says was used. Every paid action (a run, a build, generated
  scenarios, an improvement) checks this one balance.
*/
export default function useCreditBalance() {
  const store = useOptionalSimStore();
  const state = store?.state;
  return useMemo(() => {
    const m = monthUsage(state);
    return {
      /* Runs still playing hold their limits, as in the run plan. */
      ...allowanceOf(m.total + m.held.total),
      byProject: m.byProject,
      byUser: m.byUser,
    };
  }, [state]);
}
