package mock

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// testModels is a two-model catalog with round prices: 1,000 prompt and 100
// completion tokens of test/model-a cost $0.002 + $0.001.
const testModels = `{"data": [
  {"id": "test/model-a", "name": "Model A", "context_length": 1000,
   "pricing": {"prompt": "0.000002", "completion": "0.00001", "input_cache_read": "0.0000005", "input_cache_write": "0.000003"},
   "top_provider": {"max_completion_tokens": 500}, "supported_parameters": ["tools"]},
  {"id": "test/model-b", "name": "Model B", "context_length": 2000,
   "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
]}`

const testConfig = `{"report_cost": true, "balance_usd": "10", "prompt_tokens": 1000, "completion_tokens": 100,
  "stream_chunks": 4, "default_pricing": {"prompt": 0.000003, "completion": 0.000004}}`

type testMock struct {
	*httptest.Server
	srv *Server
}

// newTestMock serves a mock over fixtures holding config (testConfig when
// empty) and testModels.
func newTestMock(t *testing.T, config string) *testMock {
	t.Helper()
	if config == "" {
		config = testConfig
	}
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "config.json"), config)
	writeFile(t, filepath.Join(dir, "models.json"), testModels)
	return serveFixtures(t, dir)
}

// serveFixtures serves a mock over the fixtures in dir.
func serveFixtures(t *testing.T, dir string) *testMock {
	t.Helper()
	fix, err := LoadFixtures(dir)
	if err != nil {
		t.Fatalf("fixtures: %v", err)
	}
	srv := New(fix)
	ts := httptest.NewServer(srv)
	t.Cleanup(ts.Close)
	return &testMock{Server: ts, srv: srv}
}

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// do sends method path with body and headers (alternating name, value).
func (m *testMock) do(t *testing.T, method, path, body string, headers ...string) (*http.Response, []byte) {
	t.Helper()
	req, err := http.NewRequest(method, m.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("%s %s: reading body: %v", method, path, err)
	}
	return resp, b
}

// chat posts a chat completion for model with the given x-mock-behavior.
func (m *testMock) chat(t *testing.T, model, behavior string, stream bool, extra ...string) (*http.Response, []byte) {
	t.Helper()
	body := map[string]any{"model": model, "stream": stream, "messages": []any{map[string]any{"role": "user", "content": "hi"}}}
	b, _ := json.Marshal(body)
	headers := append([]string{"Authorization", "Bearer key-1"}, extra...)
	if behavior != "" {
		headers = append(headers, BehaviorHeader, behavior)
	}
	return m.do(t, "POST", "/v1/chat/completions", string(b), headers...)
}

func decode(t *testing.T, b []byte) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("not JSON: %v\n%s", err, b)
	}
	return v
}

// sseEvent is one server-sent event.
type sseEvent struct{ event, data string }

// parseSSE splits a complete stream into its events, failing on any line that
// is not "event:", "data:" or the blank line ending an event.
func parseSSE(t *testing.T, b []byte) []sseEvent {
	t.Helper()
	var out []sseEvent
	var cur sseEvent
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		line := sc.Text()
		switch {
		case line == "":
			if cur.data == "" {
				t.Fatalf("event without data in stream:\n%s", b)
			}
			out = append(out, cur)
			cur = sseEvent{}
		case strings.HasPrefix(line, "event: "):
			cur.event = strings.TrimPrefix(line, "event: ")
		case strings.HasPrefix(line, "data: "):
			cur.data = strings.TrimPrefix(line, "data: ")
		default:
			t.Fatalf("malformed SSE line %q in:\n%s", line, b)
		}
	}
	if cur.data != "" || cur.event != "" {
		t.Fatalf("stream ends mid-event:\n%s", b)
	}
	return out
}

func charges(t *testing.T, m *testMock) (int64, int) {
	t.Helper()
	_, b := m.do(t, "GET", "/__charges", "")
	v := decode(t, b)
	return int64(v["total_micros"].(float64)), int(v["count"].(float64))
}
