package logging

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
)

// A refused dial must read as a refused connection on every platform.
func TestRefusedConnection(t *testing.T) {
	gone := httptest.NewServer(http.NotFoundHandler())
	gone.Close()

	_, err := (&http.Client{}).Get(gone.URL)
	if err == nil {
		t.Fatal("a closed httptest server answered a request; cannot test a refused connection")
	}
	if !refusedConnection(err) {
		t.Errorf("refusedConnection(%v) = false, want true", err)
	}
	if !backendGone(fmt.Errorf("posting request log: %w", err)) {
		t.Error("backendGone(wrapped refusal) = false, want true")
	}
	if refusedConnection(context.Canceled) || refusedConnection(nil) {
		t.Error("refusedConnection accepted an error that is not a refused connection")
	}
	if backendGone(&net.DNSError{Err: "server misbehaving", Name: "backend", IsTemporary: true}) {
		t.Error("backendGone(temporary DNS error) = true, want false")
	}
}
