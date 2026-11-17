package guardrails

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An answer that mixes a refusal-only choice, an ordinary choice and a
// choice carrying both a refusal and content. Says checks read every
// choice's content and each refusal (the view). A shape check reads the
// ordinary choice and the content beside the refusal, not the refusal-only
// choice. A check of unknown kind reads every choice's content, no refusal.
// The answer keeps its refusals.
func TestRefusalsWithheldKeepContent(t *testing.T) {
	str := func(s string) *string { return &s }
	resp := &models.ChatCompletionResponse{Choices: []models.Choice{
		{Index: 0, Message: models.Message{Role: "assistant", Content: json.RawMessage(`null`), Refusal: str("I can't help with that.")}},
		{Index: 1, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"an ordinary answer"`)}},
		{Index: 2, Message: models.Message{Role: "assistant", Content: json.RawMessage(`"content beside a refusal"`), Refusal: str("No.")}},
	}}
	texts := func(r *models.ChatCompletionResponse) []string {
		var out []string
		for _, c := range r.Choices {
			if c.Message.Refusal != nil {
				t.Errorf("a check reads a refusal field: %q", *c.Message.Refusal)
			}
			text, _ := ContentText(c.Message.Content)
			out = append(out, text)
		}
		return out
	}
	v := newAnswerView(resp)

	var says []string
	for _, c := range v.resp.Choices[v.own:] {
		text, _ := ContentText(c.Message.Content)
		says = append(says, text)
	}
	if want := []string{"I can't help with that.", "No."}; !reflect.DeepEqual(says, want) {
		t.Errorf("says checks read the refusals %q, want %q", says, want)
	}

	shape, n := v.withoutRefusals(true)
	if got, want := texts(shape), []string{"an ordinary answer", "content beside a refusal"}; !reflect.DeepEqual(got, want) || n != 2 {
		t.Errorf("a shape check reads %q (%d withheld), want %q (2)", got, n, want)
	}
	unknown, n := v.withoutRefusals(false)
	if got, want := texts(unknown), []string{"", "an ordinary answer", "content beside a refusal"}; !reflect.DeepEqual(got, want) || n != 2 {
		t.Errorf("a check of unknown kind reads %q (%d withheld), want %q (2)", got, n, want)
	}
	if *resp.Choices[0].Message.Refusal != "I can't help with that." || *resp.Choices[2].Message.Refusal != "No." {
		t.Errorf("the answer lost its refusals: %+v", resp.Choices)
	}

	plain := &models.ChatCompletionResponse{Choices: resp.Choices[1:2]}
	pv := newAnswerView(plain)
	if got, n := pv.withoutRefusals(true); got != pv.resp || n != 0 {
		t.Errorf("an answer with no refusal was copied (%d withheld)", n)
	}
}
