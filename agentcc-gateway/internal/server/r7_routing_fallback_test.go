package server

// NG-10 (P1): the Fallbacks page shows "Model Fallback Chains" on when
// fallback_enabled is unset, and the gateway did not try the chain (it could
// not tell unset from false). An org with model_fallbacks and no explicit
// switch now gets its chains on the default failover codes; an explicit
// false stays off. Ported from TestCAB0FallbackChainThroughTheRealSeams (at
// the base: 502 after 1 call; with the switch, 200 from org-model-2).

import (
	"net/http"
	"strings"
	"testing"
)

const r7FallbackProviders = `"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model","org-model-2"],"enabled":true}}`

func r7FallbackGateway(t *testing.T, routingJSON string) (*Server, *r7Upstream) {
	t.Helper()
	up := r7StartUpstream(t, func(model string, _ int) bool { return model == "org-model" })
	srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"org-model", "org-model-2"}}}, nil)
	r7Push(t, srv, r7OrgA, `{`+r7FallbackProviders+`,"routing":`+routingJSON+`}`)
	return srv, up
}

// Chains saved without touching the switch (what the Fallbacks page's patch
// leaves) are tried, for the customer key and the internal key, streamed or
// not.
func TestR7OrgFallbackDefaultOnWhenChainsAndNoSwitch(t *testing.T) {
	for _, key := range []string{r7CustA, r7IntlA} {
		for _, stream := range []bool{false, true} {
			srv, up := r7FallbackGateway(t, `{"model_fallbacks":{"org-model":["org-model-2"]}}`)
			code, body, _ := r7Chat(t, srv, key, "org-model", stream)
			if code != http.StatusOK {
				t.Fatalf("key %s stream=%v: chain org-model -> org-model-2, no fallback_enabled, org-model 503 -> %d %s (calls %v), want 200 from org-model-2", key[:24], stream, code, body, up.snapshot())
			}
			calls := up.snapshot()
			if len(calls) != 2 || calls[1].Model != "org-model-2" {
				t.Fatalf("key %s stream=%v: upstream calls %v, want org-model then org-model-2", key[:24], stream, calls)
			}
			r7AssertOrgCredentialOnly(t, up)
		}
	}
}

// An explicit false stays off: one call, the upstream's failure.
func TestR7OrgFallbackDefaultExplicitFalseStaysOff(t *testing.T) {
	srv, up := r7FallbackGateway(t, `{"fallback_enabled":false,"model_fallbacks":{"org-model":["org-model-2"]}}`)
	code, body, _ := r7Chat(t, srv, r7CustA, "org-model", false)
	if code == http.StatusOK {
		t.Fatalf("fallback_enabled false -> 200 %s, want the upstream failure", body)
	}
	if calls := up.snapshot(); len(calls) != 1 || calls[0].Model != "org-model" {
		t.Fatalf("fallback_enabled false: upstream calls %v, want only org-model", calls)
	}
}

// An explicit true behaves as before.
func TestR7OrgFallbackDefaultExplicitTrue(t *testing.T) {
	srv, up := r7FallbackGateway(t, `{"fallback_enabled":true,"model_fallbacks":{"org-model":["org-model-2"]}}`)
	if code, body, _ := r7Chat(t, srv, r7CustA, "org-model", false); code != http.StatusOK {
		t.Fatalf("fallback_enabled true -> %d %s (calls %v), want 200", code, body, up.snapshot())
	}
}

// The gateway keeps "explicitly off" through its own store: the pushed
// config read back (GET /-/orgs/{org}/config, the store's JSON) still says
// false, and an absent switch stays absent.
func TestR7OrgFallbackDefaultExplicitFalseKeptByTheStore(t *testing.T) {
	srv, _ := r7FallbackGateway(t, `{"fallback_enabled":false,"model_fallbacks":{"org-model":["org-model-2"]}}`)
	code, body := r7AdminCall(t, srv, http.MethodGet, "/-/orgs/"+r7OrgA+"/config", "")
	if code != http.StatusOK || !strings.Contains(body, `"fallback_enabled":false`) {
		t.Fatalf("GET config -> %d %s, want fallback_enabled false kept", code, body)
	}
	srv2, _ := r7FallbackGateway(t, `{"model_fallbacks":{"org-model":["org-model-2"]}}`)
	_, body2 := r7AdminCall(t, srv2, http.MethodGet, "/-/orgs/"+r7OrgA+"/config", "")
	if strings.Contains(body2, `"fallback_enabled"`) {
		t.Fatalf("GET config of an org that never set the switch -> %s, want it absent", body2)
	}
}

