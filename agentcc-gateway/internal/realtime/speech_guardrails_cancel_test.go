package realtime

import (
	"testing"

	"github.com/gorilla/websocket"
)

func TestSpeechGuardrailCartesiaCancelDiscardsOnlyThatContext(t *testing.T) {
	g, seen := speechInspector(t, "cartesia", "pre")
	other := `{"context_id":"other","transcript":"hel","continue":true}`
	for _, event := range []string{`{"context_id":"turn","transcript":"prohibited","continue":true}`, other} {
		if frames, err := g.Client(websocket.TextMessage, []byte(event)); err != nil || len(frames) != 0 {
			t.Fatal("pending text was not held", frames, err)
		}
	}
	cancel := `{"context_id":"turn","cancel":true}`
	frames, err := g.Client(websocket.TextMessage, []byte(cancel))
	if err != nil || len(frames) != 1 || string(frames[0].Data) != cancel {
		t.Fatalf("cancel was not forwarded: frames=%v err=%v", frames, err)
	}
	if len(g.pending) != 1 || g.pending["other"] == nil || g.frames != 1 || g.bytes != len(other)+len("hel") {
		t.Fatalf("cancel accounting: pending=%d frames=%d bytes=%d", len(g.pending), g.frames, g.bytes)
	}
	frames, err = g.Client(websocket.TextMessage, []byte(`{"context_id":"other","transcript":"lo"}`))
	if err != nil || len(frames) != 2 {
		t.Fatalf("remaining context not released: frames=%v err=%v", frames, err)
	}
	if len(*seen) != 1 || (*seen)[0] != "hello" {
		t.Fatalf("checked units=%v", *seen)
	}
	if len(g.pending) != 0 || g.bytes != 0 || g.frames != 0 {
		t.Fatal("completed unit retained")
	}
}

// A cancel that also carries text is ambiguous: refuse it rather than forward
// unchecked text.
func TestSpeechGuardrailCartesiaCancelWithTextRefused(t *testing.T) {
	g, seen := speechInspector(t, "cartesia", "pre")
	frames, err := g.Client(websocket.TextMessage, []byte(`{"context_id":"turn","cancel":true,"transcript":"prohibited"}`))
	if err == nil || len(frames) != 0 || len(*seen) != 0 {
		t.Fatalf("cancel with text: frames=%v err=%v seen=%v", frames, err, *seen)
	}
}
