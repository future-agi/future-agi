package cassette

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeToken is a credential-looking value that matches no secret shape, so
// only the scrubber's rules, not the shape refusal, can remove it.
func fakeToken(label string) string { return label + "-Qz8" + strings.Repeat("vK3", 6) }

func newHTTPCassette(xs ...HTTPExchange) *Cassette {
	return &Cassette{Kind: KindHTTP, TestAccount: "ci-test-account", RecordedAt: time.Now().UTC().Format(time.RFC3339),
		HTTP: &HTTPRecording{Exchanges: xs}}
}

func newMCPCassette(xs ...MCPExchange) *Cassette {
	return &Cassette{Kind: KindMCP, TestAccount: "ci-test-account", RecordedAt: time.Now().UTC().Format(time.RFC3339),
		MCP: &MCPRecording{ProtocolVersion: "2025-11-25", Exchanges: xs}}
}

func hdr(kv ...string) http.Header {
	h := http.Header{}
	for i := 0; i+1 < len(kv); i += 2 {
		h.Add(kv[i], kv[i+1])
	}
	return h
}

func jsonBody(v any) *Body {
	data, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return &Body{JSON: data}
}

func raw(s string) json.RawMessage { return json.RawMessage(s) }

// saveOK saves c into a fresh directory and returns the file's text.
func saveOK(t *testing.T, c *Cassette, cfg ScrubConfig) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "c.cassette.json")
	if err := Save(path, c, cfg); err != nil {
		t.Fatalf("Save: %v", err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := Check(data); err != nil {
		t.Fatalf("Check on the saved cassette: %v", err)
	}
	return string(data)
}

// saveRefused saves c, wants a refusal, and checks the directory stayed
// empty: no cassette and no temporary file.
func saveRefused(t *testing.T, c *Cassette, cfg ScrubConfig) *RefusedError {
	t.Helper()
	dir := t.TempDir()
	err := Save(filepath.Join(dir, "c.cassette.json"), c, cfg)
	var refused *RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("Save = %v, want a refusal", err)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 0 {
		t.Fatalf("a refused save left %d file(s) behind, e.g. %s", len(entries), entries[0].Name())
	}
	return refused
}

func assertAbsent(t *testing.T, text string, values ...string) {
	t.Helper()
	for _, v := range values {
		if strings.Contains(text, v) {
			t.Errorf("the saved cassette still holds a recorded value (%d bytes, starts %.3s...)", len(v), v)
		}
	}
}

func assertPresent(t *testing.T, text string, values ...string) {
	t.Helper()
	for _, v := range values {
		if !strings.Contains(text, v) {
			t.Errorf("the saved cassette lacks %q", v)
		}
	}
}

// compactJSON is raw without whitespace (a loaded cassette keeps the file's
// indentation inside its JSON values).
func compactJSON(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var buf bytes.Buffer
	if err := json.Compact(&buf, raw); err != nil {
		t.Fatal(err)
	}
	return buf.String()
}

// jsonEscapeFirst writes the first r in s as a JSON unicode escape, which a
// scan of the raw file cannot see through.
func jsonEscapeFirst(s string, r rune) string {
	return strings.Replace(s, string(r), "\\"+"u"+fmt.Sprintf("%04x", r), 1)
}

// loadSaved parses a saved cassette back.
func loadSaved(t *testing.T, text string) *Cassette {
	t.Helper()
	c, err := Load([]byte(text))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	return c
}
