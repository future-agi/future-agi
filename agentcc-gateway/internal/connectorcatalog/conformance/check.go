package conformance

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
)

// Level is the conformance level this harness proves (scaling.md §5).
const Level = "L3"

// Case verdicts.
const (
	VerdictPass = "pass"
	VerdictFail = "fail"
	VerdictSkip = "skip"
)

// Report is a run's verdict, in the shape quality.conformance records
// (level, harness, fixturesSha256, runAt) plus every row.
type Report struct {
	Harness string       `json:"harness"`
	RunAt   string       `json:"run_at"`
	Cases   []CaseReport `json:"cases"`
	Pass    int          `json:"rows_pass"`
	Fail    int          `json:"rows_fail"`
	Skip    int          `json:"rows_skip"`
	Missing []string     `json:"missing_evidence,omitempty"`
}

// CaseReport is one case's verdict.
type CaseReport struct {
	Case           string      `json:"case"`
	Ref            string      `json:"ref"`
	Level          string      `json:"level"`
	Harness        string      `json:"harness"`
	FixturesSHA256 string      `json:"fixtures_sha256,omitempty"`
	RunAt          string      `json:"run_at"`
	Verdict        string      `json:"verdict"`
	Rows           []RowResult `json:"rows"`
}

// Failed reports whether any row failed.
func (r *Report) Failed() bool { return r.Fail > 0 }

// Check judges every row of the plan from the evidence. runAt is the run's
// date (YYYY-MM-DD).
func Check(plan *Plan, ev *Evidence, runAt string) *Report {
	rep := &Report{Harness: plan.Harness, RunAt: runAt, Missing: ev.Missing}
	for _, cs := range plan.Cases {
		cr := CaseReport{Case: cs.ID, Ref: cs.Ref, Level: Level, Harness: plan.Harness,
			FixturesSHA256: cs.FixturesSHA256, RunAt: runAt, Verdict: VerdictPass}
		ce := ev.Cases[cs.ID]
		if cs.Skip != "" {
			cr.Verdict = VerdictSkip
		}
		for _, row := range cs.Rows {
			res := RowResult{ID: row.ID, Result: ResultPass}
			switch {
			case row.Skip != "":
				res.Result, res.Detail = ResultSkip, row.Skip
			case ce == nil:
				res.Result, res.Detail = ResultFail, "no evidence was collected for the case"
			default:
				if err := judge(row.ID, cs, ce, ev); err != nil {
					res.Result, res.Detail = ResultFail, err.Error()
				}
			}
			switch res.Result {
			case ResultPass:
				rep.Pass++
			case ResultFail:
				rep.Fail++
				cr.Verdict = VerdictFail
			case ResultSkip:
				rep.Skip++
			}
			cr.Rows = append(cr.Rows, res)
		}
		rep.Cases = append(rep.Cases, cr)
	}
	return rep
}

// judge runs the check that judges one row.
func judge(id string, cs *Case, ce *CaseEvidence, ev *Evidence) error {
	switch {
	case id == rowListAudit:
		return checkListAudit(cs, ce)
	case id == rowListPosture:
		return checkListPosture(cs, ce)
	case id == rowEra:
		return checkEra(cs, ce)
	case id == rowNotForwarded:
		return checkNotForwarded(cs, ce)
	case id == rowNotLogged:
		return checkNotLogged(cs, ev)
	case id == rowRecords:
		return checkRecords(cs, ce, ev)
	case id == rowDrift:
		if len(cs.Drift) > 0 {
			return errors.New(strings.Join(cs.Drift, "; "))
		}
		return nil
	case strings.HasPrefix(id, prefixAnnotations):
		return withTool(cs, strings.TrimPrefix(id, prefixAnnotations), func(t *Tool) error { return checkAnnotations(t, ce) })
	case strings.HasPrefix(id, prefixAllowed):
		return withTool(cs, strings.TrimPrefix(id, prefixAllowed), func(t *Tool) error { return checkAllowed(cs, t, ce) })
	case strings.HasPrefix(id, prefixDenied):
		return withTool(cs, strings.TrimPrefix(id, prefixDenied), func(t *Tool) error { return checkDenied(cs, t, ce) })
	case strings.HasPrefix(id, prefixEnv):
		return checkEnvName(strings.TrimPrefix(id, prefixEnv), ce)
	case strings.HasPrefix(id, "credential."):
		return checkPlacement(id, cs, ce)
	}
	return fmt.Errorf("no check judges row %s", id)
}

func withTool(cs *Case, exposed string, f func(*Tool) error) error {
	for _, t := range cs.Tools {
		if t.Exposed == exposed {
			return f(t)
		}
	}
	return fmt.Errorf("the plan has no tool %s", exposed)
}

// toolNames are the names a tools/list answer lists, in its order.
func toolNames(list []json.RawMessage) []string {
	names := make([]string, 0, len(list))
	for _, def := range list {
		var t struct {
			Name string `json:"name"`
		}
		_ = json.Unmarshal(def, &t)
		names = append(names, t.Name)
	}
	return names
}

func listed(list []json.RawMessage, name string) json.RawMessage {
	for i, n := range toolNames(list) {
		if n == name {
			return list[i]
		}
	}
	return nil
}

// sameList holds a listing to the expected names: the same set, sorted.
func sameList(got []string, want []string) error {
	want = slices.Clone(want)
	slices.Sort(want)
	if !slices.Equal(got, want) {
		return fmt.Errorf("listed %v, want %v (sorted)", got, want)
	}
	return nil
}

