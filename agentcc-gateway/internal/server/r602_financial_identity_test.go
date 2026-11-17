package server

import "testing"

func TestR602SharedFinancialIdentityContract(t *testing.T) {
	g, l, _ := r601Gateway(t)
	answer := r601Response("0.0000095", false)
	g.managed.responseWith.Store(&answer)
	w := g.chat(t, `{"model":"google/gemini-3.5-flash","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, nil)
	rows := l.Outbox("org-m")
	if w.Code != 200 || len(rows) != 1 {
		t.Fatalf("status=%d rows=%d", w.Code, len(rows))
	}
	rec := l.OwnedSettlement("org-m", rows[0]["request_id"])
	if rows[0]["upstream_key_id"] != "" || rec == nil || rec.UpstreamKeyID != "" {
		t.Fatal("shared fingerprint changed v1 upstream_key_id semantics")
	}
	if len(rec.InclusiveBindings) != 1 {
		t.Fatal("private reconciliation binding missing")
	}
	for _, id := range rec.InclusiveBindings {
		if id.Scope != "shared" || id.Credential == "" {
			t.Fatal("private shared identity missing")
		}
	}
}
