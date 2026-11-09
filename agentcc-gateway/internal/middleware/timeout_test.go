package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// deadlineOf runs one request through h and reports whether the handler saw
// a deadline on its context.
func deadlineOf(t *testing.T, h func(http.Handler) http.Handler, path string, header map[string]string) (time.Duration, bool) {
	t.Helper()
	var left time.Duration
	var has bool
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var dl time.Time
		dl, has = r.Context().Deadline()
		if has {
			left = time.Until(dl)
		}
	})
	req := httptest.NewRequest(http.MethodGet, path, nil)
	for k, v := range header {
		req.Header.Set(k, v)
	}
	h(inner).ServeHTTP(httptest.NewRecorder(), req)
	return left, has
}

func TestTimeoutSkipsMCPPrefixes(t *testing.T) {
	mw := TimeoutSkipping(time.Minute, []string{"/v1/chat/completions"},
		[]string{"/mcp", "/mcp/", "/.well-known/oauth-protected-resource/"})
	for _, tc := range []struct {
		path string
		skip bool
	}{
		{"/v1/chat/completions", true},
		{"/mcp", true},
		{"/mcp/", true},
		{"/mcp/v/vs_1", true},
		{"/mcp/approvals/ap_1", true},
		{"/.well-known/oauth-protected-resource/mcp", true},
		{"/.well-known/oauth-protected-resource/mcp/v/vs_1", true},
		{"/mcpx", false},
		{"/v1/mcp", false},
		{"/.well-known/oauth-protected-resource", false},
		{"/v1/chat/completions/x", false},
		{"/v1/models", false},
	} {
		_, has := deadlineOf(t, mw, tc.path, nil)
		if has == tc.skip {
			t.Errorf("%s: deadline set = %v, want skipped = %v", tc.path, has, tc.skip)
		}
	}
}

func TestTimeoutKeepsSignatureAndBehaviour(t *testing.T) {
	mw := Timeout(time.Minute, "/v1/chat/completions")
	if _, has := deadlineOf(t, mw, "/v1/chat/completions", nil); has {
		t.Error("an exact skip path got a deadline")
	}
	if left, has := deadlineOf(t, mw, "/v1/models", nil); !has || left > time.Minute || left < 50*time.Second {
		t.Errorf("default deadline = %v (set %v), want about one minute", left, has)
	}
	if left, has := deadlineOf(t, mw, "/v1/models", map[string]string{"x-agentcc-timeout": "2s"}); !has || left > 2*time.Second {
		t.Errorf("header deadline = %v (set %v), want at most 2s", left, has)
	}
	if left, has := deadlineOf(t, mw, "/v1/models", map[string]string{"x-agentcc-request-timeout": "1500"}); !has || left > 1500*time.Millisecond {
		t.Errorf("ms header deadline = %v (set %v), want at most 1.5s", left, has)
	}
	if _, has := deadlineOf(t, mw, "/mcp", nil); !has {
		t.Error("Timeout without prefixes skipped /mcp")
	}
}

func TestTimeoutSkippingEmptyPrefixIgnored(t *testing.T) {
	mw := TimeoutSkipping(time.Minute, nil, []string{""})
	if _, has := deadlineOf(t, mw, "/anything", nil); !has {
		t.Error("an empty prefix skipped every path")
	}
}
