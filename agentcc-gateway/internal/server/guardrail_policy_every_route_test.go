package server

import (
	"net/http"
	"strings"
	"testing"
)

// X-Guardrail-Policy picks the request's guardrail policy (log-only,
// disabled, strict) for a key with allow_policy_override, on every route a
// check on the request reads, as on /v1/chat/completions: /v1/messages
// (translated, passed through, streamed), /v1beta generateContent and
// streamGenerateContent, /v1/responses (not streamed, streamed, background)
// and the two count routes. A key without the permission that sends the
// header is refused before the provider is called, whatever the value, and
// a permitted key's value that is not a policy is refused as invalid.
//
// The probe is pii-detection as a rule in the gateway's config file at the
// pre stage (the policy applies to config-file rules, on chat as here).

// overrideSurfaces are the routes the header must reach: requestSurfaces (the
// chat routes are the controls) and the count routes.
func overrideSurfaces() []requestSurface {
	return append(append([]requestSurface{}, requestSurfaces...), countTokensSurfaces...)
}

func newOverrideRouteGateway(t *testing.T, action string) (*postBlockGateway, *upstreamCalls) {
	t.Helper()
	calls := &upstreamCalls{}
	return newPostBlockGateway(t, postBlockSetup{
		requestRule: requestCheck, requestAction: action, upstream: countTokensUpstream(calls),
	}), calls
}

func sendWithOverride(t *testing.T, g *postBlockGateway, key string, sf requestSurface, text, policy string) (int, string) {
	t.Helper()
	headers := map[string]string{}
	if policy != "" {
		headers["X-Guardrail-Policy"] = policy
	}
	return g.doAs(t, key, sf.path, sf.body(text), headers)
}

// A key without allow_policy_override that sends the header is refused with
// 403 before the provider is called, whatever the value, on a request the
// rule would pass.
func TestGuardrailPolicyOverride_EveryRouteRefusesAKeyWithoutPermission(t *testing.T) {
	for _, sf := range overrideSurfaces() {
		for _, value := range []string{"disabled", "log-only", "strict", "not-a-policy"} {
			t.Run(sf.name+" / "+value, func(t *testing.T) {
				g, calls := newOverrideRouteGateway(t, "block")
				code, resp := sendWithOverride(t, g, postBlockKey, sf, postBlockPrompt, value)
				if code != http.StatusForbidden || !strings.Contains(resp, "does not allow guardrail policy overrides") {
					t.Errorf("status %d, want 403 saying the key does not allow overrides; body %s", code, resp)
				}
				if n := calls.count(); n != 0 {
					t.Errorf("the provider was called %d times (%v), want 0", n, calls.paths)
				}
			})
		}
	}
}

// A permitted key's override applies: disabled turns the rules off and
// log-only makes them log, so a request the rule blocks goes on; strict
// makes a rule in log mode block, so a request it would let through is
// refused before the provider is called; a value that is not a policy is
// refused 400.
func TestGuardrailPolicyOverride_EveryRouteAppliesAPermittedOverride(t *testing.T) {
	for _, sf := range overrideSurfaces() {
		for _, tc := range []struct {
			policy, action string
			want, calls    int
		}{
			{"disabled", "block", http.StatusOK, 1},
			{"log-only", "block", http.StatusOK, 1},
			{"strict", "log", http.StatusForbidden, 0},
			{"not-a-policy", "block", http.StatusBadRequest, 0},
		} {
			t.Run(sf.name+" / "+tc.policy, func(t *testing.T) {
				g, calls := newOverrideRouteGateway(t, tc.action)
				code, resp := sendWithOverride(t, g, postBlockOverrideKey, sf, requestCheckPrompt, tc.policy)
				if code != tc.want || calls.count() != tc.calls {
					t.Errorf("status %d, provider calls %d; want %d and %d; body %s", code, calls.count(), tc.want, tc.calls, resp)
				}
				if tc.policy == "not-a-policy" && !strings.Contains(resp, "invalid X-Guardrail-Policy value") {
					t.Errorf("body %s, want it to name the invalid X-Guardrail-Policy value", resp)
				}
			})
		}
	}
}

// Without the header, the rule runs as the config file sets it, for both
// keys (the set-up works): block refuses the request, log lets it through.
func TestGuardrailPolicyOverride_EveryRouteRunsTheRuleWithoutTheHeader(t *testing.T) {
	for _, sf := range overrideSurfaces() {
		for _, key := range []string{postBlockKey, postBlockOverrideKey} {
			t.Run(sf.name+" / "+key, func(t *testing.T) {
				g, calls := newOverrideRouteGateway(t, "block")
				if code, resp := sendWithOverride(t, g, key, sf, requestCheckPrompt, ""); code != http.StatusForbidden || calls.count() != 0 {
					t.Errorf("block: status %d, provider calls %d; want 403 and 0; body %s", code, calls.count(), resp)
				}
				g, calls = newOverrideRouteGateway(t, "log")
				if code, resp := sendWithOverride(t, g, key, sf, requestCheckPrompt, ""); code != http.StatusOK || calls.count() != 1 {
					t.Errorf("log: status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
				}
			})
		}
	}
}
