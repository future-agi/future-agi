package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// Fake credentials for the local fake; neither is a real token.
const (
	testToken   = "ya29.cn10-cmd-fake-user-token-0123456789"
	gmailOrigin = "https://gmail.googleapis.com"
)

// cn4Catalog is the rest example catalog directory (six rest apps).
const cn4Catalog = "../../../scripts/connector-gen/rest/tests/fixtures/catalog"

// syncBuffer is a goroutine-safe log sink.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func TestParseFlags(t *testing.T) {
	o, err := parseFlags(nil, io.Discard)
	if err != nil || o.addr != DefaultAddr || o.catalogDir != "" || len(o.replay) != 0 || o.pageSize != 50 {
		t.Fatalf("defaults: %+v, %v", o, err)
	}
	o, err = parseFlags([]string{"--addr", "127.0.0.1:0", "--catalog-dir", "/x", "--replay", gmailOrigin + "=http://127.0.0.1:9",
		"--replay", "https://graph.microsoft.com=http://127.0.0.1:10", "--vendor-timeout", "5s", "--max-vendor-bytes", "1024",
		"--page-size", "7"}, io.Discard)
	if err != nil || o.catalogDir != "/x" || len(o.replay) != 2 || o.replay[gmailOrigin] != "http://127.0.0.1:9" ||
		o.vendorTimeout != 5*time.Second || o.maxVendorBytes != 1024 || o.pageSize != 7 {
		t.Fatalf("flags: %+v, %v", o, err)
	}
	for _, bad := range [][]string{
		{"--replay", "no-equals-sign"},
		{"--replay", gmailOrigin + "="},
		{"--replay", gmailOrigin + "=http://127.0.0.1:9", "--replay", gmailOrigin + "=http://127.0.0.1:10"},
		{"--vendor-timeout", "0s"},
		{"--max-vendor-bytes", "0"},
		{"--page-size", "-1"},
		{"--no-such-flag"},
		{"serve"},
	} {
		if _, err := parseFlags(bad, io.Discard); err == nil {
			t.Errorf("%v was accepted", bad)
		}
	}
	if code := run(context.Background(), []string{"--page-size", "0"}, io.Discard); code != exitUsage {
		t.Errorf("bad flags: exit %d, want %d", code, exitUsage)
	}
}

// copyDir copies a catalog subtree into dst.
func copyDir(t *testing.T, src, dst string) {
	t.Helper()
	err := filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
}

// TestRefusesWhatItCannotServe: the process does not start on a catalog
// with no rest provider (rather than answer 404 for every app), on a rest
// provider it cannot serve as declared, or on a missing directory.
func TestRefusesWhatItCannotServe(t *testing.T) {
	noRest := t.TempDir()
	copyDir(t, "../../catalog/connectors/apps/linear", filepath.Join(noRest, "apps", "linear"))
	bad := t.TempDir()
	copyDir(t, filepath.Join(cn4Catalog, "apps", "gmail"), filepath.Join(bad, "apps", "gmail"))
	gen := filepath.Join(bad, "apps", "gmail", "providers", "futureagi-rest", "tools.generated.json")
	data, err := os.ReadFile(gen)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(gen, bytes.Replace(data, []byte(`"x-origin": "https://gmail.googleapis.com"`),
		[]byte(`"x-origin": "https://gmail.example.com"`), 1), 0o644); err != nil {
		t.Fatal(err)
	}
	for dir, want := range map[string]string{
		noRest:                        "has no kind: rest provider to serve",
		bad:                           "is not under the app's vendor.domains",
		filepath.Join(noRest, "nope"): "is not a directory",
	} {
		var stderr syncBuffer
		code := run(context.Background(), []string{"--addr", "127.0.0.1:0", "--catalog-dir", dir}, &stderr)
		if code != exitFail || !strings.Contains(stderr.String(), want) {
			t.Errorf("--catalog-dir %s: exit %d, log %s; want exit %d naming %q", dir, code, stderr.String(), exitFail, want)
		}
	}
	var stderr syncBuffer
	code := run(context.Background(), []string{"--addr", "127.0.0.1:0", "--catalog-dir", cn4Catalog,
		"--replay", gmailOrigin + "=http://10.0.0.8:80"}, &stderr)
	if code != exitFail || !strings.Contains(stderr.String(), "is not a loopback address") {
		t.Errorf("a replay target off loopback: exit %d, %s", code, stderr.String())
	}
}

