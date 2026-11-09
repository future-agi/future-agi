package server

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A Future AGI stream on /v1/messages is billed as chat bills the same
// stream. The upstream charges for the prompt and for every token it
// generates however the stream ends, so a stream that ends before the
// upstream reports usage (the upstream sends none, the client leaves, the
// upstream fails mid-stream, a guardrail withholds the answer) is billed on
// an estimate, and a stream that reports usage is billed from it once, at
// the upstream's charge plus the markup.

// managedStreamText is the text the managed upstream streams first.
const managedStreamText = "hello there"

// writeManagedChunk writes one chat stream chunk for model, with rest as its
// fields after the model, and flushes it.
func writeManagedChunk(w http.ResponseWriter, model, rest string) {
	fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":%q,%s}\n\n", model, rest)
	w.(http.Flusher).Flush()
}

// textThenWait streams managedStreamText, then waits for the request to end
// (the client left) or, after five seconds, finishes with the upstream's
// usage chunk.
func textThenWait(w http.ResponseWriter, r *http.Request, model string) {
	w.Header().Set("Content-Type", "text/event-stream")
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"`+managedStreamText+`"}}]`)
	select {
	case <-r.Context().Done():
		return
	case <-time.After(5 * time.Second):
	}
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
	writeManagedChunk(w, model, fmt.Sprintf(`"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}`, upstreamCost))
	fmt.Fprint(w, "data: [DONE]\n\n")
}

// textThenFail streams managedStreamText, then drops the connection: the
// stream fails mid-way, before any usage.
func textThenFail(w http.ResponseWriter, _ *http.Request, model string) {
	w.Header().Set("Content-Type", "text/event-stream")
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"`+managedStreamText+`"}}]`)
	panic(http.ErrAbortHandler)
}

// streamManaged sends a stream of the managed model on surface through a
// real HTTP server. It reads the answer to its end or, with leave, until the
// streamed text arrives, and then leaves. It returns once the gateway has
// finished the request, with the status and what the client read.
func streamManaged(t *testing.T, g *managedTestGateway, surface string, leave bool) (int, string) {
	t.Helper()
	status, got, _ := sendManaged(t, g, surface, "anthropic/claude-sonnet-5.5", true, leave, nil)
	return status, got
}

// sendManaged is streamManaged for model, streamed or not, with headers
// added to the request, and also returns the response's headers.
func sendManaged(t *testing.T, g *managedTestGateway, surface, model string, stream, leave bool, headers map[string]string) (int, string, http.Header) {
	t.Helper()
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	// Close waits for the gateway's handler, which settles the request.
	defer ts.Close()
	body := fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, model, stream)
	if surface == "/v1/messages" {
		body = managedMessagesBody(model, stream, "")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ts.URL+surface, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if !leave {
		got, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(got), resp.Header
	}
	var got strings.Builder
	buf := make([]byte, 4096)
	for deadline := time.Now().Add(4 * time.Second); time.Now().Before(deadline) && !strings.Contains(got.String(), managedStreamText); {
		n, rerr := resp.Body.Read(buf)
		got.Write(buf[:n])
		if rerr != nil {
			break
		}
	}
	if !strings.Contains(got.String(), managedStreamText) {
		t.Fatalf("%s: the client never got the streamed text; read %q", surface, got.String())
	}
	cancel()
	return resp.StatusCode, got.String(), resp.Header
}

// settledAccount waits until the org holds nothing, then returns what it
// has spent and holds.
func settledAccount(t *testing.T, g *managedTestGateway) (spent, held int64) {
	t.Helper()
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if a := g.account(t, "org-m"); a.HeldMicros == 0 {
			return a.SpentMicros, 0
		}
	}
	a := g.account(t, "org-m")
	return a.SpentMicros, a.HeldMicros
}

// TestManagedMessagesStreamWithoutUsageIsBilledAsChatIs: a Future AGI stream
// that ends before the upstream reports usage is billed on an estimate, on
// /v1/messages as on chat: the same amount, one settlement marked as an
// estimate, nothing left held.
func TestManagedMessagesStreamWithoutUsageIsBilledAsChatIs(t *testing.T) {
	for _, end := range []struct {
		name     string
		opts     managedGatewayOptions
		upstream upstreamStream
		noUsage  bool
		leave    bool
	}{
		{name: "the upstream sends no usage", noUsage: true},
		{name: "the client leaves after the first text", upstream: textThenWait, leave: true},
		{name: "the upstream fails mid-stream", upstream: textThenFail},
		{name: "a guardrail withholds the answer", opts: managedGatewayOptions{blockAnswers: true}, noUsage: true},
	} {
		t.Run(end.name, func(t *testing.T) {
			spent := map[string]int64{}
			for _, surface := range []string{"/v1/chat/completions", "/v1/messages"} {
				g := newManagedTestGatewayWith(t, managedOptIn(), end.opts)
				g.ledger.SetMicros("org-m", auth.USDToMicros(1))
				g.managed.noUsage.Store(end.noUsage)
				if end.upstream != nil {
					g.managed.streamWith.Store(&end.upstream)
				}
				status, got := streamManaged(t, g, surface, end.leave)
				if status != http.StatusOK {
					t.Fatalf("%s: status %d; body %s", surface, status, got)
				}
				if g.managed.calls() != 1 {
					t.Fatalf("%s: %d upstream calls, want 1", surface, g.managed.calls())
				}
				s, held := settledAccount(t, g)
				if held != 0 {
					t.Errorf("%s: %d micros still held after the stream ended", surface, held)
				}
				if s <= 0 {
					t.Errorf("%s: a Future AGI stream the upstream charged for was billed nothing (spent %d micros)", surface, s)
				}
				recs := g.outbox("org-m")
				if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["usage_estimated"] != "1" {
					t.Errorf("%s: settlement records %v, want one spend on an estimate", surface, recs)
				}
				spent[surface] = s
			}
			if spent["/v1/messages"] != spent["/v1/chat/completions"] {
				t.Errorf("/v1/messages billed %d micros where chat bills %d for the same stream",
					spent["/v1/messages"], spent["/v1/chat/completions"])
			}
		})
	}
}

// TestManagedMessagesStreamWithUsageIsBilledOnce: a Future AGI stream whose
// upstream reports usage is billed from it, once, at the upstream's charge
// plus 5%, on /v1/messages as on chat, and never on an estimate.
func TestManagedMessagesStreamWithUsageIsBilledOnce(t *testing.T) {
	for _, surface := range []string{"/v1/chat/completions", "/v1/messages"} {
		g := newManagedTestGateway(t, managedOptIn())
		g.ledger.SetMicros("org-m", auth.USDToMicros(1))
		status, got := streamManaged(t, g, surface, false)
		if status != http.StatusOK {
			t.Fatalf("%s: status %d; body %s", surface, status, got)
		}
		if _, held := settledAccount(t, g); held != 0 {
			t.Errorf("%s: %d micros still held after the stream ended", surface, held)
		}
		billed := upstreamCost * 1.05
		if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != fmt.Sprintf("%.6f", 1-billed) {
			t.Errorf("%s: org balance = %.6f, want %.6f: billed once at the upstream's charge plus 5%%", surface, bal, 1-billed)
		}
		recs := g.outbox("org-m")
		if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["usage_estimated"] == "1" {
			t.Errorf("%s: settlement records %v, want one spend from the reported usage", surface, recs)
		}
	}
}
