package server

import (
	"net/http"
	"strings"
	"testing"
)

// A withheld answer's log entry says so: metadata answer_withheld "true". The
// provider answered and the request was priced, but the client got the
// check's refusal; a request refused before the provider was called carries
// no such mark, nor does a served answer. The console tells the two refusals
// apart by it.

func loggedMetadata(e map[string]any) map[string]any {
	meta, _ := e["metadata"].(map[string]any)
	return meta
}

func TestWithheldAnswerIsMarkedInTheLog(t *testing.T) {
	for _, tc := range []struct {
		name     string
		setup    postBlockSetup
		code     int
		withheld bool
	}{
		{"an output rule blocks the answer", postBlockSetup{staticRule: answerBlockRule, priced: true}, http.StatusForbidden, true},
		{"the org's output check blocks the answer", postBlockSetup{orgCheck: true, priced: true}, http.StatusForbidden, true},
		{"no output check can read the answer", postBlockSetup{staticRule: leakageCheck, priced: true,
			upstream: refusalAudioUpstream(refusalAudioCases()["audio-bare"])}, http.StatusForbidden, true},
		{"a request rule refuses before the provider", postBlockSetup{preRule: true, priced: true}, http.StatusForbidden, false},
		{"the answer is served", postBlockSetup{staticRule: answerBlockRule, action: "log", priced: true}, http.StatusOK, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			if code, body := g.chat(t, false); code != tc.code {
				t.Fatalf("status %d, body %s; want %d", code, body, tc.code)
			}
			e := g.entry(t)
			got, ok := loggedMetadata(e)["answer_withheld"]
			if tc.withheld && got != "true" {
				t.Errorf("log metadata answer_withheld = %v (present %t), want \"true\"", got, ok)
			}
			if !tc.withheld && ok {
				t.Errorf("log metadata answer_withheld = %v, want it absent", got)
			}
			// A withheld answer was priced like a served one; a request refused
			// before the provider was not.
			cost, _ := e["cost"].(float64)
			if tc.code == http.StatusOK || tc.withheld {
				if cost <= 0 {
					t.Errorf("log cost = %v, want the request's priced cost", e["cost"])
				}
			} else if cost != 0 {
				t.Errorf("log cost = %v for a request refused before the provider, want 0", e["cost"])
			}
		})
	}
}

// A caller cannot mark its own request: answer_withheld is the gateway's name,
// dropped from request metadata like the gateway's other names.
func TestCallerCannotMarkAnAnswerWithheld(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule, action: "log"})
	body := `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}],"metadata":{"answer_withheld":"true","team_label":"kept"}}`
	if code, resp := g.do(t, "/v1/chat/completions", body); code != http.StatusOK {
		t.Fatalf("status %d, body %s; want 200", code, resp)
	}
	meta := loggedMetadata(g.entry(t))
	if got, ok := meta["answer_withheld"]; ok {
		t.Errorf("a caller's answer_withheld reached the log as %v", got)
	}
	if !strings.EqualFold(stringOr(meta["team_label"]), "kept") {
		t.Errorf("the caller's other metadata did not land: %v", meta)
	}
}

func stringOr(v any) string {
	s, _ := v.(string)
	return s
}
