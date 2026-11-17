package otel

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The trace export's header denylist is the gateway's one list of
// credential headers (the request log masks the same ones). No allowlist,
// however wide, exports a header the case table names as a credential, and
// a plain one is exported when allowlisted.
func TestHeaderMatcher_NeverExportsACredentialHeader(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "credential-headers.json"))
	if err != nil {
		t.Fatal(err)
	}
	var cases struct {
		Credential []string `json:"credential"`
		Plain      []string `json:"plain"`
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	m := newHeaderMatcher([]string{"*"})
	for _, name := range cases.Credential {
		if m.match(strings.ToLower(name)) {
			t.Errorf("%s is exported under the allowlist *", name)
		}
	}
	for _, name := range cases.Plain {
		if !m.match(strings.ToLower(name)) {
			t.Errorf("%s is not exported under the allowlist *", name)
		}
	}
}
