package conformance

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
)

// The evidence directory 93_cn_conformance.sh writes, one directory per
// case (its server id) plus the run's shared files:
//
//	<server id>/list-audit.json    tools/list through the audit virtual server, pages merged: {"tools": [...]}
//	<server id>/list-posture.json  the same through the posture virtual server
//	<server id>/calls/<name>.json  each tools/call answer through the posture virtual server
//	<server id>/env.json           a stdio case's env_names answer, through the audit virtual server
//	<server id>/received.json      the case mock's /__received
//	<server id>/keys.json          {"posture": id, "audit": id, "inbound_sha256": [...]}
//	logs.json                      the request logs the gateway delivered (the X0 mock's /__logs)
//	gateway.log                    the gateway's output
const (
	evListAudit   = "list-audit.json"
	evListPosture = "list-posture.json"
	evCalls       = "calls"
	evEnv         = "env.json"
	evReceived    = "received.json"
	evKeys        = "keys.json"
	evLogs        = "logs.json"
	evGatewayLog  = "gateway.log"
)

// Evidence is what a run collected.
type Evidence struct {
	Cases   map[string]*CaseEvidence // by case id
	Records []record                 // kind tool_call, in delivery order
	// Artifacts are every evidence file's bytes, by path, which no test
	// credential value may appear in.
	Artifacts map[string][]byte
	// Missing names evidence files a run did not write.
	Missing []string
}

// CaseEvidence is one case's evidence.
type CaseEvidence struct {
	ListAudit   []json.RawMessage
	ListPosture []json.RawMessage
	Answers     map[string]json.RawMessage // by exposed tool name
	Env         json.RawMessage
	Received    []received
	Keys        caseKeys
	missing     map[string]bool
}

type caseKeys struct {
	Posture       string   `json:"posture"`
	Audit         string   `json:"audit"`
	InboundSHA256 []string `json:"inbound_sha256"`
}

// received is the part of an agentcc-mcpmock /__received entry L3 reads.
type received struct {
	Transport   string              `json:"transport"`
	HTTPMethod  string              `json:"http_method"`
	Path        string              `json:"path"`
	Method      string              `json:"method"`
	Name        string              `json:"name"`
	Era         string              `json:"era"`
	Auth        credSeen            `json:"auth"`
	Credentials map[string]credSeen `json:"credentials"`
	Rejected    string              `json:"rejected"`
}

type credSeen struct {
	Present bool   `json:"present"`
	SHA256  string `json:"sha256"`
}

// record is the part of an M15 tool_call record L3 reads.
type record struct {
	Kind            string `json:"kind"`
	OrgID           string `json:"org_id"`
	APIKeyID        string `json:"api_key_id"`
	VirtualServerID string `json:"virtual_server_id"`
	ServerID        string `json:"server_id"`
	Tool            string `json:"tool"`
	UpstreamTool    string `json:"upstream_tool"`
	Era             string `json:"era"`
	ProtocolVersion string `json:"protocol_version"`
	Outcome         string `json:"outcome"`
	IsError         bool   `json:"is_error"`
	ErrorCode       string `json:"error_code"`
	Dispatched      bool   `json:"dispatched"`
	BillingMode     string `json:"billing_mode"`
}

// LoadEvidence reads the evidence directory for the plan's running cases.
// A file the run did not write is listed in Missing, and the rows that need
// it fail; any other read or parse problem is an error.
func LoadEvidence(dir string, plan *Plan) (*Evidence, error) {
	ev := &Evidence{Cases: map[string]*CaseEvidence{}, Artifacts: map[string][]byte{}}
	err := filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		data, err := os.ReadFile(p)
		if err == nil {
			ev.Artifacts[p] = data
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	read := func(path string, v any) (bool, error) {
		data, ok := ev.Artifacts[path]
		if !ok {
			rel, _ := filepath.Rel(dir, path)
			ev.Missing = append(ev.Missing, rel)
			return false, nil
		}
		if err := json.Unmarshal(data, v); err != nil {
			return false, fmt.Errorf("%s: %w", path, err)
		}
		return true, nil
	}
	var logs struct {
		Logs []json.RawMessage `json:"logs"`
	}
	if _, err := read(filepath.Join(dir, evLogs), &logs); err != nil {
		return nil, err
	}
	for _, raw := range logs.Logs {
		var r record
		if json.Unmarshal(raw, &r) == nil && r.Kind == "tool_call" {
			ev.Records = append(ev.Records, r)
		}
	}
	if _, ok := ev.Artifacts[filepath.Join(dir, evGatewayLog)]; !ok {
		ev.Missing = append(ev.Missing, evGatewayLog)
	}
	for _, cs := range plan.Cases {
		if cs.Skip != "" {
			continue
		}
		ce, err := loadCase(filepath.Join(dir, cs.ServerID), cs, read)
		if err != nil {
			return nil, err
		}
		ev.Cases[cs.ID] = ce
	}
	sort.Strings(ev.Missing)
	return ev, nil
}

func loadCase(dir string, cs *Case, read func(string, any) (bool, error)) (*CaseEvidence, error) {
	ce := &CaseEvidence{Answers: map[string]json.RawMessage{}, missing: map[string]bool{}}
	var list struct {
		Tools []json.RawMessage `json:"tools"`
	}
	var rec struct {
		Received []received `json:"received"`
	}
	for _, f := range []struct {
		name string
		into any
		set  func()
	}{
		{evListAudit, &list, func() { ce.ListAudit, list.Tools = list.Tools, nil }},
		{evListPosture, &list, func() { ce.ListPosture, list.Tools = list.Tools, nil }},
		{evReceived, &rec, func() { ce.Received = rec.Received }},
		{evKeys, &ce.Keys, func() {}},
	} {
		ok, err := read(filepath.Join(dir, f.name), f.into)
		if err != nil {
			return nil, err
		}
		if !ok {
			ce.missing[f.name] = true
			continue
		}
		f.set()
	}
	for _, t := range cs.Tools {
		var answer json.RawMessage
		ok, err := read(filepath.Join(dir, evCalls, t.Exposed+".json"), &answer)
		if err != nil {
			return nil, err
		}
		if ok {
			ce.Answers[t.Exposed] = answer
		}
	}
	if cs.EnvProbe != "" {
		if ok, err := read(filepath.Join(dir, evEnv), &ce.Env); err != nil {
			return nil, err
		} else if !ok {
			ce.missing[evEnv] = true
		}
	}
	return ce, nil
}

// errNoEvidence is a row's failure when a file it reads was not written.
func errNoEvidence(file string) error {
	return errors.New("no evidence: the run wrote no " + file)
}
