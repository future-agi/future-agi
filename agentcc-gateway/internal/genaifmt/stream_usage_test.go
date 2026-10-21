package genaifmt

import "testing"

// Gemini repeats usageMetadata as the response grows; the last one is billable.
const geminiStream = `data: {"candidates":[{"content":{"parts":[{"text":"He"}]}}],"modelVersion":"gemini-2.0-flash","usageMetadata":{"promptTokenCount":9,"candidatesTokenCount":1,"totalTokenCount":10}}

data: {"candidates":[{"content":{"parts":[{"text":"llo"}]}}],"modelVersion":"gemini-2.0-flash","usageMetadata":{"promptTokenCount":9,"candidatesTokenCount":48,"thoughtsTokenCount":12,"totalTokenCount":69}}
`

func TestStreamUsageScanner(t *testing.T) {
	var s StreamUsageScanner
	s.Write([]byte(geminiStream))

	prompt, completion, ok := s.Usage()
	if !ok {
		t.Fatal("no usage found in a stream that reports it")
	}
	if prompt != 9 {
		t.Errorf("prompt = %d, want 9", prompt)
	}
	// Thinking tokens bill as output, so the last chunk is 48+12.
	if completion != 60 {
		t.Errorf("completion = %d, want 60 (48 candidates + 12 thoughts)", completion)
	}
	if got := s.Model(); got != "gemini-2.0-flash" {
		t.Errorf("model = %q, want gemini-2.0-flash", got)
	}
}

func TestStreamUsageScannerAcrossChunkBoundaries(t *testing.T) {
	var s StreamUsageScanner
	for i := 0; i < len(geminiStream); i += 11 {
		end := i + 11
		if end > len(geminiStream) {
			end = len(geminiStream)
		}
		s.Write([]byte(geminiStream[i:end]))
	}

	prompt, completion, ok := s.Usage()
	if !ok || prompt != 9 || completion != 60 {
		t.Errorf("prompt=%d completion=%d ok=%v, want 9/60/true", prompt, completion, ok)
	}
}

func TestStreamUsageScannerNoUsage(t *testing.T) {
	var s StreamUsageScanner
	s.Write([]byte(`data: {"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}` + "\n\n"))

	if _, _, ok := s.Usage(); ok {
		t.Error("reported usage for a stream that carried none — cost must skip, not bill zero")
	}
}
