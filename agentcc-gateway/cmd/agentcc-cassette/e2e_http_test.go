package main

import (
	"compress/gzip"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

const (
	gmailMsg1   = "18c2f0a1b2c3d4e5"
	gmailMsg2   = "28d3a1b2c3d4e5f6"
	gmailSent   = "38e4b2c3d4e5f6a7"
	gmailCursor = "PAGE2CURSOR0042"
)

// gmailShaped is a REST server shaped like Gmail's: a bearer, a paginated
// list, a message with Base64url text holding emails, a send, a gzip answer
// and a cookie.
func gmailShaped(t *testing.T, token string) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var hits atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		if r.Header.Get("Authorization") != "Bearer "+token {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=UTF-8")
		switch {
		case r.Method == "GET" && r.URL.Path == "/gmail/v1/users/me/messages" && r.URL.Query().Get("pageToken") == "":
			http.SetCookie(w, &http.Cookie{Name: "GAPS", Value: tokenShaped("cookie"), Path: "/", HttpOnly: true})
			writeGzip(t, w, r, map[string]any{"messages": []map[string]string{{"id": gmailMsg1, "threadId": gmailMsg1}},
				"nextPageToken": gmailCursor, "resultSizeEstimate": 2})
		case r.Method == "GET" && r.URL.Path == "/gmail/v1/users/me/messages":
			_ = json.NewEncoder(w).Encode(map[string]any{"messages": []map[string]string{{"id": gmailMsg2, "threadId": gmailMsg2}}})
		case r.Method == "GET" && strings.HasPrefix(r.URL.Path, "/gmail/v1/users/me/messages/"):
			id := strings.TrimPrefix(r.URL.Path, "/gmail/v1/users/me/messages/")
			body := base64.URLEncoding.EncodeToString([]byte("Hi " + vendorBob + ", the thread is " + id + ". Reply to " + vendorAlice + "."))
			_ = json.NewEncoder(w).Encode(map[string]any{"id": id, "threadId": id, "payload": map[string]any{
				"headers": []map[string]string{{"name": "From", "value": "Alice <" + vendorAlice + ">"}},
				"body":    map[string]any{"size": 64, "data": body}}})
		case r.Method == "POST" && r.URL.Path == "/gmail/v1/users/me/messages/send":
			_, _ = io.Copy(io.Discard, r.Body)
			_ = json.NewEncoder(w).Encode(map[string]any{"id": gmailSent, "labelIds": []string{"SENT"}})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &hits
}

func writeGzip(t *testing.T, w http.ResponseWriter, r *http.Request, v any) {
	if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		_ = json.NewEncoder(w).Encode(v)
		return
	}
	w.Header().Set("Content-Encoding", "gzip")
	gz := gzip.NewWriter(w)
	_ = json.NewEncoder(gz).Encode(v)
	_ = gz.Close()
}

// send makes one request with the bearer and returns the status and body.
func send(t *testing.T, method, url, bearer, body string) (int, string, http.Header) {
	t.Helper()
	req, err := http.NewRequest(method, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(data), resp.Header
}

// TestRecordReplayHTTP records a Gmail-shaped REST API through the CLI's
// loopback proxy (bearer, pagination cursor, Base64url text with emails,
// gzip, a cookie), checks the cassette holds no credential, cookie, email
// or id, then replays it through the CLI using only cassette data: the same
// flow comes back exactly, with any live bearer, and the API is not called.
func TestRecordReplayHTTP(t *testing.T) {
	token := tokenShaped("gmail-bearer")
	api, hits := gmailShaped(t, token)
	dir := t.TempDir()
	out, addrFile := filepath.Join(dir, "gmail.cassette.json"), filepath.Join(dir, "addr")
	rec := startCLI(t, "record", "--kind", "http", "--test-account", "ci-gmail", "--upstream", api.URL,
		"--listen", "127.0.0.1:0", "--addr-file", addrFile, "--out", out)
	proxy := "http://" + waitFile(t, addrFile, rec)
	query := "/gmail/v1/users/me/messages?q=" + strings.ReplaceAll("from:"+vendorAlice, "@", "%40") + "&maxResults=1"
	recorded := map[string]string{}
	for _, step := range []struct{ name, method, path, body string }{
		{"list", "GET", query, ""},
		{"page2", "GET", query + "&pageToken=" + gmailCursor, ""},
		{"get", "GET", "/gmail/v1/users/me/messages/" + gmailMsg1 + "?format=full", ""},
		{"send", "POST", "/gmail/v1/users/me/messages/send",
			`{"raw":"` + base64.URLEncoding.EncodeToString([]byte("To: "+vendorBob+"\r\nSubject: hi\r\n\r\nthread "+gmailMsg1)) + `"}`},
	} {
		code, body, _ := send(t, step.method, proxy+step.path, token, step.body)
		if code != 200 {
			t.Fatalf("%s through the proxy: %d %s", step.name, code, body)
		}
		recorded[step.name] = body
	}
	if !strings.Contains(recorded["list"], gmailCursor) || !strings.Contains(recorded["get"], gmailMsg1) {
		t.Fatalf("the proxy did not pass the API's answers through: %v", recorded)
	}
	if code, logs := rec.stop(t); code != exitOK {
		t.Fatalf("record exited %d: %s", code, logs)
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	for _, v := range []string{token, tokenShaped("cookie"), vendorAlice, vendorBob, gmailMsg1, gmailMsg2, gmailSent,
		strings.ReplaceAll(vendorAlice, "@", "%40")} {
		if strings.Contains(text, v) {
			t.Errorf("the cassette holds a recorded value (%d bytes)", len(v))
		}
	}
	c, err := cassette.Load(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(c.HTTP.Exchanges) != 4 || !strings.Contains(c.HTTP.Exchanges[1].Request.Query, "pageToken="+gmailCursor) {
		t.Fatalf("recorded %d exchanges; page 2 query %q", len(c.HTTP.Exchanges), c.HTTP.Exchanges[1].Request.Query)
	}
	if setCookie := c.HTTP.Exchanges[0].Response.Headers.Get("Set-Cookie"); !strings.HasPrefix(setCookie, "GAPS=scrubbed-token-") {
		t.Errorf("Set-Cookie = %q", setCookie)
	}
	if enc := c.HTTP.Exchanges[0].Response.Headers.Get("Content-Encoding"); enc != "" || len(c.HTTP.Exchanges[0].Response.Body.JSON) == 0 {
		t.Errorf("the gzip answer was not recorded decoded (Content-Encoding %q)", enc)
	}

	replayAddr := filepath.Join(dir, "replay-addr")
	rep := startCLI(t, "replay", "--cassette", out, "--addr", "127.0.0.1:0", "--addr-file", replayAddr)
	base := "http://" + waitFile(t, replayAddr, rep)
	before := hits.Load()
	live := tokenShaped("another-live-bearer")
	x := c.HTTP.Exchanges
	steps := []struct{ name, method, path, body string }{
		{"list", "GET", x[0].Request.Path + "?" + x[0].Request.Query, ""},
		{"page2", "GET", x[1].Request.Path + "?" + x[1].Request.Query, ""},
	}
	_, listed, _ := send(t, "GET", base+steps[0].path, live, "")
	id := regexp.MustCompile(`"id":"(scrubbed-id-[0-9]+)"`).FindStringSubmatch(listed)
	if id == nil {
		t.Fatalf("no id placeholder in the replayed list: %s", listed)
	}
	sendBody, _ := x[3].Request.Body.Bytes()
	steps = append(steps,
		struct{ name, method, path, body string }{"get", "GET", "/gmail/v1/users/me/messages/" + id[1] + "?format=full", ""},
		struct{ name, method, path, body string }{"send", "POST", x[3].Request.Path, string(sendBody)})
	for i, step := range steps {
		code, body, hdr := send(t, step.method, base+step.path, live, step.body)
		want, _ := x[i].Response.Body.Bytes()
		if code != 200 || compactRaw(t, []byte(body)) != compactRaw(t, want) || hdr.Get("X-Agentcc-Cassette") != "" {
			t.Errorf("replayed %s = %d %s\nrecorded %s", step.name, code, body, want)
		}
	}
	if hits.Load() != before {
		t.Error("the replay reached the API")
	}
	if code, logs := rep.stop(t); code != exitOK {
		t.Errorf("replay exited %d: %s", code, logs)
	}
}
