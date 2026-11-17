package credtemplate_test

import (
	"io"
	"net/http"
	"strings"
	"testing"
	"testing/iotest"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// streamer is an inner transport that answers every request with body as
// an event stream, read through wrap.
type streamer struct {
	body string
	wrap func(io.Reader) io.Reader
}

func (s streamer) RoundTrip(r *http.Request) (*http.Response, error) {
	var rd io.Reader = strings.NewReader(s.body)
	if s.wrap != nil {
		rd = s.wrap(rd)
	}
	return &http.Response{StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream; charset=utf-8"}},
		Body: io.NopCloser(rd), Request: r}, nil
}

// TestEndpointEventRewritten: the endpoint event that opens an HTTP+SSE
// stream from the template's origin names its endpoint in the template's
// form (a variable's value back to {name}, a query part's parameter
// dropped); every other byte of the stream, later events included, passes
// through as it came, however the body is read.
func TestEndpointEventRewritten(t *testing.T) {
	tpl, err := credtemplate.Compile("https://h.example.com/m/{t}/sse",
		credtemplate.Part{In: credtemplate.InURLVariable, Name: "t", Value: "sv/1"},
		credtemplate.Part{In: credtemplate.InQuery, Name: "q", Value: "qv 2"})
	if err != nil {
		t.Fatal(err)
	}
	const later = "event: message\ndata: {\"echo\":\"sv/1\"}\n\n"
	for _, tc := range []struct {
		name, in, want string
		wrap           func(io.Reader) io.Reader
	}{
		{"LF", "event: endpoint\ndata: /m/sv%2F1/messages?sid=1&q=qv+2\n\n" + later,
			"event: endpoint\ndata: /m/{t}/messages?sid=1\n\n" + later, nil},
		{"read one byte at a time", "event: endpoint\ndata: /m/sv%2F1/messages?sid=1&q=qv+2\n\n" + later,
			"event: endpoint\ndata: /m/{t}/messages?sid=1\n\n" + later, iotest.OneByteReader},
		{"CRLF", "event: endpoint\r\ndata: /m/sv%2F1/messages\r\n\r\n" + later,
			"event: endpoint\r\ndata: /m/{t}/messages\r\n\r\n" + later, nil},
		{"CR", "event: endpoint\rdata: /m/sv%2F1/messages\r\r",
			"event: endpoint\rdata: /m/{t}/messages\r\r", nil},
		{"a comment first", ": ok\n\nevent: endpoint\ndata:/m/sv%2f1/x\n\n",
			": ok\n\nevent: endpoint\ndata: /m/{t}/x\n\n", iotest.HalfReader},
		{"an absolute URL", "event: endpoint\ndata: https://h.example.com/m/sv%2F1/x?q=qv+2\n\n",
			"event: endpoint\ndata: https://h.example.com/m/{t}/x\n\n", nil},
		{"a first event that is not the endpoint", "data: /m/sv%2F1\n\nevent: endpoint\ndata: /m/sv%2F1\n\n",
			"data: /m/sv%2F1\n\nevent: endpoint\ndata: /m/sv%2F1\n\n", nil},
		{"a stream that ends before its first event does", "event: endpoint\ndata: /m/sv%2F1",
			"event: endpoint\ndata: /m/sv%2F1", nil},
	} {
		resp, err := (&http.Client{Transport: tpl.RoundTripper(streamer{tc.in, tc.wrap})}).Get("https://h.example.com/m/{t}/sse")
		if err != nil {
			t.Fatal(err)
		}
		got, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil || string(got) != tc.want {
			t.Errorf("%s: read %q (err %v), want %q", tc.name, got, err, tc.want)
		}
	}

	t.Run("a keep-alive comment is released before the first event comes", func(t *testing.T) {
		pr, pw := io.Pipe()
		inner := roundTripFunc(func(r *http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: http.StatusOK, Header: http.Header{"Content-Type": {"text/event-stream"}},
				Body: pr, Request: r}, nil
		})
		resp, err := (&http.Client{Transport: tpl.RoundTripper(inner)}).Get("https://h.example.com/m/{t}/sse")
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		go func() { _, _ = pw.Write([]byte(": ping\n\n")) }()
		got := make(chan string, 1)
		go func() {
			buf := make([]byte, 64)
			n, _ := resp.Body.Read(buf)
			got <- string(buf[:n])
		}()
		select {
		case s := <-got:
			if s != ": ping\n\n" {
				t.Fatalf("read %q, want the comment", s)
			}
		case <-time.After(3 * time.Second):
			t.Fatal("the comment was held back waiting for the first event")
		}
		go func() {
			_, _ = pw.Write([]byte("event: endpoint\ndata: /m/sv%2F1/x\n\n"))
			_ = pw.Close()
		}()
		rest, _ := io.ReadAll(resp.Body)
		if string(rest) != "event: endpoint\ndata: /m/{t}/x\n\n" {
			t.Fatalf("then read %q", rest)
		}
		if resp.ContentLength != -1 || resp.Header.Get("Content-Length") != "" {
			t.Fatal("a rewritten stream keeps a length it may no longer have")
		}
	})

	t.Run("only a GET's stream from the template's origin", func(t *testing.T) {
		const in = "event: endpoint\ndata: /m/sv%2F1\n\n"
		for _, c := range []struct{ method, url string }{
			{http.MethodPost, "https://h.example.com/m/{t}/sse"},
			{http.MethodGet, "https://other.example.com/m/{t}/sse"},
		} {
			req, _ := http.NewRequest(c.method, c.url, nil)
			resp, err := tpl.RoundTripper(streamer{body: in}).RoundTrip(req)
			if err != nil {
				t.Fatal(err)
			}
			got, _ := io.ReadAll(resp.Body)
			if string(got) != in {
				t.Errorf("%s %s: the stream was changed to %q", c.method, c.url, got)
			}
		}
	})
}

