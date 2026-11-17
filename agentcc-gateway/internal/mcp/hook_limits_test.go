package mcp

import "testing"

type w5bRateRefusal struct{ calls int }

func (r *w5bRateRefusal) Admit(*CallContext) (func(), *Denial) {
	r.calls++
	return nil, &Denial{Code: "tool_rate_limited", Message: "Try again in ~60s"}
}

type w5bRunRefusal struct{}

func (w5bRunRefusal) Check(*CallContext) *Denial {
	return &Denial{Code: "agent_run_stopped"}
}

// These behavioral tests use only the accepted base's public dependency seams.
// A missing implementation must fail assertions, not compilation.
func TestW5bRateHookRefusesBeforeDispatch(t *testing.T) {
	s := &Server{}
	r := &w5bRateRefusal{}
	s.SetDeps(Deps{RateLimiter: r})
	cc := &CallContext{}
	d := checkRateLimit(s, cc)
	if d == nil || d.Code != "tool_rate_limited" || r.calls != 1 {
		t.Fatal("rate hook bypassed the installed admission provider")
	}
	if cc.Executed || cc.Dispatched {
		t.Fatal("refused call reached upstream")
	}
}

func TestW5bRunGuardRefusesBeforeBudget(t *testing.T) {
	s := &Server{}
	s.SetDeps(Deps{RunGuard: w5bRunRefusal{}})
	d := reserveBudget(s, &CallContext{})
	if d == nil || d.Code != "agent_run_stopped" {
		t.Fatal("budget hook bypassed the installed run guard")
	}
}
