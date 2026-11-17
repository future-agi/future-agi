package telemetry

import "testing"

func TestParentFromMetaThenHeader(t *testing.T) {
	meta := "00-11111111111111111111111111111111-2222222222222222-01"
	header := "00-33333333333333333333333333333333-4444444444444444-00"
	for _, tc := range []struct{ meta, header, want string }{{meta, header, meta}, {"invalid", header, header}, {"", header, header}, {"invalid", "invalid", ""}, {"00-00000000000000000000000000000000-2222222222222222-01", header, header}, {"00-11111111111111111111111111111111-0000000000000000-01", header, header}, {meta + "-extra", header, header}} {
		if got := Parent(tc.meta, tc.header); got != tc.want {
			t.Error("parent precedence or validation differs")
		}
	}
}