// TestPlacementVersion: a secret pushed as before (no in, name or parts)
// adds nothing to a server's fingerprint, so no existing fingerprint
// changes; a placement adds a digest that changes with
// in, name and each part, a rotated part value included, and holds no
// value.
func TestPlacementVersion(t *testing.T) {
	for _, sec := range []credtemplate.Secret{
		{Type: "bearer", Header: "Authorization", Value: "pv-value-1"},
		{Type: "api_key", Header: "X-Key", ReferenceHeader: "X-Ref", Value: "pv-value-2"},
		{},
	} {
		if v := credtemplate.PlacementVersion(sec); v != "" {
			t.Errorf("a secret with no placement has version %q", v)
		}
	}
	base := credtemplate.Secret{Type: "api_key", In: "header", Name: "DD-API-KEY", Value: "pv-value-1",
		Parts: []credtemplate.SecretPart{{In: "header", Name: "DD-APPLICATION-KEY", Value: "pv-value-2"}}}
	seen := map[string]string{}
	for what, mutate := range map[string]func(*credtemplate.Secret){
		"base": func(*credtemplate.Secret) {},
		"in":   func(s *credtemplate.Secret) { s.In = "query" },
		"name": func(s *credtemplate.Secret) { s.Name = "DD-API-KEY-2" },
		"a part's in": func(s *credtemplate.Secret) {
			s.Parts = []credtemplate.SecretPart{{In: "query", Name: "DD-APPLICATION-KEY", Value: "pv-value-2"}}
		},
		"a part's name": func(s *credtemplate.Secret) {
			s.Parts = []credtemplate.SecretPart{{In: "header", Name: "DD-APP-KEY", Value: "pv-value-2"}}
		},
		"a rotated part": func(s *credtemplate.Secret) {
			s.Parts = []credtemplate.SecretPart{{In: "header", Name: "DD-APPLICATION-KEY", Value: "pv-value-3"}}
		},
		"one more part": func(s *credtemplate.Secret) {
			s.Parts = append(s.Parts, credtemplate.SecretPart{In: "query", Name: "k", Value: "pv-value-4"})
		},
		"a name, no in":    func(s *credtemplate.Secret) { s.In, s.Parts = "", nil },
		"in only, no name": func(s *credtemplate.Secret) { s.Name, s.Parts = "", nil },
	} {
		sec := base
		mutate(&sec)
		v := credtemplate.PlacementVersion(sec)
		if v == "" || strings.Contains(v, "pv-value") {
			t.Fatalf("%s: version %q", what, v)
		}
		if other, dup := seen[v]; dup {
			t.Errorf("%s and %s have the same version", what, other)
		}
		seen[v] = what
	}
}
