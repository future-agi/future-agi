package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// A WebSocket route (the speech relays, /v1/realtime) takes the connection
// over with gorilla/websocket, which asserts http.Hijacker on the writer it
// is given rather than unwrapping it. Every wrapper in the server's chain must
// therefore hand the connection through, or the upgrade fails with a 500 and
// the client sees a bad handshake.
func TestWebSocketUpgradeThroughTheServerChain(t *testing.T) {
	upgrader := websocket.Upgrader{}
	srv := httptest.NewServer(productionChain(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		mt, data, err := conn.ReadMessage()
		if err != nil {
			return
		}
		conn.WriteMessage(mt, append([]byte("echo:"), data...))
	})))
	defer srv.Close()

	client, resp, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(srv.URL, "http")+"/deepgram/v1/listen", nil)
	if err != nil {
		status := 0
		if resp != nil {
			status = resp.StatusCode
		}
		t.Fatalf("the upgrade failed through the chain (status %d): %v", status, err)
	}
	defer client.Close()
	if err := client.WriteMessage(websocket.TextMessage, []byte("hi")); err != nil {
		t.Fatalf("write: %v", err)
	}
	client.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, got, err := client.ReadMessage()
	if err != nil || string(got) != "echo:hi" {
		t.Fatalf("read %q, %v; want echo:hi", got, err)
	}
}