// A model_map alias of an org provider that lists one model is sent as that
// model (resolveProviderOrgFirst rewrites rc.Request.Model), so the alias's
// own chain must be looked up by the requested name (rc.Model) on both chat
// paths: a customer key whose org runs its own retry gets the alias's chain
// when streaming and on a normal request alike.
func TestR7OrgFallbackChainOfAModelMapAlias(t *testing.T) {
	for _, stream := range []bool{false, true} {
		ua := r7StartUpstream(t, func(model string, _ int) bool { return model == "alpha-model" })
		ub := r7StartUpstream(t, nil)
		srv := r7Gateway(t, map[string]r7Op{"alpha": {ua, []string{"alpha-model"}}, "beta": {ub, []string{"beta-model"}}}, nil)
		r7Push(t, srv, r7OrgA, `{"providers":{`+
			`"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["alpha-model"],"enabled":true},`+
			`"beta":{"api_key":"org-beta-key","api_format":"openai","models":["beta-model"],"enabled":true}},`+
			`"model_map":{"fast":"alpha"},`+
			`"routing":{"retry":{"enabled":true,"max_retries":1,"initial_delay":"1ms","max_delay":"2ms","multiplier":1,"on_status_codes":[502,503]},`+
			`"model_fallbacks":{"fast":["beta-model"]}}}`)

		code, body, _ := r7Chat(t, srv, r7CustA, "fast", stream)
		if code != http.StatusOK {
			t.Fatalf("stream=%v: alias fast -> alpha (always 503), chain fast -> beta-model -> %d %s (alpha %v, beta %v), want 200 from beta", stream, code, body, ua.snapshot(), ub.snapshot())
		}
		if calls := ua.snapshot(); len(calls) != 2 || calls[0].Model != "alpha-model" {
			t.Fatalf("stream=%v: alpha calls %v, want alpha-model twice (the call and the org's retry)", stream, calls)
		}
		if calls := ub.snapshot(); len(calls) != 1 || calls[0].Model != "beta-model" || calls[0].Auth != "Bearer org-beta-key" {
			t.Fatalf("stream=%v: beta calls %v, want one beta-model call with the org's key", stream, calls)
		}
	}
}

// An internal key of an org that has chains and never set fallback_enabled
// runs the org's failover settings (3 attempts, the default codes) over the
// gateway's providers, as an org with fallback_enabled true always did; the
// gateway's own failover is off here, so at the base it made one call.
func TestR7InternalKeyRunsTheOrgFailoverWhenChainsAndNoSwitch(t *testing.T) {
	up := r7StartUpstream(t, func(model string, _ int) bool { return model == "gw-model" })
	srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"gw-model"}}, "beta": {up, []string{"gw-model"}}}, nil)
	r7Push(t, srv, r7OrgA, `{"routing":{"model_fallbacks":{"other-model":["gw-model"]}}}`)

	code, body, _ := r7Chat(t, srv, r7IntlA, "gw-model", false)
	if code == http.StatusOK {
		t.Fatalf("both gateway providers fail -> 200 %s", body)
	}
	seen := map[string]bool{}
	for _, c := range up.snapshot() {
		seen[c.Auth] = true
	}
	if !seen["Bearer node-alpha-key"] || !seen["Bearer node-beta-key"] {
		t.Fatalf("internal key, org chains and no switch, gateway failover off: calls %v, want a failover across the gateway's alpha and beta", up.snapshot())
	}
}
