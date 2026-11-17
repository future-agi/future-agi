package mcp

import (
	"net/url"
	"testing"
)

func TestW11ApprovalConsoleLink(t *testing.T) {
	id := "synthetic-sealed-id"
	denial := approvalRequired(w5Call("synthetic-org"), ApprovalRecord{ID: id, Status: "pending"})
	metadata := denial.Metadata.(map[string]any)
	if metadata["approve_url"] != "/dashboard/gateway/mcp?tab=approvals" {
		t.Fatalf("console link does not reach approvals: %v", metadata["approve_url"])
	}
	if metadata["poll_url"] != "/mcp/approvals/"+url.PathEscape(id) || metadata["approval_id"] != id {
		t.Fatal("console link changed the authoritative retry/poll identity")
	}
}
