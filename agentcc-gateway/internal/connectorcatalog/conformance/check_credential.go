package conformance

import (
	"bytes"
	"encoding/json"
	"fmt"
	"path/filepath"
	"slices"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// prefixEnv starts a stdio case's row for one declared variable.
const prefixEnv = "credential.env."

// served are the mock's entries for messages it served, not refused.
func served(ce *CaseEvidence) []received {
	var out []received
	for _, r := range ce.Received {
		if r.Rejected == "" {
			out = append(out, r)
		}
	}
	return out
}

// checkEra: the gateway reached the upstream, and every message it sent
// carries the era the upstream speaks (the pinned or recorded one), or,
// against a mock that speaks every era, one the gateway serves.
func checkEra(cs *Case, ce *CaseEvidence) error {
	if ce.missing[evReceived] {
		return errNoEvidence(evReceived)
	}
	n := 0
	for _, r := range served(ce) {
		if r.Method == "" {
			continue // a bare GET or DELETE carries no message
		}
		n++
		switch {
		case cs.Era != "" && r.Era != cs.Era:
			return fmt.Errorf("%s was sent under %q, the upstream speaks %s", r.Method, r.Era, cs.Era)
		case cs.Era == "" && !slices.Contains(config.MCPProtocolVersions(), r.Era):
			return fmt.Errorf("%s was sent under %q, which the gateway does not serve", r.Method, r.Era)
		}
	}
	if n == 0 {
		return fmt.Errorf("the upstream received no message: the gateway never reached it")
	}
	return nil
}

// checkPlacement: every request the upstream received, the era probe it
// refused included, carried the credential part in its header, with the
// value the case pushed (by sha256).
func checkPlacement(id string, cs *Case, ce *CaseEvidence) error {
	i := slices.IndexFunc(cs.Credential.Expect, func(p Placement) bool { return p.Row == id })
	if i < 0 {
		return fmt.Errorf("the plan places nothing for row %s", id)
	}
	want := cs.Credential.Expect[i]
	if ce.missing[evReceived] {
		return errNoEvidence(evReceived)
	}
	if len(served(ce)) == 0 {
		return fmt.Errorf("the upstream served no request to check %s on", want.Header)
	}
	for _, r := range ce.Received {
		got := r.Credentials[want.Header]
		if want.Auth {
			got = r.Auth
		}
		if !got.Present || got.SHA256 != want.SHA256 {
			return fmt.Errorf("%s %s reached the upstream without the pushed %s (present %t)",
				r.HTTPMethod, r.Path, want.Header, got.Present)
		}
	}
	return nil
}

// eraProbe reports whether an entry is the gateway's era detection: on an
// upstream with no pinned era it sends a 2026 server/discover first, which an
// upstream that speaks only a session era refuses (no_session) before it
// falls back to initialize.
func eraProbe(r received) bool { return r.Rejected == "no_session" && r.Method == "server/discover" }

// checkNotForwarded: the upstream refused nothing but the era probe, saw no
// credential header beyond the ones the case places, and never the caller's
// own gateway key.
func checkNotForwarded(cs *Case, ce *CaseEvidence) error {
	for _, f := range []string{evReceived, evKeys} {
		if ce.missing[f] {
			return errNoEvidence(f)
		}
	}
	if len(ce.Keys.InboundSHA256) == 0 {
		return fmt.Errorf("%s names no inbound key hash to look for", evKeys)
	}
	placed := map[string]bool{}
	authHeader := "authorization" // agentcc-mcpmock's --auth-header default
	for _, p := range cs.Credential.Expect {
		placed[p.Header] = true
		if p.Auth {
			authHeader = p.Header
		}
	}
	for _, r := range ce.Received {
		if r.Rejected != "" && !eraProbe(r) {
			return fmt.Errorf("the upstream refused %s %s (%s)", r.HTTPMethod, r.Path, r.Rejected)
		}
		seen := map[string]credSeen{}
		for h, c := range r.Credentials {
			seen[h] = c
		}
		if r.Auth.Present {
			seen[authHeader] = r.Auth
		}
		for h, c := range seen {
			if slices.Contains(ce.Keys.InboundSHA256, c.SHA256) {
				return fmt.Errorf("the caller's gateway key reached the upstream in %s", h)
			}
			if !placed[h] {
				return fmt.Errorf("the upstream received the credential header %s, which the case does not place", h)
			}
		}
	}
	return nil
}

// checkNotLogged: no test credential value appears in any evidence file:
// the gateway's output, its request logs, the answers and the mock's logs.
func checkNotLogged(cs *Case, ev *Evidence) error {
	for _, f := range []string{evGatewayLog, evLogs} {
		if slices.Contains(ev.Missing, f) {
			return errNoEvidence(f)
		}
	}
	paths := make([]string, 0, len(ev.Artifacts))
	for p := range ev.Artifacts {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	for i, v := range cs.Credential.Values {
		for _, p := range paths {
			if bytes.Contains(ev.Artifacts[p], []byte(v)) {
				return fmt.Errorf("test credential value %d of the case appears in %s", i+1, filepath.Base(filepath.Dir(p))+"/"+filepath.Base(p))
			}
		}
	}
	return nil
}

// checkEnvName: a declared variable of a stdio case reached the child.
func checkEnvName(name string, ce *CaseEvidence) error {
	if ce.missing[evEnv] {
		return errNoEvidence(evEnv)
	}
	var a struct {
		Result struct {
			StructuredContent struct {
				Names []string `json:"names"`
			} `json:"structuredContent"`
		} `json:"result"`
	}
	if err := json.Unmarshal(ce.Env, &a); err != nil {
		return fmt.Errorf("the env_names answer is not JSON-RPC: %w", err)
	}
	if !slices.Contains(a.Result.StructuredContent.Names, name) {
		return fmt.Errorf("the child does not see %s; it sees %s", name, strings.Join(a.Result.StructuredContent.Names, ", "))
	}
	return nil
}
