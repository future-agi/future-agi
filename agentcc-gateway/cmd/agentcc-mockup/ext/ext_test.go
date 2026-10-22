package ext

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
)

// The fixtures: 1,000 prompt and 100 completion tokens of test/model-a cost
// $0.002 + $0.001 = 3,000 micros; of test/model-b, $0.001 + $0.0002 = 1,200.
const (
	testConfig = `{"report_cost": true, "balance_usd": "10", "prompt_tokens": 1000, "completion_tokens": 100, "stream_chunks": 3}`
	testModels = `{"data": [
  {"id": "test/model-a", "name": "Model A", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
  {"id": "test/model-b", "name": "Model B", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
  {"id": "google/gemini-9-pro", "name": "Gemini", "pricing": {"prompt": "0.000001", "completion": "0.000001"}}
]}`
)

type extMock struct {
	*httptest.Server
	state *mock.State
}

// newExtMock serves the mock with this package wired in, as cmd/agentcc-mockup does.
func newExtMock(t *testing.T) *extMock {
	t.Helper()
	dir := t.TempDir()
	for name, body := range map[string]string{"config.json": testConfig, "models.json": testModels} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	fix, err := mock.LoadFixtures(dir)
	if err != nil {
		t.Fatal(err)
	}
	srv := mock.New(fix)
	srv.SetDirectiveHook(Directive)
	Register(srv.Mux(), srv.State())
	ts := httptest.NewServer(srv)
	t.Cleanup(ts.Close)
	return &extMock{Server: ts, state: srv.State()}
}

// do sends method path with body and headers (alternating name, value) and
// returns the status and the body.
func (m *extMock) do(t *testing.T, method, path, body string, headers ...string) (int, []byte) {
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
		t.Fatalf("%s %s: %v", method, path, err)
	}
	return resp.StatusCode, b
}

// chat makes a chat completion for model with bearer, adding headers.
func (m *extMock) chat(t *testing.T, bearer, body string, headers ...string) (int, []byte) {
	t.Helper()
	return m.do(t, "POST", "/v1/chat/completions", body, append([]string{"Authorization", "Bearer " + bearer}, headers...)...)
}

func chatBody(model string, stream bool) string {
	b, _ := json.Marshal(map[string]any{"model": model, "stream": stream, "messages": []any{map[string]any{"role": "user", "content": "hi"}}})
	return string(b)
}

func decode(t *testing.T, b []byte) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("not a JSON object: %v\n%s", err, b)
	}
	return v
}

// errorCode is the code of an OpenAI-shaped error body.
func errorCode(t *testing.T, b []byte) any {
	t.Helper()
	e, _ := decode(t, b)["error"].(map[string]any)
	return e["code"]
}

// A directive no package knows is still refused loudly, and not charged.
func TestUnknownDirectiveIsRefused(t *testing.T) {
	m := newExtMock(t)
	status, b := m.chat(t, "key-1", chatBody("test/model-a", false), mock.BehaviorHeader, "tools,no_such_directive:2")
	if status != http.StatusBadRequest || errorCode(t, b) != "unknown_mock_directive" || !strings.Contains(string(b), "no_such_directive") {
		t.Fatalf("status %d body %s", status, b)
	}
	if total, n := m.state.TotalChargeMicros(); n != 0 || total != 0 {
		t.Fatalf("a refused call was charged: %d micros over %d", total, n)
	}
}
