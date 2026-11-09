package credtemplate_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// lockedBuffer is a log sink safe for the client's goroutines.
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// captureLogs sends the default slog logger to a text handler at debug for
// the rest of the test.
func captureLogs(t *testing.T) *lockedBuffer {
	t.Helper()
	logs := &lockedBuffer{}
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(old) })
	return logs
}

// TestTemplateNeverLogged: no spelling of a template's secret (as written,
// query-escaped, path-escaped or JSON-escaped) appears in anything that can
// reach a log, a record or a caller: the template, its parts and the pushed
// secret under every fmt verb, slog (text and JSON, at debug) and the
// encoders; the errors Compile returns; the errors and responses a request
// through the template returns, when the inner transport echoes the URL, a
// dial fails, or a same-origin redirect echoes the secret; and the gateway's
// own log during a whole MCP session through the template, failures
// included.
func TestTemplateNeverLogged(t *testing.T) {
	// The four spellings differ: '/', '+', '?', '#', '%', '&', '=', ' ',
	// '"' and '<' are escaped differently in a query, a path and JSON.
	const secret = `lg-secret/+?#%&= "q"<t>`
	query := credtemplate.Secret{Type: "api_key", In: "query", Name: "apiKey", Value: secret}
	pathVar := credtemplate.Secret{Type: "api_key", In: "url_variable", Name: "token", Value: secret}
	header := credtemplate.Secret{Type: "api_key", Header: "X-Api-Key", Value: secret}
	noLeak := func(t *testing.T, what, text string) {
		t.Helper()
		if l := leaks(text, secret); len(l) > 0 {
			t.Fatalf("%s carries %d spelling(s) of the secret", what, len(l))
		}
	}

	t.Run("formatting and encoding", func(t *testing.T) {
		tq := mustTemplate(t, "https://q.example.com/mcp", query)
		tp := mustTemplate(t, "https://p.example.com/mcp/{token}", pathVar)
		th := mustTemplate(t, "https://h.example.com/mcp", header)
		parts, err := credtemplate.Parts(query)
		if err != nil {
			t.Fatal(err)
		}
		part := parts[0]
		multi := credtemplate.Secret{Type: "api_key", In: "header", Name: "DD-API-KEY", Value: secret,
			Parts: []credtemplate.SecretPart{{In: "query", Name: "app", Value: secret}}}
		tm := mustTemplate(t, "https://m.example.com/mcp", multi)
		values := map[string]any{
			"*Template (query)": tq, "*Template (url_variable)": tp, "*Template (header)": th, "*Template (parts)": tm,
			"Template value": *tq, "Part": part, "*Part": &part, "Secret": query, "*Secret": &query,
			"Secret with parts": multi, "SecretPart": multi.Parts[0], "[]SecretPart": multi.Parts,
			"[]Part": []credtemplate.Part{part}, "map": map[string]any{"cred": tq, "secret": query},
			"struct": struct {
				T *credtemplate.Template
				S credtemplate.Secret
			}{tq, multi},
		}
		for what, v := range values {
			for _, verb := range []string{"%v", "%+v", "%#v", "%s", "%q", "%x", "%X", "%d", "%T"} {
				noLeak(t, what+" "+verb, fmt.Sprintf(verb, v))
			}
			raw, _ := json.Marshal(v)
			noLeak(t, what+" json", string(raw))
			var text, js bytes.Buffer
			slog.New(slog.NewTextHandler(&text, &slog.HandlerOptions{Level: slog.LevelDebug})).Debug("cred", "v", v, slog.Any("any", v))
			slog.New(slog.NewJSONHandler(&js, &slog.HandlerOptions{Level: slog.LevelDebug})).Debug("cred", "v", v, slog.Any("any", v))
			noLeak(t, what+" slog text", text.String())
			noLeak(t, what+" slog json", js.String())
		}
		for what, s := range map[string]fmt.Stringer{"template": tq, "part": part, "secret": query, "secret part": multi.Parts[0]} {
			if !strings.Contains(s.String(), "[redacted]") {
				t.Errorf("%s's String() does not mark the value as redacted: %q", what, s.String())
			}
		}
	})

	t.Run("Compile's errors", func(t *testing.T) {
		bad := []struct {
			raw string
			sec credtemplate.Secret
		}{
			{"https://h.example.com/mcp?apiKey=x", query},                                             // the URL carries the parameter
			{"https://h.example.com/mcp", pathVar},                                                    // no {token}
			{"https://h.example.com/mcp/{token}#" + url.PathEscape(secret), pathVar},                  // a fragment
			{"https://h.example.com/mcp", credtemplate.Secret{Type: "api_key", Value: secret + "\n"}}, // a control character
			{"https://h.example.com/mcp", credtemplate.Secret{Type: "api_key", Header: "Content-Type", Value: secret}},
			{"https://h.example.com/mcp", credtemplate.Secret{Type: "api_key", In: "query", Name: "bad name", Value: secret}},
			{"https://h.example.com/mcp", credtemplate.Secret{Type: "api_key", In: "cookie", Name: "c", Value: secret}},
			{"ftp://" + url.PathEscape(secret) + "/x", query},
		}
		for i, b := range bad {
			_, err := credtemplate.ForSecret(b.raw, b.sec)
			if err == nil {
				t.Fatalf("case %d: ForSecret accepted it", i)
			}
			noLeak(t, fmt.Sprintf("case %d's error", i), err.Error())
		}
	})

	t.Run("a request's errors, responses and redirects", func(t *testing.T) {
		// An inner transport that echoes the request URL in its error.
		echoing := roundTripFunc(func(r *http.Request) (*http.Response, error) {
			return nil, fmt.Errorf("dial %s: %w", r.URL.String(), context.DeadlineExceeded)
		})
		for _, c := range []struct {
			raw string
			sec credtemplate.Secret
		}{
			{"https://q.example.com/mcp", query},
			{"https://p.example.com/mcp/{token}", pathVar},
		} {
			tpl := mustTemplate(t, c.raw, c.sec)
			req, _ := http.NewRequest(http.MethodPost, c.raw, nil)
			_, err := (&http.Client{Transport: tpl.RoundTripper(echoing)}).Do(req)
			if err == nil {
				t.Fatal("the echoing transport's error was lost")
			}
			noLeak(t, "an inner transport's error", err.Error())
			if !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("the scrubbed error no longer classifies as a deadline: %T", err)
			}

			// A dial that fails through the SSRF guard: http.Client's own
			// *url.Error names the caller's URL, which keeps the template.
			addr := closedAddr(t)
			u, _ := url.Parse(c.raw)
			u.Scheme, u.Host = "http", addr
			dead := mustTemplate(t, u.String(), c.sec)
			_, err = templated{guard(t), dead}.HTTPClient(0).Post(u.String(), "application/json", strings.NewReader("{}"))
			if err == nil {
				t.Fatal("a dial to a closed port succeeded")
			}
			noLeak(t, "a failed dial's error", err.Error())
		}
	})

	t.Run("a same-origin redirect that echoes the secret", func(t *testing.T) {
		for _, c := range []struct {
			path string
			sec  credtemplate.Secret
			echo func(r *http.Request) string // the Location an echoing server sends
		}{
			{"/mcp", query, func(r *http.Request) string { return "/next?" + r.URL.RawQuery }},
			{"/mcp/{token}", pathVar, func(r *http.Request) string { return r.URL.EscapedPath() + "/next" }},
		} {
			var hops atomic.Int32
			srv, _ := serve(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				hops.Add(1)
				if !strings.HasSuffix(r.URL.Path, "/next") {
					http.Redirect(w, r, c.echo(r), http.StatusTemporaryRedirect)
					return
				}
				// The second hop dies before answering, so the client
				// reports the hop's URL in its error.
				hj, _ := w.(http.Hijacker)
				conn, _, _ := hj.Hijack()
				conn.Close()
			}))
			tpl := mustTemplate(t, srv.URL+c.path, c.sec)
			_, err := templated{guard(t), tpl}.HTTPClient(0).Get(srv.URL + c.path)
			if err == nil || hops.Load() < 2 { // Go retries a GET once on a fresh connection
				t.Fatalf("%s: want the redirect followed to a failing hop; hops %d (at least 2), err %v", c.path, hops.Load(), err != nil)
			}
			noLeak(t, c.path+": the redirected hop's error", err.Error())
		}
	})

	t.Run("an HTTP+SSE endpoint that echoes the secret", func(t *testing.T) {
		logs := captureLogs(t)
		// A server mounted under its secret path names its message endpoint
		// under that path; another echoes the query key into it. The client
		// keeps the template's form, so when the server goes away its errors
		// and logs name no secret.
		prefix := "/" + url.PathEscape(secret)
		for _, c := range []struct {
			name, path string
			sec        credtemplate.Secret
			handler    func(http.Handler) http.Handler
		}{
			{"mounted under the secret", "/{token}/sse", pathVar, func(next http.Handler) http.Handler {
				return mountedAt(prefix, echoEndpoint(func(ep string) string { return prefix + ep }, next))
			}},
			{"the query key echoed", "/sse", query, func(next http.Handler) http.Handler {
				return queryKeyVendor("apiKey", secret, echoEndpoint(func(ep string) string {
					return ep + "&apiKey=" + url.QueryEscape(secret)
				}, next))
			}},
		} {
			srv, rec := serve(t, c.handler(mcpMock(t, "2024-11-05").Handler()))
			tpl := mustTemplate(t, srv.URL+c.path, c.sec)
			cl := connect(t, "sse-"+c.name, srv.URL+c.path, "sse", templated{guard(t), tpl})
			echo(t, cl)
			var posts int
			for _, r := range rec.all() {
				if r.method == http.MethodPost {
					posts++
				}
			}
			if posts == 0 {
				t.Fatalf("%s: no POST reached the server's endpoint", c.name)
			}
			srv.CloseClientConnections()
			srv.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			_, err := cl.CallTool(ctx, "echo", map[string]interface{}{"text": "after"})
			cancel()
			if err == nil {
				t.Fatalf("%s: a call to a closed server succeeded", c.name)
			}
			noLeak(t, c.name+": a call's error after the server went away", err.Error())
		}
		noLeak(t, "the gateway's log", logs.String())
	})

	t.Run("the gateway's log over a whole MCP session", func(t *testing.T) {
		logs := captureLogs(t)
		// Placed right, the session works; placed wrong (the vendor refuses
		// the key) or unreachable, the client logs and returns its errors.
		srv, _ := serve(t, queryKeyVendor("apiKey", secret, mcpMock(t, "2025-06-18").Handler()))
		echo(t, connect(t, "q", srv.URL+"/mcp", "http", templated{guard(t), mustTemplate(t, srv.URL+"/mcp", query)}))

		wrong, _ := serve(t, queryKeyVendor("apiKey", "another-key", mcpMock(t, "2025-06-18").Handler()))
		_, err := tryConnect("q-wrong", wrong.URL+"/mcp", "http", templated{guard(t), mustTemplate(t, wrong.URL+"/mcp", query)})
		if err == nil {
			t.Fatal("the vendor accepted the wrong key")
		}
		noLeak(t, "a refused connect's error", err.Error())

		dead := "http://" + closedAddr(t) + "/mcp/{token}"
		_, err = tryConnect("p-dead", dead, "http", templated{guard(t), mustTemplate(t, dead, pathVar)})
		if err == nil {
			t.Fatal("a closed port connected")
		}
		noLeak(t, "an unreachable connect's error", err.Error())
		noLeak(t, "the gateway's log", logs.String())
	})
}

