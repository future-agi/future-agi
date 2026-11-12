package requestview

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A decision body reads as what the decision model reads: each field but the
// model and the questions as a user message (a string as its text, any other
// value as its JSON text), and each question as a system message: its id (a
// System One question's key) and then each of its fields, its type and name
// included, in the body's order.
func TestDecision_ReadsWhatTheModelReads(t *testing.T) {
	for _, tc := range []struct {
		name string
		body string
		want []string
	}{
		{"System One: a state object and a question's instructions",
			`{"model":"typesafe/jev-latest","state":{"ticket":"refund sent","n":3},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}}`,
			[]string{`user: {"ticket":"refund sent","n":3}`, "system: resolved\nnoul\nIs it resolved?"}},
		{"System One: a string state, two questions, one with no instructions",
			`{"model":"m","questions":{"a":{"type":"noul","instructions":"First?"},"b":{"type":"noul"}},"state":"the ticket"}`,
			[]string{"system: a\nnoul\nFirst?", "system: b\nnoul", "user: the ticket"}},
		{"OpenAI Decisions: the input and each question's type, name and instructions",
			`{"model":"openai/gpt-6-luna","input":"refund sent","questions":[{"type":"predicate","name":"resolved","instructions":"Is it resolved?"},{"type":"predicate","name":"secret-name","instructions":"Is it late?"}]}`,
			[]string{"user: refund sent", "system: predicate\nresolved\nIs it resolved?", "system: predicate\nsecret-name\nIs it late?"}},
		{"another field of the body and of a question is read",
			`{"model":"m","input":"x","context":"more text","questions":[{"type":"choice","instructions":"Pick","options":["yes","no"]}]}`,
			[]string{"user: x", "user: more text", "system: choice\nPick\n" + `["yes","no"]`}},
		{"a key the model reads in the state is read, escapes decoded",
			`{"model":"m","state":{"café owner":"Ann"},"questions":{"q":{"type":"noul","instructions":"Who?"}}}`,
			[]string{`user: {"café owner":"Ann"}`, "system: q\nnoul\nWho?"}},
		{"of a repeated key, the last",
			`{"model":"m","state":"first","state":"second","questions":{"q":{"type":"noul","instructions":"a","instructions":"b"}}}`,
			[]string{"user: second", "system: q\nnoul\nb"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			v := Decision([]byte(tc.body))
			got := shape(t, readOK(t, v.View))
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("chat shape\n got %q\nwant %q", got, tc.want)
			}
		})
	}
}

// A decision's answer is a decision, not model text: the checks on the
// answer do not read its request (models.NoChatAnswer).
func TestDecision_IsNoChatAnswer(t *testing.T) {
	var pt models.PassThroughRequest = Decision([]byte(`{"model":"m","state":"s","questions":{"q":{"type":"noul"}}}`))
	if _, ok := pt.(models.NoChatAnswer); !ok {
		t.Fatal("a decision view is not models.NoChatAnswer")
	}
}