func checkListAudit(cs *Case, ce *CaseEvidence) error {
	if ce.missing[evListAudit] {
		return errNoEvidence(evListAudit)
	}
	var want []string
	for _, t := range cs.Tools {
		want = append(want, t.Exposed)
	}
	if cs.EnvProbe != "" {
		want = append(want, cs.EnvProbe)
	}
	return sameList(toolNames(ce.ListAudit), want)
}

func checkListPosture(cs *Case, ce *CaseEvidence) error {
	if ce.missing[evListPosture] {
		return errNoEvidence(evListPosture)
	}
	want := []string{}
	for _, t := range cs.Tools {
		if !t.Denied {
			want = append(want, t.Exposed)
		}
	}
	return sameList(toolNames(ce.ListPosture), want)
}

// checkAnnotations holds a tool's hints, as the audit view lists it, to the
// hints the upstream served: passed through, none added or dropped.
func checkAnnotations(t *Tool, ce *CaseEvidence) error {
	if ce.missing[evListAudit] {
		return errNoEvidence(evListAudit)
	}
	def := listed(ce.ListAudit, t.Exposed)
	if def == nil {
		return fmt.Errorf("%s is not listed through the audit virtual server", t.Exposed)
	}
	if got := servedHints(def); !sameHints(got, t.Hints) {
		return fmt.Errorf("listed hints %s, the upstream served %s", hintsString(got), hintsString(t.Hints))
	}
	return nil
}

// answer is a tools/call answer: its result, or its JSON-RPC error.
type answer struct {
	Result *struct {
		Content           json.RawMessage `json:"content"`
		StructuredContent json.RawMessage `json:"structuredContent"`
		IsError           bool            `json:"isError"`
		Meta              map[string]struct {
			Code     string `json:"code"`
			Metadata struct {
				VirtualServer string `json:"virtual_server"`
			} `json:"metadata"`
		} `json:"_meta"`
	} `json:"result"`
	Error *struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

func readAnswer(ce *CaseEvidence, exposed string) (*answer, error) {
	raw, ok := ce.Answers[exposed]
	if !ok {
		return nil, errNoEvidence(evCalls + "/" + exposed + ".json")
	}
	var a answer
	if err := json.Unmarshal(raw, &a); err != nil {
		return nil, fmt.Errorf("the answer to %s is not JSON-RPC: %w", exposed, err)
	}
	return &a, nil
}

// upstreamCalls counts the tools/call messages the mock received for an
// upstream tool name.
func upstreamCalls(ce *CaseEvidence, name string) int {
	n := 0
	for _, r := range ce.Received {
		if r.Method == "tools/call" && r.Name == name {
			n++
		}
	}
	return n
}

// checkAllowed: an allowed tool is listed in the posture view, its call is
// answered with the upstream's result (the canned one, when there is one),
// and the call reached the upstream exactly once under its upstream name.
func checkAllowed(cs *Case, t *Tool, ce *CaseEvidence) error {
	if listed(ce.ListPosture, t.Exposed) == nil {
		return fmt.Errorf("%s is allowed but not listed through the posture virtual server", t.Exposed)
	}
	a, err := readAnswer(ce, t.Exposed)
	if err != nil {
		return err
	}
	if a.Error != nil || a.Result == nil {
		return fmt.Errorf("the call was refused: %+v", a.Error)
	}
	if a.Result.IsError != t.ResultIsError {
		return fmt.Errorf("isError %t, the upstream's result says %t", a.Result.IsError, t.ResultIsError)
	}
	if len(t.result) > 0 {
		var want struct {
			Content           json.RawMessage `json:"content"`
			StructuredContent json.RawMessage `json:"structuredContent"`
		}
		_ = json.Unmarshal(t.result, &want)
		if !sameJSON(a.Result.Content, want.Content) || !sameJSON(a.Result.StructuredContent, want.StructuredContent) {
			return fmt.Errorf("the answer's content is not the upstream's result: %s", a.Result.Content)
		}
	}
	if n := upstreamCalls(ce, t.Name); n != 1 {
		return fmt.Errorf("the upstream received %d tools/call for %s, want 1", n, t.Name)
	}
	return nil
}

// checkDenied: a refused tool is hidden from the posture view, its call is
// answered in band with tool_denied naming the posture virtual server, and
// nothing reached the upstream.
func checkDenied(cs *Case, t *Tool, ce *CaseEvidence) error {
	if listed(ce.ListPosture, t.Exposed) != nil {
		return fmt.Errorf("%s is refused (posture %s) but listed through the posture virtual server", t.Exposed, t.Posture)
	}
	a, err := readAnswer(ce, t.Exposed)
	if err != nil {
		return err
	}
	if a.Result == nil || !a.Result.IsError {
		return fmt.Errorf("the call was not refused in band: %s", ce.Answers[t.Exposed])
	}
	meta := a.Result.Meta["io.futureagi/error"]
	if meta.Code != "tool_denied" || meta.Metadata.VirtualServer != cs.Posture.ID {
		return fmt.Errorf("refused with %q on %q, want tool_denied on %s", meta.Code, meta.Metadata.VirtualServer, cs.Posture.ID)
	}
	if n := upstreamCalls(ce, t.Name); n != 0 {
		return fmt.Errorf("the upstream received %d tools/call for the refused %s", n, t.Name)
	}
	return nil
}

// sameJSON compares two JSON values by meaning; two absent values match.
func sameJSON(a, b json.RawMessage) bool {
	if len(bytes.TrimSpace(a)) == 0 || len(bytes.TrimSpace(b)) == 0 {
		return len(bytes.TrimSpace(a)) == len(bytes.TrimSpace(b))
	}
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	xs, _ := json.Marshal(x)
	ys, _ := json.Marshal(y)
	return bytes.Equal(xs, ys)
}