// mountedAt serves next under the escaped path prefix, as a server mounted
// there: any other path answers 404.
func mountedAt(prefix string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rest, ok := strings.CutPrefix(r.URL.EscapedPath(), prefix)
		if !ok {
			http.NotFound(w, r)
			return
		}
		r2 := r.Clone(r.Context())
		r2.URL.Path, r2.URL.RawPath = rest, ""
		next.ServeHTTP(w, r2)
	})
}

// echoEndpoint rewrites the endpoint an HTTP+SSE stream names, as a server
// that builds it from where it is mounted or from the request's query.
func echoEndpoint(rewrite func(endpoint string) string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		next.ServeHTTP(&endpointWriter{ResponseWriter: w, rewrite: rewrite}, r)
	})
}

type endpointWriter struct {
	http.ResponseWriter
	rewrite func(string) string
	done    bool
}

func (e *endpointWriter) Write(p []byte) (int, error) {
	out := p
	if !e.done {
		if i := bytes.Index(p, []byte("data: /messages")); i >= 0 && bytes.IndexByte(p[i:], '\n') > 0 {
			end := i + bytes.IndexByte(p[i:], '\n')
			ep := string(p[i+len("data: ") : end])
			out = append(append(append([]byte(nil), p[:i]...), "data: "+e.rewrite(ep)...), p[end:]...)
			e.done = true
		}
	}
	if _, err := e.ResponseWriter.Write(out); err != nil {
		return 0, err
	}
	return len(p), nil
}

func (e *endpointWriter) Flush() {
	if f, ok := e.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// roundTripFunc is an http.RoundTripper made of a function.
type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
