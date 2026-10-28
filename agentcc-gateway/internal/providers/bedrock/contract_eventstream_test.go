package bedrock

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Bedrock streams are binary AWS event-stream bodies, so a streaming contract
// fixture's response.eventstream is generated, never edited by hand. Its
// source is the response.eventstream.json beside it, a JSON array of the
// messages in order:
//
//	{"event": "messageStart", "payload": {"role": "assistant"}}
//	{"event": "chunk", "chunk": {"type": "message_start", ...}}
//	{"exception": "modelStreamErrorException", "payload": {"message": "..."}}
//
// "event" is the :event-type of an event message and "payload" its JSON body.
// "chunk" is shorthand for an InvokeModelWithResponseStream chunk: the
// generator base64-encodes the compact JSON of the Anthropic stream event into
// the payload {"bytes": "..."}, as Bedrock sends it. "exception" is the
// :exception-type of an exception message. Payloads are written as compact
// JSON in the key order of the source.
//
// TestEventStreamFixtures fails when a response.eventstream does not match its
// source. To change a stream fixture, edit the source, then regenerate the
// body and the goldens:
//
//	go test ./internal/providers/bedrock/ -run TestEventStreamFixtures -update
//	go test ./internal/providers/bedrock/ -run TestContract -update

// eventStreamSourceFile is the source of a case's response.eventstream.
const eventStreamSourceFile = contracttest.ResponseEventStreamFile + ".json"

type eventStreamSource struct {
	Event     string          `json:"event,omitempty"`
	Exception string          `json:"exception,omitempty"`
	Payload   json.RawMessage `json:"payload,omitempty"`
	Chunk     json.RawMessage `json:"chunk,omitempty"`
}

type eventStreamHeader struct{ name, value string }

// encodeEventStreamMessage frames one message: a prelude (total length,
// headers length, prelude CRC32), string-typed headers, the payload, and a
// CRC32 of everything before it, as the AWS event-stream encoding specifies.
func encodeEventStreamMessage(headers []eventStreamHeader, payload []byte) []byte {
	var hdrs bytes.Buffer
	for _, h := range headers {
		hdrs.WriteByte(byte(len(h.name)))
		hdrs.WriteString(h.name)
		hdrs.WriteByte(7) // header value type: string
		_ = binary.Write(&hdrs, binary.BigEndian, uint16(len(h.value)))
		hdrs.WriteString(h.value)
	}

	prelude := make([]byte, 8)
	binary.BigEndian.PutUint32(prelude[0:4], uint32(12+hdrs.Len()+len(payload)+4))
	binary.BigEndian.PutUint32(prelude[4:8], uint32(hdrs.Len()))

	var msg bytes.Buffer
	msg.Write(prelude)
	_ = binary.Write(&msg, binary.BigEndian, crc32.ChecksumIEEE(prelude))
	msg.Write(hdrs.Bytes())
	msg.Write(payload)
	_ = binary.Write(&msg, binary.BigEndian, crc32.ChecksumIEEE(msg.Bytes()))
	return msg.Bytes()
}

// encodeEventStreamSource renders a response.eventstream.json source.
func encodeEventStreamSource(src []byte) ([]byte, error) {
	var msgs []eventStreamSource
	dec := json.NewDecoder(bytes.NewReader(src))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&msgs); err != nil {
		return nil, err
	}
	var out bytes.Buffer
	for i, m := range msgs {
		var payload bytes.Buffer
		var headers []eventStreamHeader
		switch {
		case m.Event != "" && m.Exception == "":
			headers = []eventStreamHeader{{":event-type", m.Event}, {":content-type", "application/json"}, {":message-type", "event"}}
		case m.Exception != "" && m.Event == "":
			headers = []eventStreamHeader{{":exception-type", m.Exception}, {":content-type", "application/json"}, {":message-type", "exception"}}
		default:
			return nil, fmt.Errorf("message %d: set exactly one of event and exception", i)
		}
		switch {
		case len(m.Chunk) > 0 && len(m.Payload) == 0 && m.Event == "chunk":
			var inner bytes.Buffer
			if err := json.Compact(&inner, m.Chunk); err != nil {
				return nil, fmt.Errorf("message %d: %w", i, err)
			}
			b, err := json.Marshal(struct {
				Bytes []byte `json:"bytes"`
			}{inner.Bytes()})
			if err != nil {
				return nil, err
			}
			payload.Write(b)
		case len(m.Payload) > 0 && len(m.Chunk) == 0:
			if err := json.Compact(&payload, m.Payload); err != nil {
				return nil, fmt.Errorf("message %d: %w", i, err)
			}
		default:
			return nil, fmt.Errorf("message %d: set exactly one of payload and chunk (chunk only on a \"chunk\" event)", i)
		}
		out.Write(encodeEventStreamMessage(headers, payload.Bytes()))
	}
	return out.Bytes(), nil
}

