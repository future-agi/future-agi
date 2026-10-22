package server

import (
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

// A token count (/v1/messages/count_tokens, /v1beta countTokens) and a
// Google embedContent pass the provider's answer through as it is, so rc has
// no chat answer. Each is logged with the status the client got, the
// provider's own status included when it refused the call, and a count with
// the count the provider returned (metadata counted_tokens). The entry's
// tokens stay 0: a count is free at the provider and consumes no tokens, and
// the platform prices a log entry's prompt tokens when the gateway reports
// no cost, so a count logged as prompt tokens would show spend that never
// happened.

// countLogUpstream answers the count routes with a count, or with the
// provider's 400 when the request holds "too long", and embedContent with an
// embedding.
func countLogUpstream(calls *upstreamCalls) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		calls.record(w, r)
		body, _ := io.ReadAll(r.Body)
		refuse := strings.Contains(string(body), "too long")
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/v1/messages/count_tokens" && refuse:
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long"}}`))
		case r.URL.Path == "/v1/messages/count_tokens":
			_, _ = w.Write([]byte(`{"input_tokens":1234}`))
		case strings.HasSuffix(r.URL.Path, ":countTokens") && refuse:
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"error":{"code":400,"message":"prompt is too long","status":"INVALID_ARGUMENT"}}`))
		case strings.HasSuffix(r.URL.Path, ":countTokens"):
			_, _ = w.Write([]byte(`{"totalTokens":1234}`))
		case strings.HasSuffix(r.URL.Path, ":embedContent"):
			_, _ = w.Write([]byte(`{"embedding":{"values":[0.1,0.2,0.3]}}`))
		}
	}
}

func newCountLogGateway(t *testing.T) (*postBlockGateway, *upstreamCalls) {
	t.Helper()
	calls := &upstreamCalls{}
	return newPostBlockGateway(t, postBlockSetup{upstream: countLogUpstream(calls)}), calls
}

// assertNoTokensOrCost: the entry records no tokens and no cost.
func assertNoTokensOrCost(t *testing.T, e map[string]any) {
	t.Helper()
	for _, k := range []string{"prompt_tokens", "completion_tokens", "total_tokens"} {
		if v, ok := e[k]; ok && v != float64(0) {
			t.Errorf("log %s = %v, want 0: a count consumes no tokens", k, v)
		}
	}
	if c, ok := e["cost"]; ok && c != float64(0) && c != "" && c != "0" {
		t.Errorf("log cost = %v, want none", c)
	}
}

func TestCountTokens_LoggedWithTheStatusAndTheCount(t *testing.T) {
	for _, sf := range countTokensSurfaces {
		t.Run(sf.name, func(t *testing.T) {
			g, calls := newCountLogGateway(t)
			code, resp := g.do(t, sf.path, sf.body("How many tokens is this?"))
			if code != http.StatusOK || calls.count() != 1 || !strings.Contains(resp, "1234") {
				t.Fatalf("status %d, provider calls %d; want 200, 1 and the count; body %s", code, calls.count(), resp)
			}
			e := g.entry(t)
			if e["status_code"] != float64(http.StatusOK) {
				t.Errorf("log status_code %v, want 200", e["status_code"])
			}
			if meta, _ := e["metadata"].(map[string]any); meta["counted_tokens"] != "1234" {
				t.Errorf("log metadata counted_tokens %v, want 1234 (metadata %v)", meta["counted_tokens"], meta)
			}
			assertNoTokensOrCost(t, e)
		})
	}
}

// The provider refused the count: the client gets the provider's status,
// and the log records that status, with no count.
func TestCountTokens_AProviderRefusalIsLoggedWithItsStatus(t *testing.T) {
	for _, sf := range countTokensSurfaces {
		t.Run(sf.name, func(t *testing.T) {
			g, calls := newCountLogGateway(t)
			code, resp := g.do(t, sf.path, sf.body("This prompt is too long."))
			if code != http.StatusBadRequest || calls.count() != 1 {
				t.Fatalf("status %d, provider calls %d; want the provider's 400 and 1; body %s", code, calls.count(), resp)
			}
			e := g.entry(t)
			if e["status_code"] != float64(http.StatusBadRequest) {
				t.Errorf("log status_code %v, want 400", e["status_code"])
			}
			if meta, _ := e["metadata"].(map[string]any); meta["counted_tokens"] != nil {
				t.Errorf("a refused count logged counted_tokens %v", meta["counted_tokens"])
			}
		})
	}
}

// A Google embedContent goes through the same handler as countTokens and is
// logged with its status too.
func TestGenAIEmbedContent_LoggedWithItsStatus(t *testing.T) {
	g, calls := newCountLogGateway(t)
	code, resp := g.do(t, fmt.Sprintf("/v1beta/models/%s:embedContent", postBlockGemini),
		`{"content":{"parts":[{"text":"embed me"}]}}`)
	if code != http.StatusOK || calls.count() != 1 {
		t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
	}
	if e := g.entry(t); e["status_code"] != float64(http.StatusOK) {
		t.Errorf("log status_code %v, want 200", e["status_code"])
	}
}

// The keys a passed-through answer is logged with are the gateway's:
// counted_tokens, response_status and usage_unreadable (why a /v1/responses
// request was priced on an estimate) are dropped from caller metadata.
func TestCallerCannotSetTheCountLogKeys(t *testing.T) {
	for _, k := range []string{"response_status", "counted_tokens", "Counted_Tokens", "usage_unreadable", "Usage_Unreadable"} {
		if !isBlockedMetadataKey(k) {
			t.Errorf("%q is writable by callers, but the handler records it", k)
		}
	}
	g, calls := newCountLogGateway(t)
	code, resp := g.doAs(t, postBlockKey, "/v1beta/models/"+postBlockGemini+":countTokens",
		`{"contents":[{"parts":[{"text":"hi"}]}]}`,
		map[string]string{"x-agentcc-metadata": `{"counted_tokens":"999999","response_status":"201","usage_unreadable":"caller-forged"}`})
	if code != http.StatusOK || calls.count() != 1 {
		t.Fatalf("status %d, provider calls %d; want 200 and 1; body %s", code, calls.count(), resp)
	}
	e := g.entry(t)
	meta, _ := e["metadata"].(map[string]any)
	if meta["counted_tokens"] != "1234" || meta["response_status"] != "200" {
		t.Errorf("log metadata counted_tokens %v response_status %v, want the provider's 1234 and 200", meta["counted_tokens"], meta["response_status"])
	}
	if v, ok := meta["usage_unreadable"]; ok {
		t.Errorf("log metadata usage_unreadable = %v, want the caller's value dropped", v)
	}
}
