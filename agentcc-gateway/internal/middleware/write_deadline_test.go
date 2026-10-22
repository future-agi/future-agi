package middleware

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// productionChain wraps a handler the way the server does, so the deadline is
// set through the same wrappers a real request passes — recovery's sentinel and
// the license auth usage meter among them. Without each of those unwrapping,
// http.ResponseController cannot reach the connection and the deadline is
// silently not set.
func productionChain(next http.Handler) http.Handler {
	handler := Timeout(200 * time.Millisecond)(next)
	handler = KeyAuth(nil, false)(handler)
	handler = LicenseAuth(config.LicenseAuthConfig{}, nil)(handler)
	handler = RequestID(handler)
	return Recovery(handler)
}

// serveWithShortWriteTimeout runs the chain behind a real listener whose
// server write timeout is far shorter than the request ceiling, which is the
// shape that used to drop the response.
func serveWithShortWriteTimeout(t *testing.T, next http.Handler) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	srv := &http.Server{
		Handler:      productionChain(next),
		WriteTimeout: 20 * time.Millisecond, // expires long before the ceiling
	}
	go srv.Serve(listener)
	t.Cleanup(func() { srv.Close() })
	return "http://" + listener.Addr().String()
}

// A response that takes longer than server.write_timeout used to be
// impossible to write: the deadline is armed when the headers arrive and
// nothing reset it, so the handler ran to the end and the client got a closed
// connection with no status. The request ceiling now moves that deadline.
func TestWriteDeadline_ADelayedResponseStillReachesTheClient(t *testing.T) {
	const body = `{"ok":true}`
	base := serveWithShortWriteTimeout(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(80 * time.Millisecond) // past WriteTimeout, inside the ceiling
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, body)
	}))

	resp, err := http.Get(base + "/v1/models")
	if err != nil {
		t.Fatalf("the response never arrived: %v", err)
	}
	defer resp.Body.Close()

	got, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("reading the body: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Errorf("status = %d, want %d", resp.StatusCode, http.StatusOK)
	}
	if string(got) != body {
		t.Errorf("body = %q, want %q", got, body)
	}
}

// And a request that reaches its ceiling answers with a timeout the client can
// read, rather than a dropped connection.
func TestWriteDeadline_ATimeoutReachesTheClientAsJSON(t *testing.T) {
	base := serveWithShortWriteTimeout(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done() // the ceiling, not the write deadline
		models.WriteError(w, models.ErrGatewayTimeout("request timed out"))
	}))

	resp, err := http.Get(base + "/v1/models")
	if err != nil {
		t.Fatalf("the timeout response never arrived: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusGatewayTimeout {
		t.Errorf("status = %d, want %d", resp.StatusCode, http.StatusGatewayTimeout)
	}
	var payload models.ErrorResponse
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		t.Fatalf("the timeout body was not readable JSON: %v", err)
	}
	if payload.Error.Type != models.ErrTypeGatewayTimeout {
		t.Errorf("type = %q, want %q", payload.Error.Type, models.ErrTypeGatewayTimeout)
	}
}