// TestServesTheCatalogEndToEnd: the binary serves every rest app of a
// catalog directory, answers /healthz, runs a tool call against a replayed
// vendor with the caller's bearer, logs it without the bearer, and drains
// and exits 0 when stopped.
func TestServesTheCatalogEndToEnd(t *testing.T) {
	t.Setenv("AGENTCC_MCP_K2_APPS_ENABLED", "true")
	var gotAuth []string
	var mu sync.Mutex
	vendor := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		gotAuth = append(gotAuth, r.Header.Get("Authorization"))
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"emailAddress":"someone@example.test","messagesTotal":3,"threadsTotal":2,"historyId":"7"}`))
	}))
	defer vendor.Close()
	addrFile := filepath.Join(t.TempDir(), "addr")
	ctx, cancel := context.WithCancel(context.Background())
	var stderr syncBuffer
	done := make(chan int, 1)
	go func() {
		done <- run(ctx, []string{"--addr", "127.0.0.1:0", "--addr-file", addrFile, "--catalog-dir", cn4Catalog,
			"--replay", gmailOrigin + "=" + vendor.URL}, &stderr)
	}()
	var base string
	for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		if b, err := os.ReadFile(addrFile); err == nil && len(b) > 0 {
			base = "http://" + strings.TrimSpace(string(b))
			break
		}
		select {
		case code := <-done:
			t.Fatalf("exited %d before listening: %s", code, stderr.String())
		default:
		}
		if time.Now().After(deadline) {
			t.Fatalf("never listened: %s", stderr.String())
		}
	}

	resp, err := http.Get(base + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	var health struct {
		Status string   `json:"status"`
		Apps   []string `json:"apps"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&health)
	resp.Body.Close()
	if resp.StatusCode != 200 || health.Status != "ok" || len(health.Apps) != 6 {
		t.Errorf("/healthz: %d %+v, want ok with the example's six apps", resp.StatusCode, health)
	}

	call := func(auth string) (*http.Response, []byte) {
		req, _ := http.NewRequest(http.MethodPost, base+"/gmail/mcp",
			strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_profile","arguments":{}}}`))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		req.Header.Set("MCP-Protocol-Version", "2025-11-25")
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		return resp, body
	}
	if resp, _ := call(""); resp.StatusCode != http.StatusUnauthorized || resp.Header.Get("WWW-Authenticate") == "" {
		t.Errorf("no bearer: HTTP %d, WWW-Authenticate %q", resp.StatusCode, resp.Header.Get("WWW-Authenticate"))
	}
	resp, body := call("Bearer " + testToken)
	if resp.StatusCode != 200 || !strings.Contains(string(body), `someone@example.test`) || strings.Contains(string(body), `"isError":true`) {
		t.Errorf("get_profile: HTTP %d %s", resp.StatusCode, body)
	}
	mu.Lock()
	if len(gotAuth) != 1 || gotAuth[0] != "Bearer "+testToken {
		t.Errorf("the vendor got %d calls; want one with the caller's bearer", len(gotAuth))
	}
	mu.Unlock()

	cancel()
	select {
	case code := <-done:
		if code != exitOK {
			t.Errorf("stopped: exit %d, want 0", code)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("did not stop")
	}
	logs := stderr.String()
	for _, want := range []string{`"msg":"agentcc-connectors serving"`, `"msg":"connectors request"`, `"tool":"get_profile"`,
		`"outcome":"ok"`, `"outcome":"unauthenticated"`, `"msg":"agentcc-connectors stopped"`} {
		if !strings.Contains(logs, want) {
			t.Errorf("the log has no %s:\n%s", want, logs)
		}
	}
	if strings.Contains(logs, testToken) || strings.Contains(logs, strings.TrimPrefix(testToken, "ya29.")) {
		t.Error("the log holds the bearer")
	}
}
