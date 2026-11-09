package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// vendorCredAPI is a REST API that takes its credential in a parameter and
// a JSON member no built-in rule names, and echoes it in free text.
func vendorCredAPI(t *testing.T, cred string) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var hits atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == "GET" && r.URL.Path == "/v1/items" && r.URL.Query().Get("vendor_cred") == cred:
			_ = json.NewEncoder(w).Encode(map[string]any{"items": []string{"a", "b"}, "note": "signed in with " + cred})
		case r.Method == "POST" && r.URL.Path == "/v1/search":
			var in struct {
				Handle string `json:"opaqueHandle"`
				Q      string `json:"q"`
			}
			if json.NewDecoder(r.Body).Decode(&in) != nil || in.Handle != cred {
				w.WriteHeader(http.StatusUnauthorized)
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"hits": 1, "q": in.Q})
		default:
			w.WriteHeader(http.StatusUnauthorized)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &hits
}

func plainSend(t *testing.T, method, url, body string) (int, string) {
	t.Helper()
	req, err := http.NewRequest(method, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(data)
}

// TestRecordHTTPSecretEnvAndNames: an HTTP recording names the credential
// the client sends with --secret-env (scrubbed wherever it appears, even in
// free text) and the places it goes with --secret-param and --secret-key;
// the cassette holds no trace of it, carries the names, and replays to a
// client holding another credential.
func TestRecordHTTPSecretEnvAndNames(t *testing.T) {
	cred := tokenShaped("vendor-cred")
	t.Setenv("CASSETTE_TEST_VENDOR_CRED", cred)
	api, hits := vendorCredAPI(t, cred)
	dir := t.TempDir()
	out, addrFile := filepath.Join(dir, "vendor.cassette.json"), filepath.Join(dir, "addr")
	rec := startCLI(t, "record", "--kind", "http", "--test-account", "ci-vendor", "--upstream", api.URL,
		"--addr-file", addrFile, "--out", out, "--secret-env", "CASSETTE_TEST_VENDOR_CRED",
		"--secret-param", "vendor_cred", "--secret-key", "opaqueHandle")
	proxy := "http://" + waitFile(t, addrFile, rec)
	if code, body := plainSend(t, "GET", proxy+"/v1/items?vendor_cred="+cred+"&page=1", ""); code != 200 {
		t.Fatalf("items through the proxy: %d %s", code, body)
	}
	if code, body := plainSend(t, "POST", proxy+"/v1/search", `{"opaqueHandle":"`+cred+`","q":"x"}`); code != 200 {
		t.Fatalf("search through the proxy: %d %s", code, body)
	}
	code, logs := rec.stop(t)
	if code != exitOK {
		t.Fatalf("record exited %d: %s", code, logs)
	}
	if strings.Contains(logs, cred) {
		t.Error("the log carries the credential")
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), cred) {
		t.Fatal("the cassette holds the credential")
	}
	c, err := cassette.Load(data)
	if err != nil {
		t.Fatal(err)
	}
	if c.Rules == nil || strings.Join(c.Rules.SecretParams, ",") != "vendor_cred" || strings.Join(c.Rules.SecretKeys, ",") != "opaqueHandle" {
		t.Errorf("the cassette's rules = %+v", c.Rules)
	}

	replayAddr := filepath.Join(dir, "replay-addr")
	rep := startCLI(t, "replay", "--cassette", out, "--addr", "127.0.0.1:0", "--addr-file", replayAddr)
	base := "http://" + waitFile(t, replayAddr, rep)
	before := hits.Load()
	live := tokenShaped("another-live-cred")
	for i, step := range []struct{ method, path, body string }{
		{"GET", "/v1/items?page=1&vendor_cred=" + live, ""},
		{"POST", "/v1/search", `{"q":"x","opaqueHandle":"` + live + `"}`},
	} {
		code, body := plainSend(t, step.method, base+step.path, step.body)
		want, _ := c.HTTP.Exchanges[i].Response.Body.Bytes()
		if code != 200 || compactRaw(t, []byte(body)) != compactRaw(t, want) {
			t.Errorf("replayed %s %s = %d %s, recorded %s", step.method, step.path, code, body, want)
		}
	}
	if hits.Load() != before {
		t.Error("the replay reached the API")
	}
	if code, logs := rep.stop(t); code != exitOK {
		t.Errorf("replay exited %d: %s", code, logs)
	}
}
