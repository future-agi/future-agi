package wire

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
)

type wp03RoundTrip func(*http.Request) (*http.Response, error)

func (f wp03RoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type wp03Body struct {
	io.Reader
	read   int
	closed bool
}

func (b *wp03Body) Read(p []byte) (int, error) {
	n, err := b.Reader.Read(p)
	b.read += n
	return n, err
}
func (b *wp03Body) Close() error { b.closed = true; return nil }

func wp03Post(t *testing.T, text, contentType string, max int64, sink func([]byte)) (Reply, error, *wp03Body) {
	t.Helper()
	body := &wp03Body{Reader: strings.NewReader(text)}
	client := &http.Client{Transport: wp03RoundTrip(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {contentType}}, Body: body}, nil
	})}
	rep, err := Post(context.Background(), Exchange{Client: client, URL: "https://synthetic.invalid/mcp", Body: []byte(`{}`), ID: json.RawMessage(`1`), MaxBytes: max, OnMessage: sink})
	if !body.closed {
		t.Fatal("response body not closed")
	}
	return rep, err, body
}

func TestWP03WireRefusesBeforeDecodeOrRelease(t *testing.T) {
	cases := map[string]string{
		"duplicate-envelope": `{"jsonrpc":"2.0","id":1,"result":{},"result":{"injected":true}}`,
		"escaped-duplicate":  `{"jsonrpc":"2.0","id":1,"result":{"x":1,"x":2}}`,
		"depth":              `{"jsonrpc":"2.0","id":1,"result":` + strings.Repeat(`[`, 130) + `0` + strings.Repeat(`]`, 130) + `}`,
		"invalid-utf8":       "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"x\":\"\xff\"}}",
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			rep, err, _ := wp03Post(t, raw, "application/json", 1<<20, nil)
			if err == nil || len(rep.Message) != 0 {
				t.Fatalf("untrusted envelope released: err=%v bytes=%d", err, len(rep.Message))
			}
		})
	}
}

func TestWP03WireEarlyDepthRefusal(t *testing.T) {
	raw := `{"jsonrpc":"2.0","id":1,"result":` + strings.Repeat(`[`, 130) + `"` + strings.Repeat("x", 1<<20) + `"` + strings.Repeat(`]`, 130) + `}`
	_, err, body := wp03Post(t, raw, "application/json", 2<<20, nil)
	if err == nil || body.read > 1024 {
		t.Fatalf("depth refusal read %d bytes, err=%v", body.read, err)
	}
}

func TestWP03WireSSEDoesNotReleaseRejectedNotification(t *testing.T) {
	sinkCalls := 0
	raw := "data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{\"x\":1,\"x\":2}}\n\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n\n"
	rep, err, _ := wp03Post(t, raw, "text/event-stream", 4096, func([]byte) { sinkCalls++ })
	if err == nil || sinkCalls != 0 || len(rep.Message) != 0 {
		t.Fatalf("SSE released rejected bytes: callbacks=%d answer=%d err=%v", sinkCalls, len(rep.Message), err)
	}
}

func TestWP03WireHealthyJSONSSEAndByteBoundary(t *testing.T) {
	raw := `{"jsonrpc":"2.0","id":1,"result":{"content":[],"extension":{"kept":true}}}`
	for _, ct := range []string{"application/json", "text/event-stream"} {
		body := raw
		if ct == "text/event-stream" {
			body = "data: " + raw + "\n\n"
		}
		rep, err, _ := wp03Post(t, body, ct, int64(len(raw)), nil)
		if err != nil || string(rep.Message) != raw {
			t.Fatalf("healthy %s changed: %v", ct, err)
		}
	}
	_, err, _ := wp03Post(t, raw, "application/json", int64(len(raw)-1), nil)
	if !errors.Is(err, ErrTooLarge) {
		t.Fatalf("lost existing byte-error contract: %v", err)
	}
}

func TestWP03LegacyStreamRefusesBeforePendingDelivery(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		raw := `{"jsonrpc":"2.0","id":1,"result":{"content":[]}}`
		if invalid {
			raw = `{"jsonrpc":"2.0","id":1,"result":{"x":1,"x":2}}`
		}
		ch := make(chan []byte, 1)
		session := &Session{done: make(chan struct{}), pending: map[string]chan []byte{"1": ch}}
		body := io.NopCloser(strings.NewReader("data: " + raw + "\n\n"))
		session.read(body, NewReader(body, 4096))
		got, ok := <-ch
		if invalid && (ok || len(got) != 0) {
			t.Fatal("legacy SSE released ambiguous message")
		}
		if !invalid && (!ok || string(got) != raw) {
			t.Fatal("legacy SSE lost healthy result")
		}
	}
}

func TestWP03ErrorBodyNeverPublishesAmbiguousRPC(t *testing.T) {
	body := &wp03Body{Reader: strings.NewReader(`{"jsonrpc":"2.0","id":1,"error":{"code":1,"message":"safe","message":"synthetic-rejected"}}`)}
	client := &http.Client{Transport: wp03RoundTrip(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 400, Header: http.Header{"Content-Type": {"application/json"}}, Body: body}, nil
	})}
	rep, err := Post(context.Background(), Exchange{Client: client, URL: "https://synthetic.invalid/mcp", MaxBytes: 4096})
	if err != nil || rep.Status != 400 || len(rep.Message) != 0 || !body.closed {
		t.Fatalf("unsafe error body escaped or status lost: status=%d bytes=%d err=%v", rep.Status, len(rep.Message), err)
	}
}
