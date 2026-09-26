package genaifmt

import (
	"bytes"
)

// Gemini repeats usageMetadata as the response grows and the final chunk
// carries the complete counts, so only the tail is worth keeping.
const maxUsageScanBytes = 16 << 10

// StreamUsageScanner collects token counts from a relayed SSE stream without
// buffering it. Priced at zero, a stream would be logged as if that were its
// real cost — worse than not billing it.
type StreamUsageScanner struct {
	tail []byte
}

// Write feeds a relayed chunk to the scanner.
func (s *StreamUsageScanner) Write(chunk []byte) {
	s.tail = append(s.tail, chunk...)
	if len(s.tail) > maxUsageScanBytes {
		s.tail = s.tail[len(s.tail)-maxUsageScanBytes:]
	}
}

// Usage returns the last counts reported — Gemini revises usageMetadata upward
// as it goes, so the final reading is the billable one.
func (s *StreamUsageScanner) Usage() (promptTokens, completionTokens int, ok bool) {
	lines := sseDataLines(s.tail)
	for i := len(lines) - 1; i >= 0; i-- {
		p, c, _ := ExtractUsageMetadata(lines[i])
		if p > 0 || c > 0 {
			return p, c, true
		}
	}
	return 0, 0, false
}

// Model returns the model named by the most recent chunk, if any.
func (s *StreamUsageScanner) Model() string {
	lines := sseDataLines(s.tail)
	for i := len(lines) - 1; i >= 0; i-- {
		if m := ExtractModelVersion(lines[i]); m != "" {
			return m
		}
	}
	return ""
}

// A retained window begins mid-stream, so a partial head line is expected and
// is dropped by the JSON parse.
func sseDataLines(buf []byte) [][]byte {
	var out [][]byte
	for _, line := range bytes.Split(buf, []byte("\n")) {
		line = bytes.TrimSpace(line)
		if bytes.HasPrefix(line, []byte("data:")) {
			line = bytes.TrimSpace(line[len("data:"):])
		}
		if len(line) > 0 && line[0] == '{' {
			out = append(out, line)
		}
	}
	return out
}
