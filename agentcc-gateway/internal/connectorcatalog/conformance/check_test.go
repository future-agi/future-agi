package conformance

import (
	"encoding/json"
	"strings"
	"testing"
)

const (
	remoteCase = "l3-fixtures/acme/remote"
	localCase  = "l3-fixtures/acme/local"
	legacyCase = "l3-fixtures/acme/legacy"
	linearCase = "cn1-example/linear/linear-mcp"
)

func TestCheckPassesConformingEvidence(t *testing.T) {
	plan := mustPlan(t)
	rep := Check(plan, goodEvidence(t, plan), "2026-10-20")
	if rep.Failed() {
		t.Fatalf("conforming evidence failed rows:\n%s", joined(failedRows(rep)))
	}
	skips := 0
	for _, cs := range plan.Cases {
		for _, r := range cs.Rows {
			if r.Skip != "" {
				skips++
			}
		}
	}
	if rep.Skip != skips || rep.Pass == 0 {
		t.Fatalf("report counts pass %d, skip %d; the plan names %d skips", rep.Pass, rep.Skip, skips)
	}
	for _, c := range rep.Cases {
		if c.Level != Level || c.Harness != HarnessVersion || c.RunAt != "2026-10-20" {
			t.Fatalf("case %s reports level %q harness %q run %q", c.Case, c.Level, c.Harness, c.RunAt)
		}
	}
}

