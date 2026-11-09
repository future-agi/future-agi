package conformance

import (
	"fmt"
	"strings"
)

// wantRecord is one tools/call the harness made and the record M15 must
// write for it.
type wantRecord struct {
	vs, key, tool, upstream string
	denied, isError         bool
}

// expectedRecords are the case's calls: every served tool through the
// posture virtual server, and a stdio case's env probe through the audit one.
func expectedRecords(cs *Case, ce *CaseEvidence) []wantRecord {
	var out []wantRecord
	for _, t := range cs.Tools {
		out = append(out, wantRecord{vs: cs.Posture.ID, key: ce.Keys.Posture, tool: t.Exposed, upstream: t.Name,
			denied: t.Denied, isError: t.ResultIsError})
	}
	if cs.EnvProbe != "" {
		out = append(out, wantRecord{vs: cs.Audit.ID, key: ce.Keys.Audit, tool: cs.EnvProbe, upstream: envProbeTool})
	}
	return out
}

// checkRecords: M15 wrote exactly one tool_call record per call, attributed
// to the case's org, key, virtual server and server, with the outcome the
// call had: an allowed call dispatched under its upstream name, a refused
// one denied (tool_denied) and never dispatched.
func checkRecords(cs *Case, ce *CaseEvidence, ev *Evidence) error {
	if ce.missing[evKeys] {
		return errNoEvidence(evKeys)
	}
	if !strings.HasPrefix(ce.Keys.Posture, "key_") || !strings.HasPrefix(ce.Keys.Audit, "key_") {
		return fmt.Errorf("%s names no key ids", evKeys)
	}
	var mine []record
	for _, r := range ev.Records {
		if r.VirtualServerID == cs.Posture.ID || r.VirtualServerID == cs.Audit.ID {
			mine = append(mine, r)
		}
	}
	want := expectedRecords(cs, ce)
	if len(mine) != len(want) {
		return fmt.Errorf("%d tool_call records for the case's virtual servers, want %d (one per call)", len(mine), len(want))
	}
	used := make([]bool, len(mine))
	for _, w := range want {
		found := -1
		for i, r := range mine {
			if !used[i] && r.VirtualServerID == w.vs && r.Tool == w.tool {
				found = i
				break
			}
		}
		if found < 0 {
			return fmt.Errorf("no record of the call to %s through %s", w.tool, w.vs)
		}
		used[found] = true
		if err := checkRecord(cs, w, mine[found]); err != nil {
			return fmt.Errorf("the record of %s: %w", w.tool, err)
		}
	}
	return nil
}

func checkRecord(cs *Case, w wantRecord, r record) error {
	switch {
	case r.OrgID != cs.Org:
		return fmt.Errorf("org_id %q, want %s", r.OrgID, cs.Org)
	case r.APIKeyID != w.key:
		return fmt.Errorf("api_key_id %q, want %s", r.APIKeyID, w.key)
	case r.ServerID != cs.ServerID:
		return fmt.Errorf("server_id %q, want %s", r.ServerID, cs.ServerID)
	case r.BillingMode != "byok":
		return fmt.Errorf("billing_mode %q, want byok", r.BillingMode)
	case r.Era != InboundEra || r.ProtocolVersion != InboundEra:
		return fmt.Errorf("era %q, protocol_version %q; the call was made under %s", r.Era, r.ProtocolVersion, InboundEra)
	}
	if w.denied {
		if r.Outcome != "denied" || r.ErrorCode != "tool_denied" || r.Dispatched {
			return fmt.Errorf("outcome %q, error_code %q, dispatched %t; want denied, tool_denied, not dispatched",
				r.Outcome, r.ErrorCode, r.Dispatched)
		}
		return nil
	}
	outcome := "success"
	if w.isError {
		outcome = "is_error"
	}
	if r.Outcome != outcome || r.IsError != w.isError || !r.Dispatched || r.ErrorCode != "" {
		return fmt.Errorf("outcome %q, is_error %t, dispatched %t, error_code %q; want %s, %t, dispatched, no error code",
			r.Outcome, r.IsError, r.Dispatched, r.ErrorCode, outcome, w.isError)
	}
	if r.UpstreamTool != w.upstream {
		return fmt.Errorf("upstream_tool %q, want %s", r.UpstreamTool, w.upstream)
	}
	return nil
}
