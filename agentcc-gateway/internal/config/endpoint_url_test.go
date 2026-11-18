package config

import "testing"

func TestJoinEndpoint(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		prefix  string
		path    string
		want    string
	}{
		{
			name: "default prefix", baseURL: "https://api.openai.com", prefix: "/v1",
			path: "/v1/chat/completions", want: "https://api.openai.com/v1/chat/completions",
		},
		{
			// The reported bug: Perplexity's Sonar API has no version segment.
			name: "no prefix", baseURL: "https://api.perplexity.ai", prefix: "",
			path: "/v1/chat/completions", want: "https://api.perplexity.ai/chat/completions",
		},
		{
			name: "custom prefix", baseURL: "https://api.deepinfra.com", prefix: "/v1/openai",
			path: "/v1/chat/completions", want: "https://api.deepinfra.com/v1/openai/chat/completions",
		},
		{
			// The case endpoint() was written for; must not become /v1/v1/.
			name: "base already carries the prefix", baseURL: "https://api.cohere.ai/compatibility/v1",
			prefix: "/v1", path: "/v1/chat/completions",
			want: "https://api.cohere.ai/compatibility/v1/chat/completions",
		},
		{
			name: "base with a path but no version", baseURL: "https://api.groq.com/openai",
			prefix: "/v1", path: "/v1/chat/completions",
			want: "https://api.groq.com/openai/v1/chat/completions",
		},
		{
			name: "trailing slash on base", baseURL: "https://api.openai.com/", prefix: "/v1",
			path: "/v1/models", want: "https://api.openai.com/v1/models",
		},
		{
			// A suffix match must respect the separator, or /apiv1 false-matches.
			name: "lookalike suffix is not a match", baseURL: "https://api.example.com/apiv1",
			prefix: "/v1", path: "/v1/models",
			want: "https://api.example.com/apiv1/v1/models",
		},
		{
			// Paths are relative to the API root, so an unversioned one still
			// gets the prefix. The proxy handlers pass "/v1/threads/..." and
			// rely on the strip-and-reapply above; this covers the other form.
			name: "unversioned path still gets the prefix", baseURL: "https://api.openai.com",
			prefix: "/v1", path: "/threads/abc/runs",
			want: "https://api.openai.com/v1/threads/abc/runs",
		},
		{
			// The proxy path under a prefix-less provider.
			name: "proxy path under an unversioned provider", baseURL: "https://api.perplexity.ai",
			prefix: "", path: "/v1/assistants",
			want: "https://api.perplexity.ai/assistants",
		},
		{
			// The "/v1" strip is a whole segment, not a string prefix. Without
			// the boundary check this returned ".../v20/models".
			name: "versioned path is not eaten by the strip", baseURL: "https://up.test",
			prefix: "/v2", path: "/v10/models",
			want: "https://up.test/v2/v10/models",
		},
		{
			// Same trap with a non-numeric suffix, e.g. a Gemini-style path.
			name: "v1beta path is not eaten by the strip", baseURL: "https://up.test",
			prefix: "/v2", path: "/v1beta/models",
			want: "https://up.test/v2/v1beta/models",
		},
		{
			// A bare "/v1" is a whole segment and is stripped.
			name: "bare version path collapses onto the prefix", baseURL: "https://up.test",
			prefix: "/v1", path: "/v1",
			want: "https://up.test/v1",
		},
		{
			// The prefix always carries a leading slash, so HasSuffix already
			// matches on a separator. Pins that "/superv1" cannot false-match.
			name: "lookalike suffix without a separator is not a match", baseURL: "https://api.example.com/superv1",
			prefix: "/v1", path: "/v1/models",
			want: "https://api.example.com/superv1/v1/models",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := JoinEndpoint(tt.baseURL, tt.prefix, tt.path); got != tt.want {
				t.Errorf("JoinEndpoint(%q, %q, %q)\n got %q\nwant %q", tt.baseURL, tt.prefix, tt.path, got, tt.want)
			}
		})
	}
}

func TestEffectiveAPIPathPrefix(t *testing.T) {
	str := func(s string) *string { return &s }

	tests := []struct {
		name string
		set  *string
		want string
	}{
		{name: "unset defaults to /v1", set: nil, want: "/v1"},
		{name: "explicit empty means no version segment", set: str(""), want: ""},
		{name: "missing leading slash is added", set: str("v1"), want: "/v1"},
		{name: "trailing slash is dropped", set: str("/v1/"), want: "/v1"},
		{name: "whitespace tolerated", set: str("  /v1  "), want: "/v1"},
		{name: "multi-segment kept", set: str("/v1/openai"), want: "/v1/openai"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			c := &ProviderConfig{APIPathPrefix: tt.set}
			if got := c.EffectiveAPIPathPrefix(); got != tt.want {
				t.Errorf("= %q, want %q", got, tt.want)
			}
		})
	}
}
