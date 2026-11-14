package server

import (
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// Cancelling one Cartesia context must leave the connection and its other
// contexts usable, whatever the action of the configured checks.
func TestSpeechGuardrailsCartesiaCancelKeepsOtherContexts(t *testing.T) {
	for _, action := range []string{"block", "log"} {
		t.Run(action, func(t *testing.T) {
			f := newSpeechFixture(t, "cartesia", "pre", "static", action, nil)
			client, _, err := f.dial(t, "/cartesia/tts/websocket", nil)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = client.Close() }()
			cancelled := `{"model_id":"sonic-3","transcript":"prohibited","context_id":"turn","continue":true}`
			first := `{"model_id":"sonic-3","transcript":"hel","context_id":"other","continue":true}`
			cancel := `{"context_id":"turn","cancel":true}`
			last := `{"model_id":"sonic-3","transcript":"lo","context_id":"other","continue":false}`
			for _, m := range []string{cancelled, first, cancel, last} {
				if err := client.WriteMessage(websocket.TextMessage, []byte(m)); err != nil {
					t.Fatal(err)
				}
			}
			for i := 0; i < 3; i++ {
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				if _, data, err := client.ReadMessage(); err != nil || string(data) != `{"type":"ack"}` {
					t.Fatalf("reply %d after cancel=%s err=%v", i, data, err)
				}
			}
			f.mu.Lock()
			got := append([]string(nil), f.received...)
			f.mu.Unlock()
			want := []string{cancel, first, last}
			if len(got) != len(want) {
				t.Fatalf("provider frames=%v want=%v", got, want)
			}
			for i := range want {
				if got[i] != want[i] {
					t.Fatalf("provider frames=%v want=%v", got, want)
				}
			}
			// The connection stays open for the next context.
			next := `{"model_id":"sonic-3","transcript":"again","context_id":"next"}`
			if err := client.WriteMessage(websocket.TextMessage, []byte(next)); err != nil {
				t.Fatal(err)
			}
			if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
				t.Fatal(err)
			}
			if _, data, err := client.ReadMessage(); err != nil || string(data) != `{"type":"ack"}` {
				t.Fatalf("connection unusable after cancel: %s %v", data, err)
			}
		})
	}
}
