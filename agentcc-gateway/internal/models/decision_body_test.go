package models

import (
	"strings"
	"testing"
)

// SetBody takes a rewrite of a decision's text (a check's mask) as the body
// sent upstream, keeping the model the vendor is sent and the questions, and
// refuses one that is not the same decision, leaving the body as it was.
func TestDecisionRequest_SetBody(t *testing.T) {
	const sent = `{"model":"typesafe/jev-latest","state":{"ticket":"ssn 123-45-6789"},"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}}`
	parse := func(t *testing.T) *DecisionRequest {
		t.Helper()
		req, apiErr := ParseDecisionRequest([]byte(sent))
		if apiErr != nil {
			t.Fatal(apiErr)
		}
		req.SetUpstreamModel("jev-latest")
		return req
	}

	t.Run("the same body changes nothing", func(t *testing.T) {
		req := parse(t)
		before := string(req.Body)
		if err := req.SetBody([]byte(before)); err != nil || string(req.Body) != before {
			t.Fatalf("SetBody(same) = %v, body %s", err, req.Body)
		}
	})

	t.Run("a masked state is sent, and the model can still be set", func(t *testing.T) {
		req := parse(t)
		masked := strings.Replace(string(req.Body), `"ssn 123-45-6789"`, `"ssn ***, and more text"`, 1)
		if err := req.SetBody([]byte(masked)); err != nil {
			t.Fatal(err)
		}
		if string(req.Body) != masked || req.Model != "typesafe/jev-latest" || req.UpstreamModel != "jev-latest" {
			t.Fatalf("body %s, model %q, upstream %q", req.Body, req.Model, req.UpstreamModel)
		}
		if req.StateBytes != len(`{"ticket":"ssn ***, and more text"}`) {
			t.Errorf("StateBytes = %d, want the rewritten state's size", req.StateBytes)
		}
		req.SetUpstreamModel("jev-1.13.0")
		want := strings.Replace(masked, `"jev-latest"`, `"jev-1.13.0"`, 1)
		if string(req.Body) != want {
			t.Errorf("after SetUpstreamModel: %s, want %s", req.Body, want)
		}
	})

	for _, tc := range []struct{ name, from, to string }{
		{"not JSON", `"Is it resolved?"}}}`, `"Is it resolved?"}}`},
		{"another model", `"model":"jev-latest"`, `"model":"jev-other"`},
		// The body a check rewrites is the one sent, which holds the model the
		// vendor is sent; one that held the caller's aliased model instead
		// would not be what the vendor is sent, and is refused.
		{"the caller's aliased model", `"model":"jev-latest"`, `"model":"typesafe/jev-latest"`},
		{"another question id", `"resolved":{`, `"other":{`},
		{"another question type", `"type":"noul"`, `"type":"boolean"`},
		{"another format", `"questions":{"resolved":{"type":"noul","instructions":"Is it resolved?"}}`, `"questions":[{"type":"noul","name":"resolved","instructions":"Is it resolved?"}]`},
	} {
		t.Run("refused: "+tc.name, func(t *testing.T) {
			req := parse(t)
			before := string(req.Body)
			changed := strings.Replace(before, tc.from, tc.to, 1)
			if changed == before {
				t.Fatalf("the test's rewrite of %q changed nothing", tc.from)
			}
			if err := req.SetBody([]byte(changed)); err == nil {
				t.Fatalf("SetBody took %s", changed)
			}
			if string(req.Body) != before {
				t.Errorf("the body changed: %s", req.Body)
			}
		})
	}
}

// A decision that repeats a key in the same object, at any depth, is refused
// (400 duplicate_field, without naming the key): parsers differ on which
// occurrence they keep, so a vendor could act on a value no check read. A
// repeated model and a repeated System One question id keep their own
// errors, and the same key in different objects is not a repeat.
func TestParseDecisionRequest_RepeatedKeysAreRefused(t *testing.T) {
	for _, tc := range []struct{ name, body string }{
		{"a top-level key", `{"model":"m","state":"a","state":"b","questions":{"q":{"type":"noul"}}}`},
		{"a key in the state", `{"model":"m","state":{"t":"a","t":"b"},"questions":{"q":{"type":"noul"}}}`},
		{"a key in a question", `{"model":"m","state":"s","questions":{"q":{"type":"noul","instructions":"a","instructions":"b"}}}`},
		{"a key in an object in a list", `{"model":"m","state":{"items":[{"x":1},{"x":2,"x":3}]},"questions":{"q":{"type":"noul"}}}`},
		{"a key deep in the state", `{"model":"m","state":{"a":{"b":{"c":[[{"d":1,"d":2}]]}}},"questions":{"q":{"type":"noul"}}}`},
		{"OpenAI Decisions: the input", `{"model":"m","input":"a","input":"b","questions":[{"type":"predicate"}]}`},
		{"OpenAI Decisions: a key in a question", `{"model":"m","input":"a","questions":[{"type":"predicate","name":"a","name":"b"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, apiErr := ParseDecisionRequest([]byte(tc.body))
			if apiErr == nil || apiErr.Status != 400 || apiErr.Code != "duplicate_field" {
				t.Fatalf("ParseDecisionRequest = %+v, want 400 duplicate_field", apiErr)
			}
			if !strings.Contains(apiErr.Message, "Send each key once") || apiErr.Param != nil {
				t.Errorf("message %q param %v, want plain words and no key named", apiErr.Message, apiErr.Param)
			}
		})
	}
	ok := `{"model":"m","state":{"a":{"x":1},"b":{"x":2},"l":[{"x":1},{"x":2}],"x":"top"},"questions":{"q":{"type":"noul","x":1},"r":{"type":"noul","x":2}}}`
	if _, apiErr := ParseDecisionRequest([]byte(ok)); apiErr != nil {
		t.Errorf("the same key in different objects was refused: %+v", apiErr)
	}
	if _, apiErr := ParseDecisionRequest([]byte(`{"model":"m","model":"n","state":"s","questions":{"q":{"type":"noul"}}}`)); apiErr == nil || apiErr.Code != "invalid_json" {
		t.Errorf("a repeated model: %+v, want its own invalid_json", apiErr)
	}
	if _, apiErr := ParseDecisionRequest([]byte(`{"model":"m","state":"s","questions":{"q":{"type":"noul"},"q":{"type":"noul"}}}`)); apiErr == nil || apiErr.Code != "invalid_question" {
		t.Errorf("a repeated question id: %+v, want its own invalid_question", apiErr)
	}
}
