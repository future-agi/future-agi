package logging

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// credentialHeaderCases is api_contracts/gateway/credential-headers.json, the
// case table the gateway and Django's request-log ingestion both test
// against.
type credentialHeaderCases struct {
	Credential []string `json:"credential"`
	Plain      []string `json:"plain"`
}

func loadCredentialHeaderCases(t *testing.T) credentialHeaderCases {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "credential-headers.json"))
	if err != nil {
		t.Fatal(err)
	}
	var c credentialHeaderCases
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	if len(c.Credential) == 0 || len(c.Plain) == 0 {
		t.Fatal("the case table names no headers")
	}
	return c
}

// The request log masks the value of every header the case table
// names as a credential (the named ones, and any whose name reads as one),
// and keeps every plain header as sent.
func TestSanitizeHeaders_MasksEveryCredentialHeader(t *testing.T) {
	cases := loadCredentialHeaderCases(t)
	h := map[string][]string{}
	for _, name := range cases.Credential {
		h[name] = []string{"planted-" + name}
	}
	for _, name := range cases.Plain {
		h[name] = []string{"plain-" + name}
	}

	got := sanitizeHeaders(h)

	for _, name := range cases.Credential {
		if v := got[name]; len(v) != 1 || v[0] != "***" {
			t.Errorf("%s = %q, want it masked as ***", name, v)
		}
	}
	for _, name := range cases.Plain {
		if v := got[name]; len(v) != 1 || v[0] != "plain-"+name {
			t.Errorf("%s = %q, want it kept as sent", name, v)
		}
	}
}
