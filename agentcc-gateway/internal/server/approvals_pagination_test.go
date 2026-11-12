package server

import (
	"encoding/json"
	"net/url"
	"testing"
)

func TestW11AdminApprovalPaginationAndDetail(t *testing.T) {
	f := w5Gateway(t)
	path := "/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X"
	session := f.session(t, path, "w5-owner")
	first := f.call(path, "w5-owner", session, "")
	id := w5ApprovalID(t, first)
	detail := "/-/mcp/approvals/" + url.PathEscape(id)
	if w := serve(f.s, "GET", detail+"?org_id="+f.org, "", ""); w.Code != 401 {
		t.Fatalf("unauthenticated detail=%d", w.Code)
	}
	w := serve(f.s, "GET", detail+"?org_id="+f.org, "", "Bearer mcp-admin")
	if w.Code != 200 {
		t.Fatalf("detail=%d %s", w.Code, w.Body.String())
	}
	if w := serve(f.s, "GET", detail+"?org_id=foreign", "", "Bearer mcp-admin"); w.Code != 404 {
		t.Fatalf("foreign detail=%d", w.Code)
	}
	for _, query := range []string{"limit=0", "limit=1001", "limit=bad", "cursor=bad"} {
		w := serve(f.s, "GET", "/-/mcp/approvals?org_id="+f.org+"&"+query, "", "Bearer mcp-admin")
		if w.Code != 400 {
			t.Fatalf("invalid %s=%d", query, w.Code)
		}
	}
	w = serve(f.s, "GET", "/-/mcp/approvals?org_id="+f.org+"&limit=1", "", "Bearer mcp-admin")
	var page map[string]json.RawMessage
	if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &page) != nil || page["next_cursor"] == nil {
		t.Fatalf("page=%d %s", w.Code, w.Body.String())
	}
}
