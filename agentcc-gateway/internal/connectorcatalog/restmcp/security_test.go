package restmcp

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// closedOrigin is a loopback base URL nothing listens on.
func closedOrigin(t *testing.T) string {
	t.Helper()
	return "http://" + refusedAddr(t)
}

// captureDefaultLog sends the process-wide slog default (where the egress
// guard logs its refusals) to a buffer for the rest of the test.
func captureDefaultLog(t *testing.T) *logBuffer {
	t.Helper()
	buf := &logBuffer{}
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewJSONHandler(buf, &slog.HandlerOptions{Level: slog.LevelDebug})))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return buf
}

// withOrigin points every tool of p at origin and adds its host to the
// app's vendor domains.
func withOrigin(t *testing.T, a *manifest.App, p *manifest.Provider, origin string) {
	t.Helper()
	u, err := url.Parse(origin)
	if err != nil {
		t.Fatal(err)
	}
	a.Vendor.Domains = []string{u.Hostname()}
	for i := range p.Tools.Items {
		editSchema(t, &p.Tools.Items[i], func(m map[string]any) { m["x-origin"] = origin })
	}
}

// TestTokenNeverLogged: on every path a call can take (a vendor answer, a
// vendor error that quotes the credential back, a 2xx that echoes it, a
// redirect whose Location carries it, an answer over the read limit, bad
// arguments, an unreachable vendor, a refused address, a malformed bearer,
// and an api_key app's key), neither the runtime's log, nor the process
// log the egress guard writes to, nor any answer the caller gets holds a
// credential value. The log still says what happened on each request.
func TestTokenNeverLogged(t *testing.T) {
	processLog := captureDefaultLog(t)
	echo := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		auth := r.Header.Get("Authorization")
		switch r.URL.Path {
		case "/gmail/v1/users/me/profile":
			jsonAnswer(200, map[string]any{"emailAddress": "someone@example.test", "note": "you sent " + auth})(w, r)
		case "/gmail/v1/users/me/labels":
			jsonAnswer(401, map[string]any{"error": map[string]any{"code": 401, "status": "UNAUTHENTICATED",
				"message": "Request had invalid authentication credentials: " + auth}})(w, r)
		case "/gmail/v1/users/me/threads/redirected":
			w.Header().Set("Location", "https://elsewhere.example/?access_token="+strings.TrimPrefix(auth, "Bearer "))
			w.WriteHeader(http.StatusFound)
		case "/gmail/v1/users/me/threads/huge":
			jsonAnswer(200, map[string]any{"id": "huge", "pad": strings.Repeat("x", 5000), "auth": auth})(w, r)
		default:
			jsonAnswer(404, map[string]any{"error": map[string]any{"code": 404, "message": "Not Found " + auth}})(w, r)
		}
	})
	gmail := compileOne(t, cn4Catalog(t), "gmail")
	rt := startRuntime(t, []*App{gmail}, map[string]string{gmailOrigin: echo.URL},
		func(o *Options) { o.MaxVendorBytes = 4096 })

	var answers []string
	call := func(tool string, args any, token string) callResult {
		params, _ := json.Marshal(map[string]any{"name": tool, "arguments": args})
		r := rt.legacyCall(t, "/gmail/mcp", token, "tools/call", string(params))
		answers = append(answers, r.Raw)
		return decodeCall(t, r)
	}
	if res := call("get_profile", map[string]any{}, testToken); res.IsError || !strings.Contains(res.text(), "[redacted]") {
		t.Errorf("a 2xx echoing the bearer: isError %v, %q; want the value replaced with [redacted]", res.IsError, res.text())
	}
	if res := call("list_labels", map[string]any{}, testToken); !res.IsError || !strings.Contains(res.text(), "[redacted]") {
		t.Errorf("a 401 quoting the bearer: isError %v, %q; want an isError with the value replaced", res.IsError, res.text())
	}
	if res := call("get_thread", map[string]any{"id": "redirected"}, testToken); !res.IsError || strings.Contains(res.text(), "elsewhere") {
		t.Errorf("a redirect: isError %v, %q; want an isError that does not repeat the Location", res.IsError, res.text())
	}
	if res := call("get_thread", map[string]any{"id": "huge"}, testToken); !res.IsError || !strings.Contains(res.text(), "more than 4096 bytes") {
		t.Errorf("an answer over the read limit: %q", res.text())
	}
	if res := call("get_thread", map[string]any{"id": "x", "token": testToken}, testToken); !res.IsError {
		t.Errorf("an unknown argument carrying the token was not refused: %q", res.text())
	}
	if res := call("get_thread", map[string]any{"id": "absent"}, otherToken); !res.IsError {
		t.Errorf("a 404 quoting the second user's bearer: %q", res.text())
	}
	for _, auth := range []string{"Bearer " + testToken + " trailing", "Basic " + testToken, "bearer\t" + testToken} {
		r := rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`, "MCP-Protocol-Version", "2025-11-25",
			"Authorization", auth)
		answers = append(answers, r.Raw+r.Header.Get("WWW-Authenticate"))
		if r.Status != http.StatusUnauthorized {
			t.Errorf("Authorization %q: HTTP %d, want 401", strings.Replace(auth, testToken, "<token>", 1), r.Status)
		}
	}
	// A client that sends its token as a method name: the log names the
	// method "other" (the error answer to that same client may repeat it).
	rt.post(t, "/gmail/mcp", `{"jsonrpc":"2.0","id":1,"method":"`+testToken+`"}`, "MCP-Protocol-Version", "2025-11-25",
		"Authorization", "Bearer "+testToken)

	// An unreachable vendor, and an address the egress guard refuses.
	down := startRuntime(t, []*App{gmail}, map[string]string{gmailOrigin: closedOrigin(t)})
	params := `{"name":"get_profile","arguments":{}}`
	r := down.legacyCall(t, "/gmail/mcp", testToken, "tools/call", params)
	answers = append(answers, r.Raw)
	if res := decodeCall(t, r); !res.IsError || !strings.Contains(res.text(), "could not be reached") {
		t.Errorf("an unreachable vendor: %q", res.text())
	}
	a, p := gmailCopy(t)
	withOrigin(t, a, p, "https://localhost:1")
	local, err := Compile(&manifest.Catalog{Apps: []*manifest.App{a}})
	if err != nil {
		t.Fatal(err)
	}
	refused := startRuntime(t, local, nil)
	r = refused.legacyCall(t, "/gmail/mcp", testToken, "tools/call", params)
	answers = append(answers, r.Raw)
	if res := decodeCall(t, r); !res.IsError || !strings.Contains(res.text(), "refused") {
		t.Errorf("a loopback vendor address: %q", res.text())
	}

	// An api_key app: its key arrives in X-API-Key and the vendor quotes it.
	keyEcho := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		jsonAnswer(403, map[string]any{"status": 403, "errorCode": "00_403", "message": "Forbidden for key " +
			r.Header.Get("X-API-Key")})(w, r)
	})
	adyen := startRuntime(t, []*App{compileOne(t, cn4Catalog(t), "adyen")}, map[string]string{adyenOrigin: keyEcho.URL})
	r = adyen.post(t, "/adyen/mcp", `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_transfer","arguments":{"id":"tr-1"}}}`,
		"MCP-Protocol-Version", "2025-11-25", "X-API-Key", testAPIKey)
	answers = append(answers, r.Raw)
	if res := decodeCall(t, r); !res.IsError || !strings.Contains(res.text(), "[redacted]") {
		t.Errorf("a 403 quoting the api key: %q", res.text())
	}

	if n := len(echo.requests()) + len(keyEcho.requests()); n != 6 {
		t.Errorf("the vendors got %d requests, want 6 (5 gmail calls and 1 adyen call; the bad-argument call sends nothing)", n)
	}

	// Answers the runtime cuts, a result over response.maxBytes and a long
	// vendor message, echoing the credential right where the cut falls: no
	// part of it survives. One answer is JSON that spells the token's / as
	// \/ (PHP's json_encode does), so the echo is not the token byte for byte.
	const cutMax = 1000
	cutApp := compileOne(t, cn4Catalog(t), "gmail")
	cutApp.byName["get_thread"].maxBytes, cutApp.byName["get_profile"].maxBytes = cutMax, cutMax
	// cutAt is where capText cuts a text of size bytes: the token is placed
	// to start 20 bytes before it.
	cutAt := func(size int) int { return strings.Index(capText(strings.Repeat("a", size), cutMax), "\n[truncated") }
	around := func(size, start int, secret string) string {
		return strings.Repeat("a", start) + secret + strings.Repeat("a", size-start-len(secret))
	}
	const size = 3000
	cutFake := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		switch r.URL.Path {
		case "/gmail/v1/users/me/threads/plain": // a text answer, cut as it is
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			_, _ = w.Write([]byte(around(size, cutAt(size)-20, token)))
		case "/gmail/v1/users/me/profile": // JSON, re-encoded as {"note":"..."} and then cut
			note := around(size-len(`{"note":""}`), cutAt(size)-20-len(`{"note":"`), token)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"note":"` + strings.ReplaceAll(note, "/", `\/`) + `"}`))
		case "/gmail/v1/users/me/labels": // the message is cut at maxErrorText
			jsonAnswer(400, map[string]any{"error": map[string]any{"message": around(maxErrorText+100, maxErrorText-20, token)}})(w, r)
		}
	})
	cut := startRuntime(t, []*App{cutApp}, map[string]string{gmailOrigin: cutFake.URL})
	for _, c := range []struct {
		tool, token string
		args        map[string]any
		isError     bool
	}{
		{"get_thread", testToken, map[string]any{"id": "plain"}, false},
		{"get_profile", slashToken, map[string]any{}, false},
		{"list_labels", testToken, map[string]any{}, true},
	} {
		params, _ := json.Marshal(map[string]any{"name": c.tool, "arguments": c.args})
		r := cut.legacyCall(t, "/gmail/mcp", c.token, "tools/call", string(params))
		answers = append(answers, r.Raw)
		res := decodeCall(t, r)
		if res.IsError != c.isError || !strings.Contains(res.text(), "[redacted]") || !strings.Contains(res.text(), "truncated") &&
			!strings.Contains(res.text(), "...") {
			t.Errorf("%s, cut with the echoed token at the cut: isError %v, %d bytes; want the token redacted and the text cut",
				c.tool, res.IsError, len(res.text()))
		}
		if strings.Contains(res.text(), c.token[:8]) {
			t.Errorf("%s: the cut kept part of the credential", c.tool)
		}
	}

	logs := rt.logs.String() + down.logs.String() + refused.logs.String() + adyen.logs.String() + cut.logs.String()
	secrets := []string{testToken, otherToken, testAPIKey, slashToken, strings.TrimPrefix(testToken, "ya29."),
		strings.ReplaceAll(slashToken, "/", `\/`)}
	for name, text := range map[string]string{"the runtime log": logs, "the process log": processLog.String(),
		"the answers": strings.Join(answers, "\n")} {
		for _, secret := range secrets {
			if strings.Contains(text, secret) || strings.Contains(text, secret[:8]) {
				t.Errorf("%s holds a credential value or part of one", name)
			}
		}
	}
	for _, outcome := range []string{`"outcome":"ok"`, `"outcome":"vendor_error"`, `"outcome":"too_large"`,
		`"outcome":"invalid_arguments"`, `"outcome":"unauthenticated"`, `"outcome":"transport_error"`,
		`"outcome":"egress_refused"`, `"method":"other"`} {
		if !strings.Contains(logs, outcome) {
			t.Errorf("the runtime log has no line with %s:\n%s", outcome, logs)
		}
	}
	if !strings.Contains(processLog.String(), "mcp egress refused a connection") {
		t.Errorf("the egress guard's refusal was not logged where this test looked: %s", processLog.String())
	}
}

// TestTokenSentOnlyToSpecHost: each call's credential goes to its own tool's
// origin and nowhere else. Three apps on one runtime each reach only their
// own vendor, with only their own credential; no inbound header but the
// credential crosses; a vendor redirect to another host is not followed, so
// the host it names gets nothing; a refused call sends nothing.
func TestTokenSentOnlyToSpecHost(t *testing.T) {
	stranger := newVendorFake(t, jsonAnswer(200, map[string]any{"stolen": true}))
	gmailFake := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/gmail/v1/users/me/threads/moved":
			w.Header().Set("Location", stranger.URL+"/collect")
			w.WriteHeader(http.StatusFound)
		case "/gmail/v1/users/me/threads/temp":
			w.Header().Set("Location", stranger.URL+"/collect")
			w.WriteHeader(http.StatusTemporaryRedirect)
		default:
			jsonAnswer(200, map[string]any{"emailAddress": "someone@example.test"})(w, r)
		}
	})
	graphFake := newVendorFake(t, jsonAnswer(200, map[string]any{"value": []any{}}))
	adyenFake := newVendorFake(t, jsonAnswer(200, map[string]any{"data": []any{}}))
	cat := cn4Catalog(t)
	apps := []*App{compileOne(t, cat, "gmail"), compileOne(t, cat, "microsoft-outlook"), compileOne(t, cat, "adyen")}
	// The stranger is replayed too, so the egress guard would let a
	// followed redirect reach it: only the no-redirect rule keeps it out.
	rt := startRuntime(t, apps, map[string]string{gmailOrigin: gmailFake.URL, "https://graph.microsoft.com": graphFake.URL,
		adyenOrigin: adyenFake.URL, "https://stranger.example": stranger.URL})

	inbound := []string{"MCP-Protocol-Version", "2025-11-25", "Cookie", "session=abc", "X-Forwarded-For", "203.0.113.9",
		"Mcp-Session-Id", "s-1", "X-Agentcc-Org", "org-1"}
	callBody := func(tool, args string) string {
		return `{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"` + tool + `","arguments":` + args + `}}`
	}
	post := func(path, body string, extra ...string) callResult {
		return decodeCall(t, rt.post(t, path, body, append(append([]string{}, inbound...), extra...)...))
	}
	post("/gmail/mcp", callBody("get_profile", `{}`), "Authorization", "Bearer "+testToken)
	post("/microsoft-outlook/mcp", callBody("list_mail_folders", `{}`), "Authorization", "Bearer "+otherToken)
	post("/adyen/mcp", callBody("list_transfers", `{}`), "X-API-Key", testAPIKey, "Authorization", "Bearer "+testToken)

	check := func(name string, f *vendorFake, want map[string]string) {
		t.Helper()
		got := f.requests()
		if len(got) != 1 {
			t.Fatalf("%s got %d requests, want 1", name, len(got))
		}
		h := got[0].Header
		for k, v := range want {
			if h.Get(k) != v {
				t.Errorf("%s got %s %q, want this call's own credential", name, k, h.Get(k))
			}
		}
		for _, k := range []string{"Cookie", "X-Forwarded-For", "Mcp-Session-Id", "Mcp-Protocol-Version", "X-Agentcc-Org"} {
			if h.Get(k) != "" {
				t.Errorf("%s got the inbound %s header", name, k)
			}
		}
		for k := range want {
			h.Del(k)
		}
		for k, vs := range h {
			for _, v := range vs {
				for _, secret := range []string{testToken, otherToken, testAPIKey} {
					if strings.Contains(v, secret) {
						t.Errorf("%s got another call's credential in %s", name, k)
					}
				}
			}
		}
		if strings.Contains(got[0].Query, "cn10-fake") || bytes.Contains(got[0].Body, []byte("cn10-fake")) {
			t.Errorf("%s got a credential in its query or body", name)
		}
	}
	check("gmail", gmailFake, map[string]string{"Authorization": "Bearer " + testToken})
	check("graph", graphFake, map[string]string{"Authorization": "Bearer " + otherToken})
	check("adyen", adyenFake, map[string]string{"X-API-Key": testAPIKey, "Authorization": ""})

	for _, id := range []string{"moved", "temp"} {
		res := post("/gmail/mcp", callBody("get_thread", `{"id":"`+id+`"}`), "Authorization", "Bearer "+testToken)
		if !res.IsError || !strings.Contains(res.text(), "follows no redirect") {
			t.Errorf("a vendor redirect (%s): isError %v, %q", id, res.IsError, res.text())
		}
	}
	if res := post("/gmail/mcp", callBody("get_thread", `{"id":"x","q":"extra"}`), "Authorization", "Bearer "+testToken); !res.IsError {
		t.Errorf("an unknown argument was sent: %q", res.text())
	}
	if n := len(stranger.requests()); n != 0 {
		t.Errorf("the host a redirect named got %d requests", n)
	}
	if n := len(gmailFake.requests()); n != 3 {
		t.Errorf("gmail got %d requests, want 3 (a refused call sends nothing)", n)
	}
	if len(graphFake.requests())+len(adyenFake.requests()) != 2 {
		t.Errorf("another app's vendor got a gmail call")
	}
}

// TestPrivateAndMetadataRefused: the runtime's vendor client is the probe step's
// guarded one: it refuses a metadata address always, a private or loopback
// address unless the operator replays a vendor there, and never contacts
// the address it refuses. Through tools/call, a vendor host name that
// resolves to loopback gets an isError naming the refusal, and the server
// there gets nothing. An operator replay target off loopback, and a tool
// whose origin is an IP address, are refused before anything is served.
func TestPrivateAndMetadataRefused(t *testing.T) {
	tlsFake := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("the guarded client reached a refused address")
		w.WriteHeader(http.StatusTeapot)
	}))
	defer tlsFake.Close()
	roots := x509.NewCertPool()
	roots.AddCert(tlsFake.Certificate())
	srv, err := New(nil, Options{})
	if err != nil {
		t.Fatal(err)
	}
	_, port, _ := net.SplitHostPort(strings.TrimPrefix(tlsFake.URL, "https://"))
	for target, want := range map[string]egress.Reason{
		"https://169.254.169.254/latest/meta-data/":         egress.ReasonForbidden,
		"http://169.254.169.254/computeMetadata/v1/":        egress.ReasonForbidden,
		"https://[fe80::1]/":                                egress.ReasonForbidden,
		"https://10.1.2.3/":                                 egress.ReasonNotAllowed,
		"http://192.168.1.10:8080/":                         egress.ReasonNotAllowed,
		"https://[fd00::1]/":                                egress.ReasonNotAllowed,
		"https://127.0.0.1:" + port + "/":                   egress.ReasonNotAllowed,
		"https://localhost:" + port + "/gmail/v1/users/me/": egress.ReasonNotAllowed,
	} {
		resp, err := srv.vendor.http.Get(target)
		if resp != nil {
			resp.Body.Close()
		}
		var refused *egress.RefusedError
		if !errors.As(err, &refused) || refused.Reason != want {
			t.Errorf("GET %s: %v, want an egress refusal (%s)", target, err, want)
		}
	}

	// The full path: a vendor whose host name resolves to loopback.
	a, p := gmailCopy(t)
	withOrigin(t, a, p, "https://localhost:"+port)
	apps, err := Compile(&manifest.Catalog{Apps: []*manifest.App{a}})
	if err != nil {
		t.Fatal(err)
	}
	rt := startRuntime(t, apps, nil, func(o *Options) {
		o.TLSConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	})
	res := rt.callTool(t, "/gmail/mcp", testToken, "get_profile", map[string]any{})
	if !res.IsError || !strings.Contains(res.text(), "the call to localhost was refused: egress refused: a loopback address") {
		t.Errorf("a loopback vendor: isError %v, %q", res.IsError, res.text())
	}
	if !strings.Contains(rt.logs.String(), `"outcome":"egress_refused"`) {
		t.Errorf("the refusal was not logged as egress_refused: %s", rt.logs.String())
	}

	for _, target := range []string{"http://10.0.0.5:8080", "http://169.254.169.254:80", "http://localhost:8080",
		"http://127.0.0.1", "https://example.com:443", "http://127.0.0.1:9/path"} {
		if _, err := New(nil, Options{Replay: map[string]string{gmailOrigin: target}}); err == nil {
			t.Errorf("a replay target %s was accepted", target)
		}
	}
	if _, err := New(nil, Options{Replay: map[string]string{"http://gmail.googleapis.com": "http://127.0.0.1:9"}}); err == nil {
		t.Error("a plaintext replay origin was accepted")
	}
	for _, origin := range []string{"https://169.254.169.254", "https://10.0.0.1", "https://[::1]"} {
		a, p := gmailCopy(t)
		withOrigin(t, a, p, origin)
		if _, err := Compile(&manifest.Catalog{Apps: []*manifest.App{a}}); err == nil || !strings.Contains(err.Error(), "is an IP address") {
			t.Errorf("a tool origin %s: %v, want it refused as an IP address", origin, err)
		}
	}
}
