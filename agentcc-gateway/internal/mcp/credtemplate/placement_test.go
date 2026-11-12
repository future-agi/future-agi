package credtemplate_test

import (
	"net/http"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// TestQueryKeyPlaced: a pushed secret with in: query (Browserbase's
// browserbaseApiKey) reaches the upstream as that query parameter, escaped,
// exactly once, on every request of a real MCP session over either HTTP
// transport, and nowhere else: not in a header, not in the URL the caller
// built, and not on another origin.
func TestQueryKeyPlaced(t *testing.T) {
	const name = "browserbaseApiKey"
	// Characters a query value must escape: '/', '+', '=', '&', '?', '#',
	// '%' and a space.
	const secret = "bb-key/7Hx+=&?#%9 z"
	sec := credtemplate.Secret{Type: "api_key", In: "query", Name: name, Value: secret}

	checkEvery := func(t *testing.T, reqs []seen) {
		t.Helper()
		if len(reqs) == 0 {
			t.Fatal("the upstream received nothing")
		}
		for _, r := range reqs {
			q, err := url.ParseQuery(r.rawQuery)
			if err != nil {
				t.Fatalf("%s %s: query does not parse: %v", r.method, r.path, err)
			}
			if got := q[name]; len(got) != 1 || got[0] != secret {
				t.Fatalf("%s %s: %d %s values, want exactly the key once", r.method, r.path, len(q[name]), name)
			}
			if hs := headerCarrying(r.header, secret); len(hs) > 0 {
				t.Fatalf("%s %s: the key is also in headers %v", r.method, r.path, hs)
			}
			if r.header.Get("Authorization") != "" {
				t.Fatalf("%s %s: an Authorization header was sent", r.method, r.path)
			}
		}
	}

	t.Run("every request of an MCP session over Streamable HTTP", func(t *testing.T) {
		srv, rec := serve(t, queryKeyVendor(name, secret, mcpMock(t, "2025-06-18").Handler()))
		tpl := mustTemplate(t, srv.URL+"/mcp", sec)
		c := connect(t, "browserbase", srv.URL+"/mcp", "http", templated{guard(t), tpl})
		echo(t, c)
		checkEvery(t, rec.all())
	})

	t.Run("every request of an MCP session over 2024-11-05 HTTP+SSE", func(t *testing.T) {
		srv, rec := serve(t, queryKeyVendor(name, secret, mcpMock(t, "2024-11-05").Handler()))
		tpl := mustTemplate(t, srv.URL+"/sse", sec)
		c := connect(t, "browserbase-sse", srv.URL+"/sse", "sse", templated{guard(t), tpl})
		echo(t, c)
		reqs := rec.all()
		checkEvery(t, reqs)
		var stream, posts int
		for _, r := range reqs {
			switch {
			case r.method == http.MethodGet && r.path == "/sse":
				stream++
			case r.method == http.MethodPost && r.path == "/messages":
				posts++
			}
		}
		if stream == 0 || posts == 0 {
			t.Fatalf("want the stream and the endpoint's POSTs keyed; saw %d GET /sse and %d POST /messages", stream, posts)
		}
	})

	t.Run("the rest of the query is kept byte for byte, the key appended", func(t *testing.T) {
		srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		const rest = "region=eu-west&x=%7E%20y&flag"
		tpl := mustTemplate(t, srv.URL+"/mcp?"+rest, sec)
		get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/mcp?"+rest)
		want := rest + "&" + name + "=" + url.QueryEscape(secret)
		if got := rec.all()[0].rawQuery; got != want {
			t.Fatalf("the upstream's query differs from the configured one plus the key (got %d bytes, want %d)", len(got), len(want))
		}
	})

	t.Run("a key already in a request URL is replaced, not repeated", func(t *testing.T) {
		srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		tpl := mustTemplate(t, srv.URL+"/mcp", sec)
		// An HTTP+SSE endpoint or a same-origin redirect can name the
		// parameter itself.
		get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/messages?"+name+"=stale&a=1&"+name+"=other")
		r := rec.all()[0]
		if !strings.HasPrefix(r.rawQuery, "a=1&") {
			t.Fatalf("the other parameter was not kept in place")
		}
		checkEvery(t, []seen{r})
	})

	t.Run("the caller's request and the response's request keep no key", func(t *testing.T) {
		srv, _ := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		tpl := mustTemplate(t, srv.URL+"/mcp", sec)
		req, resp := get(t, tpl.RoundTripper(http.DefaultTransport), srv.URL+"/mcp")
		for what, u := range map[string]*url.URL{"the caller's request": req.URL, "resp.Request": resp.Request.URL} {
			if l := leaks(u.String(), secret); len(l) > 0 || strings.Contains(u.RawQuery, name) {
				t.Fatalf("%s carries the key: %d forms, query names it: %v", what, len(l), strings.Contains(u.RawQuery, name))
			}
		}
	})

	t.Run("another origin gets no key", func(t *testing.T) {
		srv, _ := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		other, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		tpl := mustTemplate(t, srv.URL+"/mcp", sec)
		get(t, tpl.RoundTripper(http.DefaultTransport), other.URL+"/mcp")
		if r := rec.all()[0]; strings.Contains(r.rawQuery, name) || len(leaks(r.rawQuery, secret)) > 0 {
			t.Fatalf("a request to another origin carried the key")
		}
	})
}

// TestSecretURLVariableRendered: a pushed secret with in: url_variable (a
// registry server.json remote's isSecret variable) fills each {name} of the
// server URL's path or query, escaped for where it stands, on every request
// of a real MCP session; it never sets the origin, the caller's URL keeps
// the placeholder, and a URL it cannot fill is refused when compiled.
func TestSecretURLVariableRendered(t *testing.T) {
	// '/', '?', '#', '%', ' ', '+', '&' and '=' must not change the URL's
	// structure: the value stays one path segment or one query value.
	const secret = "hk/7?x#y%z w+&=.."
	sec := credtemplate.Secret{Type: "api_key", In: "url_variable", Name: "token", Value: secret}

	t.Run("one path segment on every request of an MCP session", func(t *testing.T) {
		srv, rec := serve(t, pathKeyVendor(secret, mcpMock(t, "2025-06-18").Handler()))
		tpl := mustTemplate(t, srv.URL+"/mcp/{token}", sec)
		c := connect(t, "hooks", srv.URL+"/mcp/{token}", "http", templated{guard(t), tpl})
		echo(t, c)
		reqs := rec.all()
		if len(reqs) == 0 {
			t.Fatal("the upstream received nothing")
		}
		for _, r := range reqs {
			if r.path != "/mcp/"+url.PathEscape(secret) {
				t.Fatalf("%s reached path %q; want the secret as the one segment after /mcp/", r.method, r.path)
			}
			if r.rawQuery != "" || len(headerCarrying(r.header, secret)) > 0 {
				t.Fatalf("%s: the secret is also in the query or a header", r.method)
			}
		}
	})

	t.Run("an HTTP+SSE server mounted under the secret", func(t *testing.T) {
		// The stream is at /<secret>/sse and the server names its endpoint
		// under the same path; the client keeps /{token}/messages and the
		// template fills it on each POST.
		prefix := "/" + url.PathEscape(secret)
		srv, rec := serve(t, mountedAt(prefix, echoEndpoint(func(ep string) string { return prefix + ep }, mcpMock(t, "2024-11-05").Handler())))
		tpl := mustTemplate(t, srv.URL+"/{token}/sse", sec)
		echo(t, connect(t, "hooks-sse", srv.URL+"/{token}/sse", "sse", templated{guard(t), tpl}))
		var stream, posts int
		for _, r := range rec.all() {
			switch {
			case r.method == http.MethodGet && r.path == prefix+"/sse":
				stream++
			case r.method == http.MethodPost && r.path == prefix+"/messages":
				posts++
			default:
				t.Fatalf("%s reached %q; want every request under the secret path", r.method, r.path)
			}
		}
		if stream == 0 || posts == 0 {
			t.Fatalf("saw %d streams and %d POSTs under the secret path", stream, posts)
		}
	})

	t.Run("each spelling of the placeholder, in the path and the query", func(t *testing.T) {
		for _, tc := range []struct{ path, query, wantPath, wantQuery string }{
			{"/mcp/{token}", "", "/mcp/" + url.PathEscape(secret), ""},
			{"/mcp/%7Btoken%7D", "", "/mcp/" + url.PathEscape(secret), ""},
			{"/mcp/%7btoken%7d/x", "", "/mcp/" + url.PathEscape(secret) + "/x", ""},
			{"/mcp", "session={token}&x=1", "/mcp", "session=" + url.QueryEscape(secret) + "&x=1"},
			{"/mcp", "session=%7Btoken%7D", "/mcp", "session=" + url.QueryEscape(secret)},
			{"/a/pre-{token}-post/b", "k={token}", "/a/pre-" + url.PathEscape(secret) + "-post/b", "k=" + url.QueryEscape(secret)},
		} {
			srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
			raw := srv.URL + tc.path
			if tc.query != "" {
				raw += "?" + tc.query
			}
			tpl := mustTemplate(t, raw, sec)
			req, resp := get(t, tpl.RoundTripper(http.DefaultTransport), raw)
			r := rec.all()[0]
			if r.path != tc.wantPath || r.rawQuery != tc.wantQuery {
				t.Errorf("%s?%s: the upstream saw a different path or query than the rendered template", tc.path, tc.query)
			}
			for what, u := range map[string]*url.URL{"the caller's request": req.URL, "resp.Request": resp.Request.URL} {
				if len(leaks(u.String(), secret)) > 0 {
					t.Errorf("%s?%s: %s carries the secret", tc.path, tc.query, what)
				}
			}
		}
	})

	t.Run("never the origin, and only a URL it can fill", func(t *testing.T) {
		for _, raw := range []string{
			"https://{token}.example.com/mcp",       // the host
			"https://{token}@h.example.com/mcp",     // user info
			"https://h.example.com:{token}/mcp",     // the port
			"https://h.example.com/mcp#{token}",     // the fragment is never sent
			"https://h.example.com/mcp",             // no {token} to fill
			"https://h.example.com/mcp/{token}/{x}", // {x} is filled by nothing
		} {
			if _, err := credtemplate.ForSecret(raw, sec); err == nil {
				t.Errorf("ForSecret accepted %s", raw)
			}
		}
		for _, v := range []string{".", ".."} {
			bad := sec
			bad.Value = v
			if _, err := credtemplate.ForSecret("https://h.example.com/mcp/{token}", bad); err == nil {
				t.Errorf("ForSecret accepted the dot segment %q as a path value", v)
			}
		}
	})
}