// TestCheckFailsEachBrokenRule breaks one rule at a time in conforming
// evidence and requires the row that names the rule to fail, with a detail
// that says why. A check that passed broken evidence would let a gateway
// that breaks the rule through L3.
func TestCheckFailsEachBrokenRule(t *testing.T) {
	for _, tc := range []struct {
		name, caseID, row, detail string
		breakIt                   func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence)
	}{
		{"a served tool missing from the audit view", remoteCase, rowListAudit, "listed",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.ListAudit = ce.ListAudit[1:] }},
		{"the audit view listed out of order", remoteCase, rowListAudit, "sorted",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.ListAudit[0], ce.ListAudit[1] = ce.ListAudit[1], ce.ListAudit[0]
			}},
		{"a refused tool listed in the posture view", remoteCase, rowListPosture, "acme_remote__purge_docs",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.ListPosture = append(ce.ListPosture, mustRaw(t, map[string]any{"name": "acme_remote__purge_docs"}))
			}},
		{"the same, judged by the tool's own row", remoteCase, prefixDenied + "acme_remote__purge_docs", "listed through the posture",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.ListPosture = append(ce.ListPosture, mustRaw(t, map[string]any{"name": "acme_remote__purge_docs"}))
			}},
		{"an annotation hint changed in transit", remoteCase, prefixAnnotations + "acme_remote__purge_docs", "destructiveHint",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				for i, def := range ce.ListAudit {
					if strings.Contains(string(def), "purge_docs") {
						ce.ListAudit[i] = mustRaw(t, map[string]any{"name": "acme_remote__purge_docs",
							"annotations": map[string]bool{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false}})
					}
				}
			}},
		{"a hint dropped in transit", localCase, prefixAnnotations + "acme_local__list_notes", "listed hints",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				for i, def := range ce.ListAudit {
					if strings.Contains(string(def), "list_notes") {
						ce.ListAudit[i] = mustRaw(t, map[string]any{"name": "acme_local__list_notes"})
					}
				}
			}},
		{"a refused call reached the upstream", remoteCase, prefixDenied + "acme_remote__purge_docs", "received 1 tools/call",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Received = append(ce.Received, received{Method: "tools/call", Name: "purge_docs", Era: "2025-06-18"})
			}},
		{"an allowed call answered with other content than its own result", localCase, prefixAllowed + "acme_local__list_notes", "not the upstream's result",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Answers["acme_local__list_notes"] = json.RawMessage(`{"result":{"content":[{"type":"text","text":"list_notes ok: {}"}]}}`)
			}},
		{"a refused call answered with a result", remoteCase, prefixDenied + "acme_remote__update_doc", "not refused in band",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Answers["acme_remote__update_doc"] = json.RawMessage(`{"result":{"content":[]}}`)
			}},
		{"a refusal naming another virtual server", remoteCase, prefixDenied + "acme_remote__update_doc", "want tool_denied on",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Answers["acme_remote__update_doc"] = json.RawMessage(`{"result":{"isError":true,"_meta":{"io.futureagi/error":{"code":"tool_denied","metadata":{"virtual_server":"vs_OTHER"}}}}}`)
			}},
		{"an allowed call refused", remoteCase, prefixAllowed + "acme_remote__search_docs", "refused",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Answers["acme_remote__search_docs"] = json.RawMessage(`{"error":{"code":-32602,"message":"Unknown tool"}}`)
			}},
		{"an allowed call answered with other content than the recording", remoteCase, prefixAllowed + "acme_remote__search_docs", "not the upstream's result",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Answers["acme_remote__search_docs"] = json.RawMessage(`{"result":{"content":[{"type":"text","text":"forged"}]}}`)
			}},
		{"structured content lost", legacyCase, prefixAllowed + "acme_legacy__doc_stats", "not the upstream's result",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				var a map[string]map[string]any
				_ = json.Unmarshal(ce.Answers["acme_legacy__doc_stats"], &a)
				delete(a["result"], "structuredContent")
				ce.Answers["acme_legacy__doc_stats"] = mustRaw(t, a)
			}},
		{"an allowed call sent upstream twice", remoteCase, prefixAllowed + "acme_remote__search_docs", "received 2 tools/call",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Received = append(ce.Received, received{Method: "tools/call", Name: "search_docs", Era: "2025-06-18"})
			}},
		{"an allowed call whose answer was not kept", remoteCase, prefixAllowed + "acme_remote__list_spaces", "no evidence",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				delete(ce.Answers, "acme_remote__list_spaces")
			}},
		{"one message without the credential", remoteCase, "credential.header.X-Acme-Key", "without the pushed x-acme-key",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.Received[1].Auth = credSeen{} }},
		{"the credential with another value", remoteCase, "credential.header.X-Acme-Key", "without the pushed",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Received[0].Auth = credSeen{Present: true, SHA256: sha256Hex("another")}
			}},
		{"a refused era probe without the credential", remoteCase, "credential.header.X-Acme-Key", "without the pushed",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Received = append(ce.Received, received{HTTPMethod: "POST", Method: "server/discover", Rejected: "no_session"})
			}},
		{"the caller's gateway key reached the upstream", linearCase, rowNotForwarded, "gateway key reached",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Received[0].Auth = credSeen{Present: true, SHA256: sha256Hex("Bearer " + inboundKey)}
			}},
		{"a credential the case does not place", remoteCase, rowNotForwarded, "x-forwarded-token",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Received[0].Credentials["x-forwarded-token"] = credSeen{Present: true, SHA256: sha256Hex("x")}
			}},
		{"the upstream refused a request", remoteCase, rowNotForwarded, "unauthorized",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.Received[1].Rejected = "unauthorized" }},
		{"a test credential value in the gateway's output", remoteCase, rowNotLogged, "gateway.log",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ev.Artifacts["evidence/gateway.log"] = []byte("upstream auth " + cs.Credential.Values[0])
			}},
		{"a stdio variable's value in a mock's log", localCase, rowNotLogged, "acme_remote.log",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ev.Artifacts["evidence/mock-logs/acme_remote.log"] = []byte("env " + cs.Credential.Values[0])
			}},
		{"a record missing", remoteCase, rowRecords, "want 4",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				dropRecord(ev, "acme_remote__list_spaces")
			}},
		{"two records of one call", remoteCase, rowRecords, "want 4",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ev.Records = append(ev.Records, *findRecord(ev, "acme_remote__search_docs"))
			}},
		{"a refused call recorded as dispatched", remoteCase, rowRecords, "dispatched true",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				findRecord(ev, "acme_remote__purge_docs").Dispatched = true
			}},
		{"a call attributed to another key", remoteCase, rowRecords, "api_key_id",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				findRecord(ev, "acme_remote__search_docs").APIKeyID = "key_other"
			}},
		{"a call attributed to another org", localCase, rowRecords, "org_id",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				findRecord(ev, "acme_local__list_notes").OrgID = "org-other"
			}},
		{"a record under another era", remoteCase, rowRecords, "era",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				findRecord(ev, "acme_remote__search_docs").Era = "2025-03-26"
			}},
		{"a record under the wrong upstream name", remoteCase, rowRecords, "upstream_tool",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				findRecord(ev, "acme_remote__search_docs").UpstreamTool = "list_spaces"
			}},
		{"an is_error result recorded as success", localCase, rowRecords, "outcome",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				findRecord(ev, "acme_local__env_names").Outcome = "denied"
			}},
		{"a message under another era than the pin", legacyCase, rowEra, "speaks 2024-11-05",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.Received[1].Era = "2025-06-18" }},
		{"a message under an era the gateway does not serve", localCase, rowEra, "does not serve",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.Received[0].Era = "2099-01-01" }},
		{"an upstream never reached", remoteCase, rowEra, "never reached",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.Received = nil }},
		{"a declared variable missing in the child", localCase, prefixEnv + "ACME_TOKEN", "does not see ACME_TOKEN",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ce.Env = json.RawMessage(`{"result":{"structuredContent":{"names":["PATH"]}}}`)
			}},
		{"no audit listing written", remoteCase, rowListAudit, "no evidence",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.missing[evListAudit] = true }},
		{"no received log written", remoteCase, rowEra, "no evidence",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) { ce.missing[evReceived] = true }},
		{"no gateway output written", remoteCase, rowNotLogged, "no evidence",
			func(t *testing.T, cs *Case, ce *CaseEvidence, ev *Evidence) {
				ev.Missing = append(ev.Missing, evGatewayLog)
			}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			plan := mustPlan(t)
			ev := goodEvidence(t, plan)
			cs := caseByID(t, plan, tc.caseID)
			tc.breakIt(t, cs, ev.Cases[tc.caseID], ev)
			rep := Check(plan, ev, "2026-10-20")
			got := rowResult(t, rep, tc.caseID, tc.row)
			if got.Result != ResultFail || !strings.Contains(got.Detail, tc.detail) {
				t.Fatalf("row %s = %s (%q), want fail mentioning %q", tc.row, got.Result, got.Detail, tc.detail)
			}
			if !rep.Failed() {
				t.Fatal("the report does not count the failure")
			}
		})
	}
}

