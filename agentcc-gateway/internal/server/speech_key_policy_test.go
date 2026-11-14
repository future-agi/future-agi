package server

import (
	"errors"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// A Monitor key policy never changes speech traffic: the relay installs no
// hold/refuse filter for it. Speak text is forwarded as it arrives (before any
// Flush), and a frame the guardrail parser would refuse still reaches the
// vendor and keeps the session open.
func TestSpeechMonitorKeyPolicyDoesNotFilter(t *testing.T) {
	f := newSpeechFixture(t, "deepgram", "pre", "key-policy-monitor", "block", nil)
	client, _, err := f.dial(t, "/deepgram/v1/speak?model=aura-2-thalia-en", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = client.Close() }()
	// No Flush: a hold filter would keep this until one arrives.
	if err := client.WriteMessage(websocket.TextMessage, []byte(`{"type":"Speak","text":"prohibited"}`)); err != nil {
		t.Fatal(err)
	}
	if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, data, err := client.ReadMessage(); err != nil || string(data) != `{"type":"ack"}` {
		t.Fatalf("Speak under a Monitor key policy must stream without Flush: data=%s err=%v", data, err)
	}
	// A binary frame is refused by the guardrail filter; with no filter it is relayed.
	if err := client.WriteMessage(websocket.BinaryMessage, []byte{1, 2, 3}); err != nil {
		t.Fatal(err)
	}
	if _, data, err := client.ReadMessage(); err != nil || string(data) != `{"type":"ack"}` {
		t.Fatalf("a Monitor key policy must not end the session: data=%s err=%v", data, err)
	}
}

// An Enforce key policy whose block check is pre-stage does not refuse the
// key's transcription sessions: audio has no text to check before the vendor,
// so the check is skipped there (it still applies to chat and synthesis).
// The same check configured org-wide keeps refusing, as before.
func TestSpeechEnforceKeyPolicySkipsUnreadableDirection(t *testing.T) {
	for _, tc := range []struct {
		name, vendor, stage, source, path string
		status                            int
	}{
		{"key pre-stage on deepgram listen", "deepgram", "pre", "key-policy-enforce", "/deepgram/v1/listen?encoding=linear16&sample_rate=16000", 101},
		{"key pre-stage on cartesia stt", "cartesia", "pre", "key-policy-enforce", "/cartesia/stt/websocket?encoding=pcm_s16le&sample_rate=16000", 101},
		{"key post-stage on cartesia turns", "cartesia", "post", "key-policy-enforce", "/cartesia/stt/turns/websocket?encoding=pcm_s16le&sample_rate=16000", 101},
		{"key post-stage on deepgram speak", "deepgram", "post", "key-policy-enforce", "/deepgram/v1/speak?model=aura-2-thalia-en", 101},
		{"org pre-stage on deepgram listen still refuses", "deepgram", "pre", "managed", "/deepgram/v1/listen?encoding=linear16&sample_rate=16000", 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newSpeechFixture(t, tc.vendor, tc.stage, tc.source, "block", nil)
			conn, resp, err := f.dial(t, tc.path, nil)
			if conn != nil {
				_ = conn.Close()
			}
			status := 0
			if resp != nil {
				status = resp.StatusCode
			}
			if status != tc.status {
				t.Fatalf("status=%d err=%v want=%d", status, err, tc.status)
			}
		})
	}
}

// An Enforce key policy still blocks in the direction the relay can read.
func TestSpeechEnforceKeyPolicyBlocksReadableDirection(t *testing.T) {
	f := newSpeechFixture(t, "deepgram", "pre", "key-policy-enforce", "block", nil)
	client, _, err := f.dial(t, "/deepgram/v1/speak?model=aura-2-thalia-en", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = client.Close() }()
	for _, m := range []string{`{"type":"Speak","text":"prohibited"}`, `{"type":"Flush"}`} {
		if err := client.WriteMessage(websocket.TextMessage, []byte(m)); err != nil {
			t.Fatal(err)
		}
	}
	if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	_, data, err := client.ReadMessage()
	var closeErr *websocket.CloseError
	if !errors.As(err, &closeErr) || closeErr.Code != 1008 || closeErr.Text != "realtime_guardrail_blocked" {
		t.Fatalf("enforce key policy must block synthesis text: data=%s err=%v", data, err)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.received) != 0 {
		t.Fatalf("blocked text reached the vendor: %v", f.received)
	}
}

// An Enforce key policy's warn and log checks do not run on speech relays:
// they could only write a log line there, yet any bound rule installs the
// hold/refuse filter. Interim transcripts stream and Speak text is not held.
func TestSpeechEnforceKeyPolicyNonBlockingChecksDoNotFilter(t *testing.T) {
	t.Run("warn post check on deepgram listen forwards interim transcripts", func(t *testing.T) {
		interim := `{"type":"Results","is_final":false,"channel":{"alternatives":[{"transcript":"prohibited interim"}]}}`
		f := newSpeechFixture(t, "deepgram", "post", "key-policy-enforce", "warn", []string{interim})
		client, _, err := f.dial(t, "/deepgram/v1/listen?encoding=linear16&sample_rate=16000", nil)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = client.Close() }()
		if err := client.WriteMessage(websocket.BinaryMessage, []byte{0, 0}); err != nil {
			t.Fatal(err)
		}
		if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
			t.Fatal(err)
		}
		if _, data, err := client.ReadMessage(); err != nil || string(data) != interim {
			t.Fatalf("interim transcript under a warn key check: data=%s err=%v", data, err)
		}
	})
	t.Run("log pre check on deepgram speak does not hold Speak text", func(t *testing.T) {
		f := newSpeechFixture(t, "deepgram", "pre", "key-policy-enforce", "log", nil)
		client, _, err := f.dial(t, "/deepgram/v1/speak?model=aura-2-thalia-en", nil)
		if err != nil {
			t.Fatal(err)
		}
		defer func() { _ = client.Close() }()
		if err := client.WriteMessage(websocket.TextMessage, []byte(`{"type":"Speak","text":"prohibited"}`)); err != nil {
			t.Fatal(err)
		}
		if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
			t.Fatal(err)
		}
		if _, data, err := client.ReadMessage(); err != nil || string(data) != `{"type":"ack"}` {
			t.Fatalf("Speak under a log key check must stream without Flush: data=%s err=%v", data, err)
		}
	})
}
