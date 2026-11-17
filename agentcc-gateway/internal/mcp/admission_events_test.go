package mcp

import (
	"strings"
	"testing"
)

func TestAdmissionMetadataStreamCannotReplayContent(t *testing.T) {
	for _, raw := range []string{
		`{"jsonrpc":"2.0","method":"notifications/resources/list_changed","params":{"content":"private"}}`,
		`{"jsonrpc":"2.0","method":"notifications/message","params":{"data":"private"}}`,
		`{"jsonrpc":"2.0","id":1,"result":{"content":"private"}}`,
		`{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}`,
		strings.Repeat("x", 65537),
	} {
		if _, err := admissionMetadataEvent([]byte(raw)); err != errAdmissionDenied {
			t.Fatal("content entered metadata-authorized stream")
		}
	}
	for _, method := range []string{"notifications/resources/list_changed", "notifications/prompts/list_changed"} {
		out, err := admissionMetadataEvent([]byte(`{"jsonrpc":"2.0","method":"` + method + `","params":{"ignored":"private"},"params":{}}`))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(out), "private") || strings.Contains(string(out), "params") {
			t.Fatal("original value escaped closed projection")
		}
	}
}
