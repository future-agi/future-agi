package inclusive

import "testing"

// Upstream micros keep round()'s half-up rule and its 2^60 ceiling exactly.
func TestOwnedR8UpstreamMicrosEdges(t *testing.T) {
	for raw, want := range map[string]int64{
		"1152921504606.846976":                 1 << 60,
		"1152921504606.8469755":                1 << 60,
		"1152921504606.8469754999999999999999": 1<<60 - 1,
		"1152921504606.846975":                 1<<60 - 1,
		"0.0000004999999999999999999":          0,
		"0.0000005":                            1,
		"5e-7":                                 1,
		"4.9999e-7":                            0,
		"999999999999.9999995":                 1000000000000000000,
	} {
		w, err := lex(raw)
		if err != nil {
			t.Fatalf("%s lex err=%v", raw, err)
		}
		if got, err := w.micros(); err != nil || got != want {
			t.Fatalf("%s micros=%d err=%v want %d", raw, got, err, want)
		}
	}
	for _, raw := range []string{"1152921504606.8469765", "1152921504606.846977", "1e13", "99999999999999999999e-6"} {
		w, err := lex(raw)
		if err != nil {
			t.Fatalf("%s lex err=%v", raw, err)
		}
		if got, err := w.micros(); err == nil {
			t.Fatalf("%s micros=%d accepted beyond 2^60", raw, got)
		}
	}
}
