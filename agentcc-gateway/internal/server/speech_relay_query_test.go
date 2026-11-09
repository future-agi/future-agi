package server

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/gorilla/websocket"
)

// A relayed session is checked and metered by the query parameters the gateway reads (model,
// encoding, sample_rate, channels), and the vendor receives the query the gateway parsed. A
// query that names one of those parameters more than once is refused with 400
// ambiguous_parameter before the vendor is dialed, so the vendor never receives a value of
// them the gateway did not check and meter. The same query with each named once opens.
func TestSpeechRelayRefusesAQueryThatRepeatsAParameterTheGatewayReads(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	cases := []struct{ path, key string }{
		// A key limited to deepgram/nova-3 naming a second model.
		{"/deepgram/v1/listen?model=nova-3&model=nova-2-medical&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=48000&sample_rate=8000", defaultModelKeyOpen},
		{"/deepgram/v1/listen?model=nova-3&encoding=linear16&encoding=mulaw&sample_rate=16000", defaultModelKeyOpen},
		{"/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&channels=2&channels=1", defaultModelKeyOpen},
		{"/deepgram/v1/speak?model=aura-2-thalia-en&model=aura-2-zeus-en", defaultModelKeyOpen},
		{"/cartesia/stt/websocket?model=ink-whisper&model=ink-2&encoding=pcm_s16le&sample_rate=16000", defaultModelKeyOpen},
	}
	for _, tc := range cases {
		status, code := openForError(t, g, tc.path, tc.key)
		if status != http.StatusBadRequest || code != "ambiguous_parameter" {
			t.Errorf("%s: status %d code %q, want 400 ambiguous_parameter", tc.path, status, code)
		}
	}
	g.mu.Lock()
	dialed := append([]string(nil), g.dialed...)
	g.mu.Unlock()
	if len(dialed) != 0 {
		t.Fatalf("the vendor was dialed for %q, want never", dialed)
	}

	// Each parameter named once: the session opens and the vendor gets exactly that query.
	if status, _ := g.open(t, "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&channels=2", defaultModelKeyListed); status != http.StatusSwitchingProtocols {
		t.Fatalf("the same session with each parameter once: status %d, want 101", status)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.dialed) != 1 || g.dialed[0] != "/v1/listen?channels=2&encoding=linear16&model=nova-3&sample_rate=16000" {
		t.Errorf("the vendor was dialed for %q, want the one query the gateway checked", g.dialed)
	}
}

// A query that names model, encoding, sample_rate or channels in another spelling (Model,
// %4Dodel, MODEL, Sample_Rate) is refused with 400 ambiguous_parameter before the vendor is
// dialed, as a repeat is, whatever the key: a key limited to deepgram/nova-3 cannot send the
// vendor Model=nova-2-medical beside model=nova-3. A spelled-out repeat (mod%65l) and an empty
// second value are refused too. A parameter the gateway does not read travels as sent.
func TestSpeechRelayRefusesAParameterTheGatewayReadsInAnotherSpelling(t *testing.T) {
	g := newSpeechDefaultModelGateway(t)
	cases := []struct{ path, key string }{
		{"/deepgram/v1/listen?model=nova-3&Model=nova-2-medical&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?model=nova-3&%4Dodel=nova-2-medical&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?model=nova-3&Model=&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?model=nova-3&mod%65l=nova-2-medical&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?model=nova-3&model=&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?Model=nova-2-medical&encoding=linear16&sample_rate=16000", defaultModelKeyListed},
		{"/deepgram/v1/listen?Model=nova-2-medical&encoding=linear16&sample_rate=16000", defaultModelKeyOpen},
		{"/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&Sample_Rate=8000", defaultModelKeyOpen},
		{"/deepgram/v1/speak?model=aura-2-thalia-en&MODEL=aura-2-zeus-en", defaultModelKeyOpen},
		{"/cartesia/stt/websocket?model=ink-whisper&Model=ink-2&encoding=pcm_s16le&sample_rate=16000", defaultModelKeyOpen},
	}
	for _, tc := range cases {
		status, code := openForError(t, g, tc.path, tc.key)
		if status != http.StatusBadRequest || code != "ambiguous_parameter" {
			t.Errorf("%s: status %d code %q, want 400 ambiguous_parameter", tc.path, status, code)
		}
	}
	g.mu.Lock()
	dialed := append([]string(nil), g.dialed...)
	g.mu.Unlock()
	if len(dialed) != 0 {
		t.Fatalf("the vendor was dialed for %q, want never", dialed)
	}

	// A parameter the gateway does not read, in any spelling: the session opens and it travels.
	if status, _ := g.open(t, "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&Punctuate=true", defaultModelKeyListed); status != http.StatusSwitchingProtocols {
		t.Fatalf("the session with an unread parameter: status %d, want 101", status)
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.dialed) != 1 || g.dialed[0] != "/v1/listen?Punctuate=true&encoding=linear16&model=nova-3&sample_rate=16000" {
		t.Errorf("the vendor was dialed for %q, want the query as the gateway checked it", g.dialed)
	}
}

// openForError dials path with key and returns the handshake status and, for a refusal, the
// error code of its JSON body.
func openForError(t *testing.T, g *speechDefaultModelGateway, path, key string) (int, string) {
	t.Helper()
	hdr := http.Header{"Authorization": {"Token " + key}}
	if strings.HasPrefix(path, "/cartesia/") {
		hdr = http.Header{"X-API-Key": {key}}
	}
	conn, resp, err := websocket.DefaultDialer.Dial(g.base+path, hdr)
	if conn != nil {
		conn.Close()
	}
	if resp == nil {
		t.Fatalf("%s: no handshake response: %v", path, err)
	}
	var body struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	raw, _ := io.ReadAll(resp.Body)
	_ = json.Unmarshal(raw, &body)
	return resp.StatusCode, body.Error.Code
}