// updateFlag reports whether -update was passed. contracttest defines the
// flag; reading it here lets one flag regenerate every contract artifact.
func updateFlag() bool {
	f := flag.Lookup("update")
	if f == nil {
		return false
	}
	g, ok := f.Value.(flag.Getter)
	if !ok {
		return false
	}
	on, _ := g.Get().(bool)
	return on
}

func TestEventStreamFixtures(t *testing.T) {
	sources, err := filepath.Glob(filepath.Join(contracttest.DefaultRoot, "*", "*", eventStreamSourceFile))
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) == 0 {
		t.Fatalf("no %s under %s", eventStreamSourceFile, contracttest.DefaultRoot)
	}
	for _, src := range sources {
		dir := filepath.Dir(src)
		t.Run(filepath.Base(filepath.Dir(dir))+"/"+filepath.Base(dir), func(t *testing.T) {
			raw, err := os.ReadFile(src)
			if err != nil {
				t.Fatal(err)
			}
			want, err := encodeEventStreamSource(raw)
			if err != nil {
				t.Fatalf("%s: %v", src, err)
			}
			body := filepath.Join(dir, contracttest.ResponseEventStreamFile)
			if updateFlag() {
				if err := os.WriteFile(body, want, 0o644); err != nil {
					t.Fatal(err)
				}
				return
			}
			got, err := os.ReadFile(body)
			if err != nil {
				t.Fatalf("%v (generate it with -update)", err)
			}
			if !bytes.Equal(got, want) {
				t.Errorf("%s does not match %s; regenerate it with -update", body, eventStreamSourceFile)
			}
		})
	}
}

// The adapter's decoder reads back what the generator writes, and every
// message CRC (which readEventStreamMessage does not check) is valid.
func TestEventStreamEncoderRoundTrip(t *testing.T) {
	src := []byte(`[
		{"event": "contentBlockDelta", "payload": {"delta": {"text": "hé"}, "contentBlockIndex": 0}},
		{"event": "chunk", "chunk": {"type": "message_stop"}},
		{"exception": "throttlingException", "payload": {"message": "slow down"}}
	]`)
	body, err := encodeEventStreamSource(src)
	if err != nil {
		t.Fatal(err)
	}
	want := []struct {
		kind, name, payload string
	}{
		{":event-type", "contentBlockDelta", `{"delta":{"text":"hé"},"contentBlockIndex":0}`},
		{":event-type", "chunk", `{"bytes":"eyJ0eXBlIjoibWVzc2FnZV9zdG9wIn0="}`},
		{":exception-type", "throttlingException", `{"message":"slow down"}`},
	}
	r := bytes.NewReader(body)
	for i, w := range want {
		start := len(body) - r.Len()
		msg, err := readEventStreamMessage(r)
		if err != nil || msg == nil {
			t.Fatalf("message %d: %v, %v", i, msg, err)
		}
		if msg.Headers[w.kind] != w.name || string(msg.Payload) != w.payload || msg.Headers[":content-type"] != "application/json" {
			t.Errorf("message %d = %v %s, want %s=%s %s", i, msg.Headers, msg.Payload, w.kind, w.name, w.payload)
		}
		end := len(body) - r.Len()
		frame := body[start:end]
		if got := binary.BigEndian.Uint32(frame[len(frame)-4:]); got != crc32.ChecksumIEEE(frame[:len(frame)-4]) {
			t.Errorf("message %d: bad message CRC", i)
		}
	}
	if msg, err := readEventStreamMessage(r); msg != nil || err != nil {
		t.Errorf("want EOF after the last message, got %v, %v", msg, err)
	}
}
