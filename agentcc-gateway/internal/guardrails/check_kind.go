package guardrails

import "fmt"

// What an output check judges, as a check's config sets it ("judges"): what
// the text says (it reads a model's refusal as content) or the answer's shape
// (it does not read the refusal). Checks whose kind is set outside the
// gateway (a Future AGI evaluation, a webhook, an external evaluator) take
// it from this setting; with none, a check of unknown kind reads the answer
// as production did (UnknownKindCheck).
const (
	JudgesSays  = "says"
	JudgesShape = "shape"
)

// How a check's kind was decided (KindSourcer): set by the check's config,
// derived from what it runs (a Future AGI evaluation's system eval ids),
// not known, or refused (an invalid "judges": the check fails closed).
const (
	KindSet     = "set"
	KindDerived = "derived"
	KindNone    = "none"
	KindInvalid = "invalid"
)

// ParseJudges reads a check config's "judges": JudgesSays, JudgesShape, or
// "" when it is absent or null. Any other value is an error, which the check
// reports as its config error: it fails closed.
func ParseJudges(cfg map[string]interface{}) (string, error) {
	v, ok := cfg["judges"]
	if !ok || v == nil {
		return "", nil
	}
	if s, ok := v.(string); ok && (s == JudgesSays || s == JudgesShape) {
		return s, nil
	}
	return "", fmt.Errorf(`"judges" must be %q or %q`, JudgesSays, JudgesShape)
}

// KindSourcer is implemented by a check whose kind comes from its config:
// KindSource says how it was decided (KindSet, KindDerived, KindNone or
// KindInvalid).
type KindSourcer interface {
	KindSource() string
}

// CheckKind is the kind check g runs with on the answer: "shape", "unknown"
// or "says" (answerInputFor), and how it was decided: the check's
// KindSource, or "built-in" for a check whose type fixes it.
func CheckKind(g Guardrail) (kind, source string) {
	switch {
	case IsShapeCheck(g):
		kind = JudgesShape
	case IsUnknownKindCheck(g):
		kind = "unknown"
	default:
		kind = JudgesSays
	}
	source = "built-in"
	if s, ok := g.(KindSourcer); ok {
		source = s.KindSource()
		if source == "" {
			source = KindNone
		}
	}
	return kind, source
}
