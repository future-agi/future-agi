package server

// R7-REQUEST review R1 (NG-26) through the whole gateway: the SDK's nested
// GatewayConfig in x-agentcc-config answers 200 as it did at the base; the
// cache fields with a header of their own apply, and what is not read is
// named in x-agentcc-config-ignored.

import (
	"net/http"
	"testing"
)

func TestR7AgentccConfigHeaderSDKNestedEndToEnd(t *testing.T) {
	capt := &r7Capture{}
	srv := newR7Gateway(t, capt)

	plain := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, nil)
	if plain.Code != http.StatusOK {
		t.Fatalf("a plain chat -> %d; body %s", plain.Code, plain.Body.String())
	}
	defaultTimeout := plain.Header().Get("x-agentcc-timeout-ms")

	// {"cache":{"ttl":300}}: the docs' per-request shape.
	w := r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"cache":{"ttl":300}}`})
	meta, _, _ := capt.snapshot()
	if w.Code != http.StatusOK || meta["cache_ttl"] != "300" {
		t.Errorf(`{"cache":{"ttl":300}} -> %d, metadata cache_ttl %q; want 200 and 300; body %s`, w.Code, meta["cache_ttl"], w.Body.String())
	}
	if v, ok := w.Header()["X-Agentcc-Config-Ignored"]; ok {
		t.Errorf(`{"cache":{"ttl":300}}: x-agentcc-config-ignored %q, want none`, v)
	}

	// {"timeout":1234}: the audit probe's numeric timeout. Its unit is not
	// known, so it is named and the request keeps its timeout.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"timeout":1234}`})
	if w.Code != http.StatusOK || w.Header().Get("x-agentcc-timeout-ms") != defaultTimeout || w.Header().Get("x-agentcc-config-ignored") != "timeout" {
		t.Errorf(`{"timeout":1234} -> %d, timeout-ms %q (default %q), ignored %q; want 200, the default timeout and "timeout"; body %s`,
			w.Code, w.Header().Get("x-agentcc-timeout-ms"), defaultTimeout, w.Header().Get("x-agentcc-config-ignored"), w.Body.String())
	}

	// The audit probe's nested timeout, cache and retry.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{
		"x-agentcc-config": `{"timeout":{"total":1.234},"cache":{"ttl":300,"namespace":"prod"},"retry":{"max_retries":3}}`,
	})
	meta, _, _ = capt.snapshot()
	if w.Code != http.StatusOK || meta["cache_ttl"] != "300" || meta["cache_namespace"] != "prod" {
		t.Errorf("nested timeout/cache/retry -> %d, metadata cache_ttl %q cache_namespace %q; want 200, 300, prod; body %s", w.Code, meta["cache_ttl"], meta["cache_namespace"], w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-config-ignored"); got != "retry,timeout" {
		t.Errorf("nested timeout/cache/retry: x-agentcc-config-ignored %q, want retry,timeout", got)
	}

	// The ignored header is on a response the gateway refuses too (no key).
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, "", map[string]string{"x-agentcc-config": `{"retry":{"max_retries":3}}`})
	if w.Code != http.StatusUnauthorized || w.Header().Get("x-agentcc-config-ignored") != "retry" {
		t.Errorf("no key with a nested retry -> %d, ignored %q; want 401 and retry", w.Code, w.Header().Get("x-agentcc-config-ignored"))
	}

	// Review 2 (P3): a Python float ttl=300.0 is 300 seconds, as at the base
	// a request carrying it was answered 200.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"cache":{"ttl":300.0,"namespace":"py"}}`})
	meta, _, _ = capt.snapshot()
	if w.Code != http.StatusOK || meta["cache_ttl"] != "300" || meta["cache_namespace"] != "py" {
		t.Errorf(`{"cache":{"ttl":300.0}} -> %d, metadata cache_ttl %q cache_namespace %q; want 200, 300, py; body %s`, w.Code, meta["cache_ttl"], meta["cache_namespace"], w.Body.String())
	}

	// A bad value in a field the gateway applies is still refused.
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"cache":{"ttl":300.5}}`})
	if w.Code != http.StatusBadRequest || r7Code(w) != "invalid_agentcc_config" {
		t.Errorf(`{"cache":{"ttl":300.5}} -> %d %s, want 400 invalid_agentcc_config`, w.Code, r7Code(w))
	}
	w = r7Do(srv, "POST", "/v1/chat/completions", r7ChatBody, r7ByokKey, map[string]string{"x-agentcc-config": `{"cache":{"ttl":-1}}`})
	if w.Code != http.StatusBadRequest || r7Code(w) != "invalid_agentcc_config" {
		t.Errorf(`{"cache":{"ttl":-1}} -> %d %s, want 400 invalid_agentcc_config`, w.Code, r7Code(w))
	}
}
