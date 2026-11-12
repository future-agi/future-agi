package futureagi

import "strings"

// shapeSystemEvalNames are the system evals (futureagi/model_hub/system_evals)
// that judge the answer's shape: its format (JSON, XML, HTML, SQL, a URL, an
// email, one line, a pattern, code) or its length. A refusal is not an answer
// in that shape, so a rule that runs only these does not read it. Every other
// system eval judges what the text says. systemEvalKinds is generated from
// this list and the system evals' YAML; TestSystemEvalKindsMatchTheSeed
// fails when they drift apart.
var shapeSystemEvalNames = map[string]bool{
	"contains_code":          true,
	"is_email":               true,
	"is_html":                true,
	"is_json":                true,
	"is_sql":                 true,
	"is_url":                 true,
	"is_xml":                 true,
	"json_diff":              true,
	"json_scheme_validation": true,
	"length_between":         true,
	"length_greater_than":    true,
	"length_less_than":       true,
	"one_line":               true,
	"regex":                  true,
	"sentence_count":         true,
	"syntax_validation":      true,
	"word_count_in_range":    true,
}

// derivedJudges is the kind a rule's eval ids give it: guardrails.JudgesShape
// when every id is a system eval that judges shape, JudgesSays when every id
// is a system eval that judges what the text says, and "" (unknown) when
// there is none, an id is not a system eval's (a custom eval, a typo), or
// the ids mix the two kinds.
func derivedJudges(evalIDs []string) string {
	kind := ""
	for _, id := range evalIDs {
		k, ok := systemEvalKinds[strings.TrimSpace(id)]
		if !ok || (kind != "" && k.kind != kind) {
			return ""
		}
		kind = k.kind
	}
	return kind
}

// systemEval is a system eval's name and kind, by its eval_id.
type systemEval struct {
	name string
	kind string
}
