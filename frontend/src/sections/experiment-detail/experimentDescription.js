// L7c g6 (exp:description): the description shown under the experiment
// title: the experiment record's own, else the copy every run carries
// (`experiment_description`), trimmed; empty when none was typed.
export const experimentDescription = (experiment) =>
  String(
    experiment?.description ??
      experiment?.runs?.find((r) => r?.experiment_description)
        ?.experiment_description ??
      "",
  ).trim();
