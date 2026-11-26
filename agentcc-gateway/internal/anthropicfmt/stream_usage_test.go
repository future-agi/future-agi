package anthropicfmt

import (
	"strings"
	"testing"
)

// A real Anthropic stream: input tokens stated once in message_start, output
// revised upward until message_delta.
const anthropicStream = `event: message_start
data: {"type":"message_start","message":{"id":"msg_1","model":"claude-3-5-sonnet","usage":{"input_tokens":41,"output_tokens":1}}}

event: content_block_delta
data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":87}}

event: message_stop
data: {"type":"message_stop"}
`

func TestStreamUsageScanner(t *testing.T) {
	var s StreamUsageScanner
	s.Write([]byte(anthropicStream))

	in, out, ok := s.Usage()
	if !ok {
		t.Fatal("no usage found in a stream that reports it")
	}
	if in != 41 {
		t.Errorf("input = %d, want 41 (from message_start)", in)
	}
	if out != 87 {
		t.Errorf("output = %d, want 87 (from message_delta, not message_start's 1)", out)
	}
	if got := s.Model(); got != "claude-3-5-sonnet" {
		t.Errorf("model = %q, want claude-3-5-sonnet", got)
	}
}

// The relay hands over arbitrary byte slices, so events arrive split.
func TestStreamUsageScannerAcrossChunkBoundaries(t *testing.T) {
	var s StreamUsageScanner
	for i := 0; i < len(anthropicStream); i += 7 {
		end := i + 7
		if end > len(anthropicStream) {
			end = len(anthropicStream)
		}
		s.Write([]byte(anthropicStream[i:end]))
	}

	in, out, ok := s.Usage()
	if !ok || in != 41 || out != 87 {
		t.Errorf("in=%d out=%d ok=%v, want 41/87/true when fed in 7-byte pieces", in, out, ok)
	}
}

// A long response pushes message_start out of the retained head, but the
// billable output count is at the other end and must survive.
func TestStreamUsageScannerKeepsOutputOnLongStream(t *testing.T) {
	var s StreamUsageScanner
	s.Write([]byte(anthropicStream[:strings.Index(anthropicStream, "event: content_block_delta")]))
	filler := "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"text\":\"" +
		strings.Repeat("x", 200) + "\"}}\n\n"
	for i := 0; i < 200; i++ {
		s.Write([]byte(filler))
	}
	s.Write([]byte("event: message_delta\ndata: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":512}}\n\n"))

	_, out, ok := s.Usage()
	if !ok || out != 512 {
		t.Errorf("output = %d ok=%v, want 512 after the head aged out", out, ok)
	}
}

func TestStreamUsageScannerNoUsage(t *testing.T) {
	var s StreamUsageScanner
	s.Write([]byte("event: ping\ndata: {\"type\":\"ping\"}\n\n"))

	if _, _, ok := s.Usage(); ok {
		t.Error("reported usage for a stream that carried none — cost must skip, not bill zero")
	}
}

func TestStreamUsageScannerIgnoresGarbage(t *testing.T) {
	var s StreamUsageScanner
	s.Write([]byte("data: not-json\ndata: {broken\n\n"))

	if _, _, ok := s.Usage(); ok {
		t.Error("parsed usage out of unparseable data")
	}
}
