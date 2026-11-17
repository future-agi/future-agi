package conformance

import (
	"encoding/json"
	"os"
	"sort"
	"strings"
	"testing"
)

// testdataCatalogs are the two catalogs `--catalog testdata` runs.
func testdataCatalogs() []Catalog {
	return []Catalog{
		{Label: "cn1-example", FS: os.DirFS("../manifest/testdata/valid")},
		{Label: "l3-fixtures", FS: os.DirFS("testdata/catalog")},
	}
}

func mustPlan(t *testing.T) *Plan {
	t.Helper()
	plan, err := Build(testdataCatalogs())
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	return plan
}

func caseByID(t *testing.T, plan *Plan, id string) *Case {
	t.Helper()
	for _, cs := range plan.Cases {
		if cs.ID == id {
			return cs
		}
	}
	t.Fatalf("no case %s", id)
	return nil
}

func toolByName(t *testing.T, cs *Case, name string) *Tool {
	t.Helper()
	for _, tl := range cs.Tools {
		if tl.Name == name {
			return tl
		}
	}
	t.Fatalf("case %s has no tool %s", cs.ID, name)
	return nil
}

func mustRaw(t *testing.T, v any) json.RawMessage {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return b
}

// inboundKey is the fake gateway key the synthetic evidence's caller used.
const inboundKey = "scn-test-inbound-key"

// goodEvidence is what a run of a conforming gateway collects for the plan:
// every rule each row names holds. Each test breaks one thing in it.
func goodEvidence(t *testing.T, plan *Plan) *Evidence {
	t.Helper()
	ev := &Evidence{Cases: map[string]*CaseEvidence{}, Artifacts: map[string][]byte{
		"evidence/gateway.log": []byte("level=info msg=listening\n"),
		"evidence/logs.json":   []byte(`{"logs":[]}`),
	}}
	for i, cs := range plan.Cases {
		if cs.Skip != "" {
			continue
		}
		ce := &CaseEvidence{Answers: map[string]json.RawMessage{}, missing: map[string]bool{}}
		ce.Keys = caseKeys{Posture: "key_posture" + string(rune('a'+i)), Audit: "key_audit" + string(rune('a'+i)),
			InboundSHA256: []string{sha256Hex("Bearer " + inboundKey), sha256Hex(inboundKey)}}
		era := cs.Era
		if era == "" {
			era = "2026-07-28"
		}
		msg := func(method, name string) received {
			r := received{Transport: "http", HTTPMethod: "POST", Path: cs.Path, Method: method, Name: name, Era: era,
				Credentials: map[string]credSeen{}}
			for _, p := range cs.Credential.Expect {
				if p.Auth {
					r.Auth = credSeen{Present: true, SHA256: p.SHA256}
				} else {
					r.Credentials[p.Header] = credSeen{Present: true, SHA256: p.SHA256}
				}
			}
			return r
		}
		ce.Received = append(ce.Received, msg("initialize", ""), msg("tools/list", ""))
		for _, tl := range cs.Tools {
			def := map[string]any{"name": tl.Exposed, "annotations": tl.Hints}
			ce.ListAudit = append(ce.ListAudit, mustRaw(t, def))
			if tl.Denied {
				ce.Answers[tl.Exposed] = mustRaw(t, map[string]any{"jsonrpc": "2.0", "id": 3, "result": map[string]any{
					"isError": true, "content": []any{map[string]any{"type": "text", "text": "denied"}},
					"_meta": map[string]any{"io.futureagi/error": map[string]any{"code": "tool_denied",
						"metadata": map[string]any{"virtual_server": cs.Posture.ID}}}}})
			} else {
				ce.ListPosture = append(ce.ListPosture, mustRaw(t, def))
				result := map[string]any{"content": []any{map[string]any{"type": "text", "text": tl.Name + " ok"}}}
				if len(tl.result) > 0 {
					result = map[string]any{}
					_ = json.Unmarshal(tl.result, &result)
				}
				if tl.ResultIsError {
					result["isError"] = true
				}
				ce.Answers[tl.Exposed] = mustRaw(t, map[string]any{"jsonrpc": "2.0", "id": 3, "result": result})
				ce.Received = append(ce.Received, msg("tools/call", tl.Name))
			}
			ev.Records = append(ev.Records, goodRecord(cs, ce, tl.Exposed, tl.Name, tl.Posture, tl.Denied, tl.ResultIsError))
		}
		if cs.EnvProbe != "" {
			ce.ListAudit = append(ce.ListAudit, mustRaw(t, map[string]any{"name": cs.EnvProbe}))
			names := []string{"PATH", "HOME"}
			for name := range cs.Credential.Env {
				names = append(names, name)
			}
			ce.Env = mustRaw(t, map[string]any{"result": map[string]any{"structuredContent": map[string]any{"names": names}}})
			ce.Received = append(ce.Received, msg("tools/call", envProbeTool))
			r := goodRecord(cs, ce, cs.EnvProbe, envProbeTool, "", false, false)
			r.VirtualServerID, r.APIKeyID = cs.Audit.ID, ce.Keys.Audit
			ev.Records = append(ev.Records, r)
		}
		sortDefs(ce.ListAudit)
		ev.Cases[cs.ID] = ce
	}
	return ev
}

// sortDefs orders tool definitions by name, as the gateway lists them.
func sortDefs(defs []json.RawMessage) {
	sort.SliceStable(defs, func(i, j int) bool { return toolNames(defs[i : i+1])[0] < toolNames(defs[j : j+1])[0] })
}

func goodRecord(cs *Case, ce *CaseEvidence, exposed, upstream, posture string, denied, isError bool) record {
	r := record{Kind: "tool_call", OrgID: cs.Org, APIKeyID: ce.Keys.Posture, VirtualServerID: cs.Posture.ID,
		ServerID: cs.ServerID, Tool: exposed, Era: InboundEra, ProtocolVersion: InboundEra, BillingMode: "byok"}
	switch {
	case denied:
		r.Outcome, r.ErrorCode = "denied", "tool_denied"
	case isError:
		r.Outcome, r.IsError, r.Dispatched, r.UpstreamTool = "is_error", true, true, upstream
	default:
		r.Outcome, r.Dispatched, r.UpstreamTool = "success", true, upstream
	}
	return r
}

// rowResult is the judged row of a case, failing the test when absent.
func rowResult(t *testing.T, rep *Report, caseID, rowID string) RowResult {
	t.Helper()
	for _, c := range rep.Cases {
		if c.Case != caseID {
			continue
		}
		for _, r := range c.Rows {
			if r.ID == rowID {
				return r
			}
		}
		t.Fatalf("case %s has no row %s", caseID, rowID)
	}
	t.Fatalf("no case %s in the report", caseID)
	return RowResult{}
}

// failedRows lists every failed row as "<case> <row>".
func failedRows(rep *Report) []string {
	var out []string
	for _, c := range rep.Cases {
		for _, r := range c.Rows {
			if r.Result == ResultFail {
				out = append(out, c.Case+" "+r.ID+": "+r.Detail)
			}
		}
	}
	return out
}

func joined(lines []string) string { return strings.Join(lines, "\n") }
