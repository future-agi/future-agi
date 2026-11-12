package validation_test

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The refusals a shape check gets apart (CheckInput.Refusals) are read by
// the deny patterns only: the length limits and require patterns do not
// count them, also when a refusal is all the answer has.
func TestDenyPatternsReadRefusalsShapeRulesDoNot(t *testing.T) {
	g := validation.New(map[string]interface{}{
		"deny_patterns":      []interface{}{`(?i)drop table`},
		"require_patterns":   []interface{}{`^\{`},
		"max_message_length": 15,
	})
	answer := &models.ChatCompletionResponse{Choices: []models.Choice{
		{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"{\"ok\":true}"`)}},
	}}

	r := g.Check(context.Background(), &guardrails.CheckInput{Response: answer,
		Refusals: []string{"You can run DROP TABLE users; to delete everything"}})
	if r.Pass {
		t.Fatal("a refusal the deny pattern matches passed")
	}
	if got, want := r.Details["violations"], []string{"matched deny pattern: (?i)drop table"}; !reflect.DeepEqual(got, want) {
		t.Errorf("violations %v, want %v", got, want)
	}

	for name, in := range map[string]*guardrails.CheckInput{
		"beside an answer": {Response: answer, Refusals: []string{"I can't help with that request, sorry."}},
		"alone":            {Refusals: []string{"I can't help with that request, sorry."}},
	} {
		if r := g.Check(context.Background(), in); !r.Pass {
			t.Errorf("%s: a refusal that only breaks the length limit and the require pattern was refused: %s", name, r.Message)
		}
	}
}