func TestCheckAcceptsTheEraProbe(t *testing.T) {
	plan := mustPlan(t)
	ev := goodEvidence(t, plan)
	ce := ev.Cases[remoteCase]
	probe := ce.Received[0]
	probe.Method, probe.Era, probe.Rejected = "server/discover", "", "no_session"
	ce.Received = append([]received{probe}, ce.Received...)
	if rep := Check(plan, ev, "2026-10-20"); rep.Failed() {
		t.Fatalf("the era probe failed rows:\n%s", joined(failedRows(rep)))
	}
}

func TestCheckFailsADriftingRecording(t *testing.T) {
	plan := mustPlan(t)
	cs := caseByID(t, plan, remoteCase)
	cs.Drift = []string{"the manifest declares purge_docs, which the recording does not serve"}
	got := rowResult(t, Check(plan, goodEvidence(t, plan), "2026-10-20"), remoteCase, rowDrift)
	if got.Result != ResultFail || !strings.Contains(got.Detail, "purge_docs") {
		t.Fatalf("row %s = %s (%q), want fail naming purge_docs", rowDrift, got.Result, got.Detail)
	}
}

func TestCheckFailsACaseWithNoEvidence(t *testing.T) {
	plan := mustPlan(t)
	ev := goodEvidence(t, plan)
	delete(ev.Cases, localCase)
	rep := Check(plan, ev, "2026-10-20")
	for _, c := range rep.Cases {
		if c.Case != localCase {
			continue
		}
		if c.Verdict != VerdictFail {
			t.Fatalf("a case with no evidence is %s", c.Verdict)
		}
		for _, r := range c.Rows {
			if r.Result == ResultPass {
				t.Fatalf("row %s passed with no evidence", r.ID)
			}
		}
	}
}

func dropRecord(ev *Evidence, tool string) {
	for i, r := range ev.Records {
		if r.Tool == tool {
			ev.Records = append(ev.Records[:i], ev.Records[i+1:]...)
			return
		}
	}
}

func findRecord(ev *Evidence, tool string) *record {
	for i := range ev.Records {
		if ev.Records[i].Tool == tool {
			return &ev.Records[i]
		}
	}
	panic("no record of " + tool)
}
