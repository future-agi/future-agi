package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
)

// unsignedTranscript is a request whose earlier assistant tool call carries
// no Gemini thought signature (eligibility rule 10).
func unsignedTranscript(model string) string {
	return `{"model":"` + model + `","messages":[{"role":"user","content":"weather?"},` +
		`{"role":"assistant","tool_calls":[{"id":"call_1","type":"function","function":{"name":"w","arguments":"{}"}}]},` +
		`{"role":"tool","tool_call_id":"call_1","content":"sunny"}]}`
}

func thoughtSigGateway(t *testing.T, models []string) *eligGateway {
	const g3, g25 = "google/gemini-3-pro", "google/gemini-2.5-pro"
	return newEligGateway(t, eligOpts{org: optedIn(), thoughtSigModels: models, orModels: map[string]*catalog.Model{
		g3: eligModel(g3, 0.1e-6), g25: eligModel(g25, 0.1e-6),
	}})
}

// With catalog.thought_signature_models unset (the
// default), the engine's own rule applies: an unsigned tool transcript is
// refused on a Gemini 3 model and allowed on google/gemini-2.5-pro.
func TestThoughtSignatureModelsUnsetUsesEngineRule(t *testing.T) {
	g := thoughtSigGateway(t, nil)
	expectRefused(t, g.chat(t, unsignedTranscript("google/gemini-3-pro")), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeToolHistoryIncompatible)
	if w := g.chat(t, unsignedTranscript("google/gemini-2.5-pro")); w.Code != http.StatusOK {
		t.Fatalf("gemini-2.5-pro with the list unset: status %d, body %s", w.Code, w.Body)
	}
	g.snapshot(t)
}

// A configured list replaces the engine's rule and reaches every
// eligibility input: with ["google/gemini-2.5*"] the same transcript is
// refused on google/gemini-2.5-pro, and allowed on the Gemini 3 model the
// list does not name.
func TestThoughtSignatureModelsSetIsWired(t *testing.T) {
	g := thoughtSigGateway(t, []string{"google/gemini-2.5*"})
	expectRefused(t, g.chat(t, unsignedTranscript("google/gemini-2.5-pro")), http.StatusForbidden, codeModelNotPermitted, eligibility.CodeToolHistoryIncompatible)
	if n := len(g.or.received()); n != 0 {
		t.Fatalf("the refused request reached the upstream %d time(s)", n)
	}
	if w := g.chat(t, unsignedTranscript("google/gemini-3-pro")); w.Code != http.StatusOK {
		t.Fatalf("gemini-3-pro with the list set to gemini-2.5*: status %d, body %s", w.Code, w.Body)
	}
	g.snapshot(t)
}
