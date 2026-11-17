package reqopts

import "testing"

// A routing field no lane serves says so in words that promise no later
// date: "in this release", never "yet". The code is unchanged.
func TestAnUnsupportedRoutingFieldSaysInThisRelease(t *testing.T) {
	const field = "fixture_field_no_lane_serves"
	e := CheckSupported([]string{field})
	if e == nil {
		t.Fatal("an unregistered field was accepted")
	}
	if e.Code != CodeNotYetSupported {
		t.Fatalf("code %q, want %q", e.Code, CodeNotYetSupported)
	}
	if want := field + " is not supported by this gateway in this release"; e.Message != want {
		t.Fatalf("message %q, want %q", e.Message, want)
	}
}
