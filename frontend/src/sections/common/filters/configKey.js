// The comparable "same setup" key of a run (moved from api/develop/r2.js,
// which re-exports it unchanged, so By dataset, the Matrix and the runs
// catalogue's `config_key` read one rule). Pure.
import { runKindOf } from "src/sections/common/Runs/runKind";

/**
 * The comparable "same setup" key of a run, for By dataset: the run's
 * `config_key` when the API sends one, else kind + target + model.
 */
export function configKeyOf(run) {
  if (!run) return null;
  if (run.config_key) return run.config_key;
  const c = run.config ?? {};
  const kind = runKindOf(run);
  const target =
    c.prompt?.id ??
    c.agent?.graph_id ??
    c.harness?.name ??
    c.code?.repo ??
    c.traces?.project_id ??
    run.target?.prompt_id ??
    run.target?.graph_id ??
    run.name ??
    "";
  const model = c.model?.id ?? c.harness?.model ?? run.target?.model ?? "";
  return [kind, target, model].join("|");
}
