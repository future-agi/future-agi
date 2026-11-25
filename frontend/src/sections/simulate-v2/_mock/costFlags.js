/*
  Cost before a run, and run caps, are Later in the plan (T34, D15,
  controls.md). Built, but off: no estimate or cap shows before a run starts,
  and no cap is applied. The run's Cost tab (what it did cost) stays.
*/
export const SHOW_RUN_COST = false;

/*
  Cost before an Epoch starts (Kartik, 25 Nov): an estimate and a warning
  when the budget is below it. Built in the prototype and on, but Later for
  engineering — not picked up now. Runs keep SHOW_RUN_COST.
*/
export const SHOW_EPOCH_COST = true;
