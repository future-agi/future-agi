package mcp

// hookCheckApproval is step 10 of the tools/call chain (chain.go).
const hookCheckApproval = "checkApproval"

// checkApproval delegates to the shared approval provider. Approval retries use the
// ordinary chain: an approval never overrides visibility, ACLs or quarantine.
//
// The admin's Try a tool (principal admin, AdminTestTool) never asks for an
// approval: it has no key or user an approval could be bound to, and its
// call carries no approval id to claim one with. A tool its virtual server
// holds for an approval is refused with approval_required, and nothing runs
// or is created. A policy that cannot be read stays the provider's answer.
func checkApproval(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckApproval)
	if cc.Principal.Kind == PrincipalAdmin {
		if _, required, err := approvalPolicy(cc); err == nil && required {
			return adminApprovalRequired()
		}
	}
	return s.Deps().Approvals.Check(cc)
}

// adminApprovalRequired is the admin's Try a tool answer for a tool that needs
// an approval before it runs.
func adminApprovalRequired() *Denial {
	return approvalRefusal("approval_required", "This tool needs an approval before it runs, so Try a tool cannot run it. Agents' calls to it wait for an approval.")
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
