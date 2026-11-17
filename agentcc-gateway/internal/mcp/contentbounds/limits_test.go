package contentbounds

import "testing"

// Explicit architecture design fixtures, NOT production defaults. Inherited
// transport limits (including R19g 64 KiB) must still be supplied by callers.
func designLimits() Limits {
	return Limits{
		MaxReadBytes: 4 << 20, MaxItemBytes: 256 << 10,
		MaxTotalBytes: 8 << 20, MaxItems: 5000,
		MaxDepth: 32, MaxNodes: 50000,
	}
}

func requireCode(t *testing.T, err error, want Code) {
	t.Helper()
	if err == nil {
		t.Fatal("expected typed refusal")
	}
	e, ok := err.(*Error)
	if !ok || e.Code() != want || e.Error() != string(want) {
		t.Fatal("unexpected refusal class")
	}
}

func TestEffectiveLimitsPreservesEveryStricterBound(t *testing.T) {
	inherited := designLimits()
	inherited.MaxReadBytes = 65536
	inherited.MaxTotalBytes = 70000
	inherited.MaxNodes = 30
	requested := designLimits()
	requested.MaxItemBytes = 200
	requested.MaxItems = 10
	requested.MaxDepth = 3
	got, err := EffectiveLimits(inherited, requested)
	if err != nil {
		t.Fatal("valid limits refused")
	}
	want := Limits{65536, 200, 70000, 10, 3, 30}
	if got != want || inherited.MaxReadBytes != 65536 || requested.MaxReadBytes != 4<<20 {
		t.Fatal("effective limits did not preserve fieldwise minima")
	}
	other, err := EffectiveLimits(requested, inherited)
	if err != nil || other != got {
		t.Fatal("effective limits must be symmetric")
	}
}

func TestLimitsRejectMissingNegativeAndOverflow(t *testing.T) {
	setters := []func(*Limits, int64){
		func(l *Limits, v int64) { l.MaxReadBytes = v },
		func(l *Limits, v int64) { l.MaxItemBytes = v },
		func(l *Limits, v int64) { l.MaxTotalBytes = v },
		func(l *Limits, v int64) { l.MaxItems = v },
		func(l *Limits, v int64) { l.MaxDepth = v },
		func(l *Limits, v int64) { l.MaxNodes = v },
	}
	for _, set := range setters {
		for _, invalid := range []int64{0, -1, 1<<63 - 1, -1 << 63} {
			l := designLimits()
			set(&l, invalid)
			_, err := EffectiveLimits(designLimits(), l)
			requireCode(t, err, CodeInvalidLimits)
			_, err = EffectiveLimits(l, designLimits())
			requireCode(t, err, CodeInvalidLimits)
			_, err = NewAccumulator(l)
			requireCode(t, err, CodeInvalidLimits)
			_, err = Scan(nil, l)
			requireCode(t, err, CodeInvalidLimits)
		}
	}
	_, err := EffectiveLimits(Limits{}, Limits{})
	requireCode(t, err, CodeInvalidLimits)
}

func TestSmallAndLargestRepresentableSafeLimits(t *testing.T) {
	for _, n := range []int64{1, int64(^uint(0)>>1) - 1} {
		l := Limits{n, n, n, n, n, n}
		got, err := EffectiveLimits(l, l)
		if err != nil || got != l {
			t.Fatal("positive overflow-safe limits refused")
		}
	}
}
