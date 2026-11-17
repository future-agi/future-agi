package credtemplate

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

// TestRoundTripRefusesAControlCharacterInAHeader: Compile refuses a header
// part whose prefix or value holds a control character, and the round
// tripper checks again as it attaches the parts, so CR, LF, NUL, a tab or
// DEL never reaches a header even in a template built some other way. The
// request is refused before it is sent, naming the header and never the
// value.
func TestRoundTripRefusesAControlCharacterInAHeader(t *testing.T) {
	var reached atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { reached.Add(1) }))
	defer srv.Close()
	u, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range []Part{
		{In: InHeader, Name: "Authorization", Prefix: "Token token=", Value: "attach-test-k\r\nX-Injected: 1"},
		{In: InHeader, Name: "Authorization", Prefix: "Token\ttoken=", Value: "attach-test-k"},
		{In: InHeader, Name: "Authorization", Prefix: "Token token=", Value: "attach-test-k\x00"},
		{In: InHeader, Name: "Authorization", Prefix: "Token token=\n", Value: "attach-test-k"},
		{In: InHeader, Name: "X-Api-Key", Value: "attach-test-k\x7f"},
		{In: InHeader, Name: "X-Api-Key", Value: "attach-test-k\tv"},
	} {
		if _, err := Compile(srv.URL+"/mcp", bad); err == nil {
			t.Errorf("Compile placed a %s part with a control character", bad.Name)
		}
		bad.Name = http.CanonicalHeaderKey(bad.Name)
		tpl := &Template{origin: origin(u), parts: []Part{bad}, headers: []Part{bad},
			values: []string{bad.Value}, secrets: longestFirst(spellings(bad.Value))}
		req, err := http.NewRequest(http.MethodGet, srv.URL+"/mcp", nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := tpl.RoundTripper(http.DefaultTransport).RoundTrip(req)
		if resp != nil {
			resp.Body.Close()
		}
		var ce *Error
		if !errors.As(err, &ce) || !strings.Contains(ce.Reason, bad.Name) || !strings.Contains(ce.Reason, "control character") {
			t.Errorf("%s %q+%q: err %v, want a refusal naming the header", bad.Name, bad.Prefix, bad.Value, err)
			continue
		}
		if strings.Contains(err.Error(), "attach-test-k") {
			t.Errorf("%s: the refusal carries the value", bad.Name)
		}
	}
	if n := reached.Load(); n != 0 {
		t.Fatalf("%d requests with a control character in a header reached the server", n)
	}
}
