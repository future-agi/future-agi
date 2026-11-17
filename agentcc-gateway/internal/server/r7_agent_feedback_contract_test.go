package server

// POST /v1/feedback is frozen. The release notes told callers
// not to build on it; this contract test pins its request and response
// shapes (field names and JSON types, never values) and its errors, so they
// cannot change without failing here. Green at the release base by design
// (a freeze, not a fix).
//
// The contract: POST /v1/feedback with a key, body
// {"generation_id": "<the call's x-agentcc-request-id>", "outcome": "ok" |
// "bad" | "verify_failed", "score": <number, optional>}, at most 4 KB, one
// JSON object, no other fields.
//   - 200 {"data": {"generation_id", "outcome", "event_id"}}.
//   - 400 invalid_feedback: a body that is not that object (param null), a
//     missing or longer than 256-byte generation_id (param generation_id), an
//     outcome outside the three (param outcome).
//   - 404 generation_not_found: an unknown generation, another org's, one
//     older than an hour, or one the gateway did not keep (an untagged BYOK
//     call: send agent headers, x-agentcc-agent-id for one).

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
)

var r7FeedbackRecorded = r7Object(map[string]r7Shape{
	"data": r7Object(map[string]r7Shape{
		"generation_id": r7String(),
		"outcome":       r7String(),
		"event_id":      r7String(),
	}),
})

func TestR7FeedbackContract(t *testing.T) {
	g := newSignalsGateway(t)
	_, gen := g.chat(t, feedbackKeyA, "x-agentcc-agent-id", "researcher", "x-agentcc-step", "act")
	if gen == "" {
		t.Fatal("the chat response carries no x-agentcc-request-id")
	}

	for _, outcome := range []string{"ok", "bad", "verify_failed"} {
		for _, score := range []string{"", `,"score":0.5`} {
			w := g.feedback(t, feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":%q%s}`, gen, outcome, score))
			if w.Code != http.StatusOK || !strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") {
				t.Fatalf("%s%s: HTTP %d %q: %s", outcome, score, w.Code, w.Header().Get("Content-Type"), w.Body.String())
			}
			if extra := r7CheckShape(t, w.Body.Bytes(), r7FeedbackRecorded); len(extra) > 0 {
				t.Logf("fields outside the frozen shape (additive, allowed): %v", extra)
			}
			if !strings.Contains(w.Body.String(), gen) || !strings.Contains(w.Body.String(), `"outcome":"`+outcome+`"`) {
				t.Errorf("the response does not echo the verdict: %s", w.Body.String())
			}
		}
	}

	param := func(p string) *string { return &p }
	for _, c := range []struct {
		name   string
		key    string
		body   string
		status int
		code   string
		param  *string
	}{
		{"an outcome outside the three", feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"great"}`, gen), 400, "invalid_feedback", param("outcome")},
		{"no generation_id", feedbackKeyA, `{"outcome":"ok"}`, 400, "invalid_feedback", param("generation_id")},
		{"a 257-byte generation_id", feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"ok"}`, strings.Repeat("g", 257)), 400, "invalid_feedback", param("generation_id")},
		{"an unknown field", feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"ok","request_id":"x"}`, gen), 400, "invalid_feedback", nil},
		{"not an object", feedbackKeyA, `[]`, 400, "invalid_feedback", nil},
		{"two objects", feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"ok"} {}`, gen), 400, "invalid_feedback", nil},
		{"over 4 KB", feedbackKeyA, fmt.Sprintf(`{"generation_id":%q,"outcome":"ok","score":0%s}`, gen, strings.Repeat(" ", 4<<10)), 400, "invalid_feedback", nil},
		{"an unknown generation", feedbackKeyA, `{"generation_id":"req-unknown","outcome":"ok"}`, 404, "generation_not_found", nil},
		{"another org's generation", feedbackKeyB, fmt.Sprintf(`{"generation_id":%q,"outcome":"ok"}`, gen), 404, "generation_not_found", nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			w := g.feedback(t, c.key, c.body)
			if w.Code != c.status || r7AgentErrorCode(w.Body.Bytes()) != c.code {
				t.Fatalf("HTTP %d %s, want %d %s", w.Code, w.Body.String(), c.status, c.code)
			}
			r7CheckShape(t, w.Body.Bytes(), r7ErrorShape)
			got := r7AgentErrorParam(w.Body.Bytes())
			if want := ""; c.param != nil {
				want = *c.param
				if got != want {
					t.Errorf("param %q, want %q", got, want)
				}
			} else if got != "" {
				t.Errorf("param %q, want null", got)
			}
		})
	}
}
