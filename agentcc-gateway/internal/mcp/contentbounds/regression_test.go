package contentbounds

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// This opt-in test-only mutant operates on tiny constant fixtures. Run ONLY
// this test with CONTENTBOUNDS_TEST_UNBOUNDED=1 to obtain a semantic RED: the
// same refusal assertions must fail. No production code reads this variable.
// A compile failure is not RED. The default invocation exercises real Scan.
func TestSemanticRefusalWitness(t *testing.T) {
	cases := []struct {
		name, input string
		limits      Limits
		want        Code
	}{
		{"bytes", `"abcd"`, Limits{5, 20, 20, 10, 10, 20}, CodeReadBytes},
		{"depth", `[[[]]]`, Limits{100, 100, 100, 10, 2, 20}, CodeDepth},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var err error
			if os.Getenv("CONTENTBOUNDS_TEST_UNBOUNDED") == "1" {
				if !json.Valid([]byte(tc.input)) {
					t.Fatal("mutant fixture must be valid JSON")
				}
				// Deliberately accepts structurally valid but over-limit JSON.
			} else {
				_, err = Scan(strings.NewReader(tc.input), tc.limits)
			}
			requireCode(t, err, tc.want)
		})
	}
}