// A check's rewrite (pii-detection's mask) is written into the decision body
// where it was read: a string in place, a JSON value as the JSON the check
// left. The model and every label keep their bytes, and the body reads back
// as the checks left it.
func TestDecision_WritesTheRewriteIntoTheBody(t *testing.T) {
	for _, tc := range []struct {
		name     string
		body     string
		rewrites map[int]string
		want     string
	}{
		{"the state object",
			`{"model":"jev-latest","state":{"ticket":"ssn 1", "n":3},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}}`,
			map[int]string{0: `{"ticket":"ssn ***","n":3}`},
			`{"model":"jev-latest","state":{"ticket":"ssn ***","n":3},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}}`},
		{"a question's instructions",
			`{"model":"jev-latest","state":{"ticket":"ok"},"questions":{"resolved":{"type":"noul","instructions":"ssn 1?"}}}`,
			map[int]string{1: "resolved\nnoul\nssn ***?"},
			`{"model":"jev-latest","state":{"ticket":"ok"},"questions":{"resolved":{"type":"noul","instructions":"ssn ***?"}}}`},
		{"the OpenAI input and a question's instructions",
			`{"model":"gpt-6-luna","input":"ssn 1","questions":[{"type":"predicate","name":"r","instructions":"ssn 2?"}]}`,
			map[int]string{0: "ssn ***", 1: "predicate\nr\nssn ***?"},
			`{"model":"gpt-6-luna","input":"ssn ***","questions":[{"type":"predicate","name":"r","instructions":"ssn ***?"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			v := Decision([]byte(tc.body))
			if err := rewrite(t, v.View, tc.rewrites); err != nil {
				t.Fatalf("ApplyChecks: %v", err)
			}
			if got := string(v.Body()); got != tc.want {
				t.Errorf("body\n got %s\nwant %s", got, tc.want)
			}
			if _, apiErr := models.ParseDecisionRequest(v.Body()); apiErr != nil {
				t.Errorf("the rewritten body is not a decision: %v", apiErr)
			}
		})
	}
}

// A rewrite the body cannot take is refused, and the body stays as it was:
// a number masked into text is not a number.
func TestDecision_ARewriteThatCannotBeWrittenIsRefused(t *testing.T) {
	body := `{"model":"m","state":123456789,"questions":{"q":{"type":"noul","instructions":"ok"}}}`
	v := Decision([]byte(body))
	if err := rewrite(t, v.View, map[int]string{0: "***"}); err == nil {
		t.Fatal("a mask of a number was written into the body")
	}
	if string(v.Body()) != body {
		t.Errorf("the body changed: %s", v.Body())
	}
}

// A state nested more than MaxDepth deep is read above that, and the view
// says where it stopped, so a check that would block or rewrite the request
// refuses it (request_unavailable).
func TestDecision_StateNestedTooDeepIsSaid(t *testing.T) {
	state := strings.Repeat(`{"a":`, MaxDepth+2) + `"x"` + strings.Repeat(`}`, MaxDepth+2)
	v := Decision([]byte(`{"model":"m","state":` + state + `,"questions":{"q":{"type":"noul","instructions":"ok"}}}`))
	req, reason := v.ChatRequest(context.Background())
	if req == nil || !strings.Contains(reason, "state") {
		t.Fatalf("request %v, reason %q; want what can be read and a reason naming the state", req, reason)
	}
}

// A check's rewrite of a label (a question's id, name or type) cannot be
// written: the gateway sends labels as they are, so the rewrite is refused in
// plain words that do not quote the label, and the body stays as it was.
func TestDecision_ARewriteOfALabelIsRefused(t *testing.T) {
	for _, tc := range []struct {
		name, body, rewritten, what string
	}{
		{"a System One question's id",
			`{"model":"m","state":"ok","questions":{"ssn 123-45-6789":{"type":"noul","instructions":"ok?"}}}`,
			"ssn ***\nnoul\nok?", "a question's id"},
		{"a question's type",
			`{"model":"m","state":"ok","questions":{"q":{"type":"ssn 123-45-6789","instructions":"ok?"}}}`,
			"q\nssn ***\nok?", "a question's type"},
		{"an OpenAI question's name",
			`{"model":"m","input":"ok","questions":[{"type":"predicate","name":"ssn 123-45-6789","instructions":"ok?"}]}`,
			"predicate\nssn ***\nok?", "a question's name"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			v := Decision([]byte(tc.body))
			err := rewrite(t, v.View, map[int]string{1: tc.rewritten})
			if err == nil {
				t.Fatalf("the rewrite of %s was written: %s", tc.what, v.Body())
			}
			if msg := err.Error(); !strings.Contains(msg, tc.what) || !strings.Contains(msg, "take that text out of it") || strings.Contains(msg, "123-45-6789") {
				t.Errorf("error %q, want plain words naming %s and not quoting it", msg, tc.what)
			}
			if string(v.Body()) != tc.body {
				t.Errorf("the body changed: %s", v.Body())
			}
		})
	}
}
