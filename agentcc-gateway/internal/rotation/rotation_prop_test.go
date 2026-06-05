package rotation

import (
	"os"
	"strings"
	"testing"

	"github.com/leanovate/gopter"
	"github.com/leanovate/gopter/gen"
	"github.com/leanovate/gopter/prop"
)

// ---------------------------------------------------------------------------
// Properties: maskKey
// ---------------------------------------------------------------------------

func TestProp_MaskKey_EmptyReturnsEmpty(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("empty key stays empty", prop.ForAll(
		func(_ string) bool {
			return maskKey("") == ""
		},
		gen.Const(""),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// shortKeyGen generates non-empty strings of 1–8 ASCII chars.
var shortKeyGen = gen.RegexMatch(`[a-zA-Z0-9\-_.]{1,8}`)

// longKeyGen generates strings of 9–50 ASCII chars (guaranteed > 8 bytes).
var longKeyGen = gen.RegexMatch(`[a-zA-Z0-9\-_.]{9,50}`)

func TestProp_MaskKey_ShortKeysBecome3Stars(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("keys 1–8 chars become ***", prop.ForAll(
		func(key string) bool { return maskKey(key) == "***" },
		shortKeyGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

func TestProp_MaskKey_LongKeyOutputIsFixed11(t *testing.T) {
	properties := gopter.NewProperties(nil)

	// maskKey always returns key[:4] + "..." + key[len-4:] for long keys — exactly 11 chars.
	properties.Property("long-key output is exactly 11 chars (4+...+4)", prop.ForAll(
		func(key string) bool { return len(maskKey(key)) == 11 },
		longKeyGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

func TestProp_MaskKey_OutputContainsEllipsis(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("masked long keys always contain '...'", prop.ForAll(
		func(key string) bool { return strings.Contains(maskKey(key), "...") },
		longKeyGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

func TestProp_MaskKey_NonEmpty(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("non-empty key always produces non-empty mask", prop.ForAll(
		func(key string) bool {
			if key == "" {
				return true
			}
			return maskKey(key) != ""
		},
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// ---------------------------------------------------------------------------
// Properties: KeyState.MaskedState
// ---------------------------------------------------------------------------

func TestProp_MaskedState_PrimaryAlwaysMasked(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("MaskedState primary is never the full original for long keys", prop.ForAll(
		func(primary string) bool {
			if len(primary) <= 8 {
				return true // short keys become "***" which is fine
			}
			ks := &KeyState{Primary: primary, Status: StatusIdle}
			masked := ks.MaskedState()
			return masked.Primary != primary
		},
		gen.AnyString().SuchThat(func(s string) bool { return len(s) > 8 }),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

func TestProp_MaskedState_EmptyKeyStaysEmpty(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("empty OldKey stays empty after masking", prop.ForAll(
		func(primary string) bool {
			ks := &KeyState{Primary: primary, OldKey: "", Status: StatusIdle}
			masked := ks.MaskedState()
			return masked.OldKey == ""
		},
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}
