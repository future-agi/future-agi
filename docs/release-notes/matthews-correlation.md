# MatthewsCorrelation score correction

The Python `MatthewsCorrelation` evaluator now computes multiclass Matthews
correlation using Gorodkin's formula, including the actual predicted and expected
class counts. Previously, it used accuracy adjusted against a uniform class prior.

Scores remain normalized from raw MCC in `[-1, 1]` to `[0, 1]` using
`(MCC + 1) / 2`. For example, predicting only the majority class for expected labels
`["a", "a", "a", "a", "a", "a", "a", "a", "b", "c"]` now scores **0.5** rather
than **0.85**. A lower score on the same multiclass data reflects this calculation
correction, not a change in the model's predictions.

When either the predicted labels or the expected labels are constant, the
denominator is zero: raw MCC is defined as zero and the normalized score is
**0.5**. Both inputs do not need to be constant at the same time.

Binary scores, label parsing, invalid-input handling, and result field names are
unchanged. Previously stored results are not recalculated. Re-run evaluations
before comparing corrected scores to historical multiclass results. The separate
YAML system evaluator already uses the multiclass formula and is unchanged.
