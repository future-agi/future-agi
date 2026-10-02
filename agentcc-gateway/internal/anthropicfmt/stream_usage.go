package anthropicfmt

import (
	"bytes"
	"encoding/json"
)

// An Anthropic stream carries its counts at the two ends — input in
// message_start, output in message_delta — so both ends are enough, and
// buffering whole responses to bill them would not be.
const maxUsageScanBytes = 16 << 10

// StreamUsageScanner collects token counts from a relayed SSE stream without
// buffering it. Priced at zero, a stream would be logged as if that were its
// real cost — worse than not billing it.
type StreamUsageScanner struct {
	head    []byte
	tail    []byte
	sawHead bool
}

// Write feeds a relayed chunk in. An unparseable stream yields no usage, which
// the caller can tell apart from a genuine zero.
func (s *StreamUsageScanner) Write(chunk []byte) {
	if !s.sawHead {
		room := maxUsageScanBytes - len(s.head)
		if room > 0 {
			if len(chunk) < room {
				room = len(chunk)
			}
			s.head = append(s.head, chunk[:room]...)
		}
		if len(s.head) >= maxUsageScanBytes {
			s.sawHead = true
		}
	}

	s.tail = append(s.tail, chunk...)
	if len(s.tail) > maxUsageScanBytes {
		s.tail = s.tail[len(s.tail)-maxUsageScanBytes:]
	}
}

// Usage returns the counts seen. input_tokens comes from the first usage object
// and output_tokens from the last: Anthropic states input once, then revises
// output as the message grows.
func (s *StreamUsageScanner) Usage() (inputTokens, outputTokens int, ok bool) {
	if in, found := firstUsageField(s.head, "input_tokens"); found {
		inputTokens, ok = in, true
	}
	if out, found := lastUsageField(s.tail, "output_tokens"); found {
		outputTokens, ok = out, true
	}
	return inputTokens, outputTokens, ok
}

// Model returns the model from message_start, if still in the retained head.
func (s *StreamUsageScanner) Model() string {
	for _, data := range sseDataLines(s.head) {
		var ev struct {
			Message struct {
				Model string `json:"model"`
			} `json:"message"`
		}
		if err := json.Unmarshal(data, &ev); err == nil && ev.Message.Model != "" {
			return ev.Message.Model
		}
	}
	return ""
}

// usage is nested one level deeper on message_start than on message_delta.
type usageEvent struct {
	Usage struct {
		InputTokens  *int `json:"input_tokens"`
		OutputTokens *int `json:"output_tokens"`
	} `json:"usage"`
	Message struct {
		Usage struct {
			InputTokens  *int `json:"input_tokens"`
			OutputTokens *int `json:"output_tokens"`
		} `json:"usage"`
	} `json:"message"`
}

func firstUsageField(buf []byte, field string) (int, bool) {
	for _, data := range sseDataLines(buf) {
		if v, ok := usageField(data, field); ok {
			return v, true
		}
	}
	return 0, false
}

func lastUsageField(buf []byte, field string) (int, bool) {
	lines := sseDataLines(buf)
	for i := len(lines) - 1; i >= 0; i-- {
		if v, ok := usageField(lines[i], field); ok {
			return v, true
		}
	}
	return 0, false
}

func usageField(data []byte, field string) (int, bool) {
	var ev usageEvent
	if err := json.Unmarshal(data, &ev); err != nil {
		return 0, false
	}
	for _, candidate := range [][2]*int{
		{ev.Usage.InputTokens, ev.Usage.OutputTokens},
		{ev.Message.Usage.InputTokens, ev.Message.Usage.OutputTokens},
	} {
		v := candidate[0]
		if field == "output_tokens" {
			v = candidate[1]
		}
		if v != nil {
			return *v, true
		}
	}
	return 0, false
}

// A retained window starts mid-stream, so a partial line at either edge is
// expected and is dropped by the JSON parse.
func sseDataLines(buf []byte) [][]byte {
	var out [][]byte
	for _, line := range bytes.Split(buf, []byte("\n")) {
		line = bytes.TrimSpace(line)
		if !bytes.HasPrefix(line, []byte("data:")) {
			continue
		}
		payload := bytes.TrimSpace(line[len("data:"):])
		if len(payload) > 0 && payload[0] == '{' {
			out = append(out, payload)
		}
	}
	return out
}
