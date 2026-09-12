package realtime

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestRelayIdleKeepalive(t *testing.T) {
	clientPeer, clientConn := wsPair(t)
	providerPeer, providerConn := wsPair(t)

	session := NewSession("s1", "r1", "org", "model", "provider", clientConn, providerConn)
	relay := NewRelay(session, RelayConfig{
		ChannelBufferSize: 8,
		PingInterval:      30 * time.Millisecond,
		PongTimeout:       50 * time.Millisecond,
		MaxMessageSize:    1024,
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))

	var clientPings, providerPings atomic.Int32
	countPings(clientPeer, &clientPings)
	countPings(providerPeer, &providerPings)

	clientGot := make(chan []byte, 8)
	providerGot := make(chan []byte, 8)
	go readInto(clientPeer, clientGot)
	go readInto(providerPeer, providerGot)

	done := make(chan struct{})
	go func() {
		defer close(done)
		relay.Start()
	}()

	waitForPings(t, &clientPings, &providerPings)

	// Idle longer than PongTimeout+PingInterval. Without pings the read
	// deadline would fire (~80ms); with keepalive the session stays up.
	time.Sleep(250 * time.Millisecond)
	if session.IsClosed() {
		t.Fatalf("idle session closed: %s", session.CloseReason)
	}
	if clientPings.Load() == 0 || providerPings.Load() == 0 {
		t.Fatalf("expected pings on both sides, client=%d provider=%d", clientPings.Load(), providerPings.Load())
	}

	if err := clientPeer.WriteMessage(websocket.TextMessage, []byte("hello-provider")); err != nil {
		t.Fatalf("client write: %v", err)
	}
	select {
	case msg := <-providerGot:
		if string(msg) != "hello-provider" {
			t.Fatalf("provider got %q", msg)
		}
	case <-time.After(time.Second):
		t.Fatal("provider did not receive relayed message")
	}

	if err := providerPeer.WriteMessage(websocket.TextMessage, []byte("hello-client")); err != nil {
		t.Fatalf("provider write: %v", err)
	}
	select {
	case msg := <-clientGot:
		if string(msg) != "hello-client" {
			t.Fatalf("client got %q", msg)
		}
	case <-time.After(time.Second):
		t.Fatal("client did not receive relayed message")
	}

	session.Close("test_done")
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("relay did not stop")
	}
}

func countPings(conn *websocket.Conn, n *atomic.Int32) {
	prev := conn.PingHandler()
	conn.SetPingHandler(func(appData string) error {
		n.Add(1)
		return prev(appData)
	})
}

func waitForPings(t *testing.T, clientPings, providerPings *atomic.Int32) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for clientPings.Load() == 0 || providerPings.Load() == 0 {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for pings: client=%d provider=%d", clientPings.Load(), providerPings.Load())
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func readInto(conn *websocket.Conn, got chan []byte) {
	for {
		_, data, err := conn.ReadMessage()
		if err != nil {
			return
		}
		select {
		case got <- data:
		default:
		}
	}
}

// wsPair returns (peer, relayConn). peer is the test-side socket; relayConn
// is the side owned by the relay.
func wsPair(t *testing.T) (peer, relayConn *websocket.Conn) {
	t.Helper()
	upgraded := make(chan *websocket.Conn, 1)
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upgrader := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
		c, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Error(err)
			close(upgraded)
			return
		}
		upgraded <- c
		<-r.Context().Done()
	}))
	t.Cleanup(s.Close)

	url := "ws" + strings.TrimPrefix(s.URL, "http")
	peer, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = peer.Close() })

	relayConn = <-upgraded
	if relayConn == nil {
		t.Fatal("server upgrade failed")
	}
	t.Cleanup(func() { _ = relayConn.Close() })
	return peer, relayConn
}
