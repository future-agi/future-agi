package conformance

import "fmt"

// The rows L3 skips until the lane that turns them on lands (scaling.md §5:
// rows that need M19, M20, M23, M25 or M26 are skipped with a named reason).
// Each reason names that lane. A landing lane turns its row on here, with the
// check that judges it, in the same commit.
const (
	waitApprovals = "M26 (approvals, R8) answers an ask tool with approval_required; until it lands the gateway " +
		"cannot enforce ask, so L3 holds every ask tool as deny (row posture.ask)"
	waitRateLimits = "M19 (tool rate limits, R3) turns the provider's rateLimits into tool_rate_limited; the " +
		"gateway's rate-limit hook is a no-op at this base"
	waitPins = "M20 (tool pins) checks the served tools against the provider's snapshot and quarantines drift; " +
		"the gateway's pin hook is a no-op at this base"
	waitUserMode = "user mode needs M23 and M24 (a person's own gateway token on the virtual server, R5 and R6) " +
		"and M25 (that person's own upstream credential, R5); L3 calls with an org key, as agent mode does"
	waitOAuth = "the OAuth token comes from M25x's connect flow (R5, AM-C1) and reaches the upstream through " +
		"M25's per-call resolution (R5); neither is built, so L3 places no OAuth token and checks only that no " +
		"inbound credential reaches the upstream"
	waitK2Runtime = "L3's gateway rows run against agentcc-mcpmock serving the provider's generated tools in place " +
		"of K2's runtime, agentcc-connectors (CN10); the runtime itself is checked by " +
		"connectors/94_cn_rest_runtime.sh --catalog <dir> --apps all, which replays each rest provider's committed " +
		"cassette through it (CW2d), and a rest provider with no committed cassette waits for a recording (CN13's " +
		"live recorder, CN31's verified promotion)"
	waitGeneric = "AM-C12(c): posture on a generic tool's operation argument needs CN9's translator and an " +
		"argument rule in the gateway; L3 applies each generic tool's own posture only"
	waitPartner = "a K3 partner provider reaches its platform through V10's vendor credentials and end-user " +
		"binding and CN20's platform entries (both CW3); neither is built"
	waitEnvValue = "agentcc-mcpmock reports the names of a stdio child's environment variables, never their " +
		"values, so L3 checks that each declared name reaches the child but not the value it holds (M4)"
	waitSSEPath = "a 2024-11-05 HTTP+SSE upstream: agentcc-mcpmock serves that era only at /sse and /messages " +
		"(M4), so the declared path is not checked"
	waitBasic = "auth kind basic: a pushed secret carries a bearer token or one raw header value (AM-C9), and no " +
		"lane in CONNECTORS-PLAN encodes a Basic credential into it (integrator request CN8-IR-4)"
	waitServiceAccount = "auth kind service_account: the upstream token comes from a service-account exchange " +
		"at connect time, which no lane in CONNECTORS-PLAN builds (integrator request CN8-IR-4)"
	waitNoTools = "the manifest declares no tools and has no recording: the mock serves only " + undeclaredTool +
		", which judges the provider's default posture; its own tools wait for a recording (CN13's live " +
		"recorder, CN31's verified promotion)"
)

// waitQuery is the reason a credential part in a query parameter is skipped.
func waitQuery(name string) string {
	return fmt.Sprintf("the credential part %s goes in a query parameter: the gateway places it (CN7), but "+
		"agentcc-mcpmock's /__received keeps no query string to witness it (M4; integrator request CN8-IR-2)", name)
}

// waitURLVariable is the reason a credential part in a URL variable is
// skipped.
func waitURLVariable(name string) string {
	return fmt.Sprintf("the credential part %s fills the URL variable {%s}: the gateway renders it (CN7), but "+
		"agentcc-mcpmock keeps a request path verbatim, not as a hash, so L3 cannot witness it without logging it "+
		"(M4; integrator request CN8-IR-2)", name, name)
}

// waitCustomerSet is the reason a non-secret part with no value is skipped.
func waitCustomerSet(name string) string {
	return fmt.Sprintf("the credential part %s is a non-secret header the customer sets: the push carries only "+
		"secret parts and the catalog's constants (AM-C20), so the gateway refuses this provider's credential "+
		"rather than send it without the header (CN7; integrator request CN8-IR-3)", name)
}

// waitHeldBack is the reason a part L3 could push is not pushed: another
// part of the same credential holds it back.
func waitHeldBack(name, held string) string {
	return fmt.Sprintf("the credential part %s is not pushed: %s holds back the whole credential, since the gateway "+
		"takes a catalog provider's credential whole and refuses one pushed in part (CN7); %s's row says why",
		name, held, held)
}

// waitNoSecretPart is the reason the parts of an api_key credential with no
// secret part are skipped: nothing is pushed for the gateway to add them to.
func waitNoSecretPart(name string) string {
	return fmt.Sprintf("the credential part %s is a constant, and the provider declares no secret part: the gateway "+
		"adds constants only to a pushed secret (CN7), so nothing carries it", name)
}

// waitLaterAuthorization is the reason an Authorization part after the
// secret's own is skipped.
func waitLaterAuthorization() string {
	return "the credential part Authorization is a later secret part: the push sends the Bearer prefix only on the " +
		"secret's own value, never on one of its parts, so no lane places it yet (integrator request CN8-IR-3)"
}

// waitPrefix is the reason a credential part with a prefix the gateway
// cannot send is skipped.
func waitPrefix(name, prefix string) string {
	what := fmt.Sprintf("carries the prefix %q", prefix)
	if prefix == "" {
		what = "has no prefix"
	}
	return fmt.Sprintf("the credential part %s %s: the pushed secret has no prefix field, and the gateway "+
		"prefixes only Authorization, always with Bearer, so no lane places it yet (integrator request CN8-IR-3)",
		name, what)
}

// waitUserVariable is the reason a per-user URL variable is skipped.
func waitUserVariable(name string) string {
	return fmt.Sprintf("the URL variable {%s} is per user: a per-user URL needs M25's per-user resolution (R5); "+
		"L3 renders a sample value and checks the path the gateway sends", name)
}
