package chwriter

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type delayedBodyTransport struct{ started, release, closed chan struct{} }

func (d *delayedBodyTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	// RoundTripper is allowed to finish reading/closing the request body in a
	// goroutine after returning a response. The writer must retain its encoding
	// reservation until that owner has released the body, not merely until EOF
	// on the independent response body.
	go func() {
		close(d.started)
		<-d.release
		io.Copy(io.Discard, r.Body)
		r.Body.Close()
		close(d.closed)
	}()
	return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("")), ContentLength: 0, Request: r}, nil
}

func TestInsertRedirectBodiesFinish(t *testing.T) {
	var target atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/target" {
			http.Redirect(w, r, "/target", http.StatusTemporaryRedirect)
			return
		}
		b, err := io.ReadAll(r.Body)
		if err != nil || string(b) != "{\"value\":\"redirect\"}\n" {
			t.Error("redirect changed canonical request body")
			w.WriteHeader(400)
			return
		}
		target.Add(1)
		w.WriteHeader(200)
	}))
	defer srv.Close()
	w, err := New(Config{URL: srv.URL, DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	confirmed, err := w.InsertConfirmed(ctx, []map[string]any{{"value": "redirect"}})
	if err != nil || len(confirmed) != 1 || !confirmed[0] || target.Load() != 1 {
		t.Fatalf("redirect outcome confirmed=%v target=%d err=%v", confirmed, target.Load(), err)
	}
}

func TestInsertWaitsForRequestBodyClose(t *testing.T) {
	d := &delayedBodyTransport{make(chan struct{}), make(chan struct{}), make(chan struct{})}
	w, err := New(Config{URL: "http://offline.invalid", DeadLetterFile: t.TempDir() + "/dl.jsonl"})
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	w.client = &http.Client{Transport: d}
	done := make(chan error, 1)
	go func() {
		done <- w.Insert(context.Background(), []map[string]any{{"value": strings.Repeat("x", 1<<20)}})
	}()
	<-d.started
	returned := false
	select {
	case err := <-done:
		returned = true
		if err != nil {
			t.Error(err)
		}
		t.Error("Insert returned while the HTTP transport still owns the encoded request body")
	case <-time.After(100 * time.Millisecond):
	}
	close(d.release)
	<-d.closed
	if !returned {
		select {
		case err := <-done:
			if err != nil {
				t.Fatal(err)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("Insert did not finish after transport released its body")
		}
	}
}
