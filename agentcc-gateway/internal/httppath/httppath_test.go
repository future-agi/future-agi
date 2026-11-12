package httppath

import "testing"

func TestCanonical(t *testing.T) {
	tests := []struct {
		in, want  string
		canonical bool
	}{
		{"/", "/", true},
		{"/v1/videos/x", "/v1/videos/x", true},
		{"/v1/.well-known/agent.json", "/v1/.well-known/agent.json", true},
		{"/-/keys/k_1", "/-/keys/k_1", true},
		{"/v1/videos/x..y", "/v1/videos/x..y", true},

		{"//v1/videos/x", "/v1/videos/x", false},
		{"///v1/videos/x", "/v1/videos/x", false},
		{"/v1//videos/x", "/v1/videos/x", false},
		{"/v1/videos/x/", "/v1/videos/x", false},
		{"/v1/", "/v1", false},
		{"/./v1/videos/x", "/v1/videos/x", false},
		{"/v1/./videos/x", "/v1/videos/x", false},
		{"/v1/videos/x/.", "/v1/videos/x", false},
		{"/zz/../v1/videos/x", "/v1/videos/x", false},
		{"/../v1/videos/x", "/v1/videos/x", false},
		{"/v1/..", "/", false},
		{"/v1/../-/keys", "/-/keys", false},
		{"v1/videos/x", "/v1/videos/x", false},
		{"*", "/*", false},
		{"", "/", false},
	}
	for _, tt := range tests {
		if got := Canonical(tt.in); got != tt.want {
			t.Errorf("Canonical(%q) = %q, want %q", tt.in, got, tt.want)
		}
		if got := IsCanonical(tt.in); got != tt.canonical {
			t.Errorf("IsCanonical(%q) = %v, want %v", tt.in, got, tt.canonical)
		}
		// Canonical is idempotent and always yields a canonical path.
		if c := Canonical(tt.in); !IsCanonical(c) || Canonical(c) != c {
			t.Errorf("Canonical(%q) = %q is not itself canonical", tt.in, c)
		}
	}
}

func TestIsCanonicalDoesNotAllocate(t *testing.T) {
	p := "/v1/threads/thread_1/runs/run_1/steps/step_1"
	if n := testing.AllocsPerRun(100, func() { _ = IsCanonical(p) }); n != 0 {
		t.Fatalf("IsCanonical allocated %v times per call on a canonical path", n)
	}
}
