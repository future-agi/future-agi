package anthropicfmt

import "testing"

func TestExtractUsage(t *testing.T) {
	tests := []struct {
		name       string
		body       string
		wantInput  int
		wantOutput int
	}{
		{
			name:       "no caching",
			body:       `{"usage":{"input_tokens":10,"output_tokens":5}}`,
			wantInput:  10,
			wantOutput: 5,
		},
		{
			name:       "with prompt caching",
			body:       `{"usage":{"input_tokens":10,"cache_creation_input_tokens":100,"cache_read_input_tokens":900,"output_tokens":5}}`,
			wantInput:  1010,
			wantOutput: 5,
		},
		{
			name:       "malformed body",
			body:       `not json`,
			wantInput:  0,
			wantOutput: 0,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			in, out := ExtractUsage([]byte(tt.body))
			if in != tt.wantInput {
				t.Errorf("inputTokens = %d, want %d", in, tt.wantInput)
			}
			if out != tt.wantOutput {
				t.Errorf("outputTokens = %d, want %d", out, tt.wantOutput)
			}
		})
	}
}
