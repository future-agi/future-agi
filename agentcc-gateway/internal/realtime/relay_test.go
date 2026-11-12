package realtime

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// A speech vendor sends its final results and then closes (Deepgram after CloseStream). The
// relay must deliver everything the vendor sent before ending the session, even when the client
// reads slowly enough that those results are still queued inside the relay at the close.
func TestRelayDeliversWhatTheProviderSentBeforeClosing(t *testing.T) {
	relayDrainTest(t, false)
}

// A caller keeps streaming audio while the vendor closes, so a write to the vendor can fail
// before the relay has seen the vendor's close. That failure must not drop the final results.
func TestRelayDeliversWhatTheProviderSentBeforeClosingWhileTheClientIsSending(t *testing.T) {
	relayDrainTest(t, true)
}

func relayDrainTest(t *testing.T, clientSends bool) {
	t.Helper()
	const messages = 200
	payload := strings.Repeat("x", 64<<10)
	upgrader := websocket.Upgrader{}

	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		// Reading the caller's audio also completes the close handshake, as a vendor does.
		readerDone := make(chan struct{})
		go func() {
			defer close(readerDone)
			for {
				if _, _, err := conn.ReadMessage(); err != nil {
					return
				}
			}
		}()
		for i := 0; i < messages; i++ {
			if err := conn.WriteMessage(websocket.TextMessage, []byte(payload)); err != nil {
				return
			}
		}
		conn.WriteControl(websocket.CloseMessage,
			websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""), time.Now().Add(time.Second))
		select {
		case <-readerDone:
		case <-time.After(5 * time.Second):
		}
	}))
	defer provider.Close()

	relayDone := make(chan string, 1)
	gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstream, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(provider.URL, "http"), nil)
		if err != nil {
			t.Errorf("dialing provider: %v", err)
			return
		}
		client, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			upstream.Close()
			return
		}
		session := NewSession("s", "r", "", "m", "p", client, upstream)
		NewRelay(session, RelayConfig{IdleTimeout: 10 * time.Second, MaxMessageSize: 1 << 20},
			slog.New(slog.NewTextHandler(io.Discard, nil))).Start()
		session.Close("relay_done")
		relayDone <- session.CloseReason
	}))
	defer gateway.Close()

	client, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(gateway.URL, "http"), nil)
	if err != nil {
		t.Fatalf("dialing gateway: %v", err)
	}
	defer client.Close()
	if clientSends {
		go func() {
			audio := make([]byte, 640)
			for client.WriteMessage(websocket.BinaryMessage, audio) == nil {
			}
		}()
	}

	// Let the provider finish and close while the relay still holds most of its messages.
	time.Sleep(300 * time.Millisecond)
	received := 0
	client.SetReadDeadline(time.Now().Add(10 * time.Second))
	for {
		if _, _, err := client.ReadMessage(); err != nil {
			break
		}
		received++
	}
	if received != messages {
		t.Errorf("client received %d of the %d messages the provider sent before closing", received, messages)
	}

	select {
	case reason := <-relayDone:
		if reason != "provider_disconnected" {
			t.Errorf("session close reason = %q, want provider_disconnected", reason)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("relay did not end after the provider closed")
	}
}
