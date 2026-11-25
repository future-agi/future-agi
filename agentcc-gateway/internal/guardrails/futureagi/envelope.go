package futureagi

import (
	"fmt"
	"strings"
)

// validateEvaluations binds every returned verdict to the requested evaluation.
// A single legacy result may omit evalId only for a single requested evaluation.
func validateEvaluations(resp *evalResponse, ids []string) error {
	if resp == nil {
		return fmt.Errorf("missing evaluation response")
	}
	wanted := make(map[string]bool, len(ids))
	for _, id := range ids {
		if wanted[id] {
			return fmt.Errorf("duplicate requested evaluation identity")
		}
		wanted[id] = true
	}
	count := 0
	for _, group := range resp.Result {
		count += len(group.Evaluations)
	}
	if count != len(wanted) || count == 0 {
		return fmt.Errorf("incomplete evaluation results")
	}
	seen := make(map[string]bool, count)
	for i := range resp.Result {
		for j := range resp.Result[i].Evaluations {
			ev := &resp.Result[i].Evaluations[j]
			id := ev.EvalID
			if id == "" && len(ids) == 1 && count == 1 {
				id = ids[0]
			}
			if !wanted[id] || seen[id] {
				return fmt.Errorf("missing, unexpected or duplicate evaluation identity")
			}
			seen[id] = true
			switch v := ev.Output.(type) {
			case bool:
			case string:
				if !strings.EqualFold(v, "passed") && !strings.EqualFold(v, "failed") {
					return fmt.Errorf("invalid evaluation output")
				}
			default:
				return fmt.Errorf("missing or invalid evaluation output")
			}
			ev.EvalID = id
		}
	}
	return nil
}
