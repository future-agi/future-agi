package mcp

// hookCheckApproval is step 10 of the tools/call chain (chain.go).
const hookCheckApproval = "checkApproval"

// checkApproval delegates to the shared approval provider. Approval retries use the
// ordinary chain: an approval never overrides visibility, ACLs or quarantine.
func checkApproval(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckApproval)
	return s.Deps().Approvals.Check(cc)
}

// The default preserves unconfigured behavior, but cannot silently allow a
// configured approval policy when executable wiring is absent.
type noApprovals struct{}

func (noApprovals) Check(cc *CallContext) *Denial {
	_, required, err := approvalPolicy(cc)
	retry, _ := cc.Context().Value(approvalRetryKey{}).(approvalRetry)
	if required || err != nil || retry.ID != "" || retry.Invalid {
		return approvalUnavailable()
	}
	return nil
}
