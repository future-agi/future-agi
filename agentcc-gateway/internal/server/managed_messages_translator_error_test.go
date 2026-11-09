package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// sendManagedWithin streams model on surface as sendManaged does, but gives
// the client a deadline: complete is false when the gateway had not ended
// the response by then.
func sendManagedWithin(t *testing.T, g *managedTestGateway, surface, model string, within time.Duration) (status int, body string, complete bool) {
	t.Helper()
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	// Close waits for the gateway's handler, which settles the request.
	defer ts.Close()
	reqBody := fmt.Sprintf(`{"model":%q,"stream":true,"messages":[{"role":"user","content":"hi"}]}`, model)
	if surface == "/v1/messages" {
		reqBody = managedMessagesBody(model, true, "")
	}
	ctx, cancel := context.WithTimeout(context.Background(), within)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ts.URL+surface, strings.NewReader(reqBody))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return 0, "", false
		}
		t.Fatal(err)
	}
	defer resp.Body.Close()
	got, err := io.ReadAll(resp.Body)
	return resp.StatusCode, string(got), err == nil
}

// The Messages translator stops reading the stream at its own error (here a
// tool call that reaches its finish without a name). The upstream's events
// after that point, its usage report among them, must still be read: the
// request is settled from them, and the response ends when the upstream's
// stream does. Nothing may wait on a translator that has returned.
func TestManagedMessagesTranslatorErrorSettles(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	installBillabilityModels(billabilityModels(t))(t, g)
	g.ledger.SetMicros("org-m", 1_000_000)
	up := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_nameless","type":"function"}]}}]`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"hello there"}}]`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]`)
		writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3}`)
		fmt.Fprint(w, "data: [DONE]\n\n")
	})
	g.managed.streamWith.Store(&up)

	status, body, complete := sendManagedWithin(t, g, "/v1/messages", "acme/token", 10*time.Second)
	spent, held := settledAccount(t, g)
	calls := g.managed.calls()
	t.Logf("translator-error status=%d complete=%t calls=%d spent=%d held=%d", status, complete, calls, spent, held)
	if !complete {
		t.Fatalf("the response did not end after the translator's error: status=%d spent=%d held=%d body=%.400s", status, spent, held, body)
	}
	if status != http.StatusOK || !strings.Contains(body, "event: message_start") || !strings.Contains(body, "incomplete tool identity") {
		t.Errorf("want the started stream to end with the translator's error event: status=%d body=%.600s", status, body)
	}
	if calls != 1 || spent != 48 || held != 0 {
		t.Errorf("want one dispatch billed from its reported usage (48): calls=%d spent=%d held=%d", calls, spent, held)
	}
}
