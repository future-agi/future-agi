package conformance

import "fmt"

// The rows L3 skips until the gateway part that turns them on exists
// (rows that need tool rate limits, tool pins, user mode,
// per-user credentials or approvals are skipped with a reason). Each reason
// says in plain words what the row waits on: the report an operator reads
// prints it. The change that adds that part turns its row on here, with the
// check that judges it, in the same commit.
const (
	waitApprovals = "approvals are not built yet: until the gateway answers an ask tool with approval_required it " +
		"cannot enforce ask, so L3 holds every ask tool as deny (row posture.ask)"
	waitRateLimits = "tool rate limits are not built yet: the gateway's rate-limit hook is a no-op at this base, so the " +
		"provider's rateLimits do not become tool_rate_limited"
	waitPins = "tool pins are not built yet: the gateway's pin hook is a no-op at this base, so the served tools are " +
		"not checked against the provider's snapshot"
	waitUserMode = "user mode needs a person's own gateway token on the virtual server and that person's own upstream " +
		"credential; neither is built, so L3 calls with an org key, as agent mode does"
	waitOAuth = "the OAuth token comes from the per-user connect flow and reaches the upstream through per-call " +
		"resolution; neither is built, so L3 places no OAuth token and checks only that no inbound credential " +
		"reaches the upstream"
	waitK2Runtime = "L3's gateway rows run against agentcc-mcpmock serving the provider's generated tools in place " +
		"of the K2 runtime, agentcc-connectors; the runtime itself is checked by " +
		"connectors/94_cn_rest_runtime.sh --catalog <dir> --apps all, which replays each rest provider's committed " +
		"cassette through it, and a rest provider with no committed cassette waits for a recording (the live " +
		"recorder and its verified promotion)"
	waitGeneric = "posture on a generic tool's operation argument needs an argument translator and an argument rule " +
		"in the gateway; L3 applies each generic tool's own posture only"
	waitPartner = "a K3 partner provider reaches its platform through vendor credentials, end-user binding and " +
		"platform entries; none of them is built"
	waitEnvValue = "agentcc-mcpmock reports the names of a stdio child's environment variables, never their " +
		"values, so L3 checks that each declared name reaches the child but not the value it holds"
	waitSSEPath = "a 2024-11-05 HTTP+SSE upstream: agentcc-mcpmock serves that era only at /sse and /messages, " +
		"so the declared path is not checked"
	waitBasic = "auth kind basic: a pushed secret carries a bearer token or one raw header value, and nothing " +
		"encodes a Basic credential into it yet"
	waitServiceAccount = "auth kind service_account: the upstream token comes from a service-account exchange " +
		"at connect time, which is not built"
	waitNoTools = "the manifest declares no tools and has no recording: the mock serves only " + undeclaredTool +
		", which judges the provider's default posture; its own tools wait for a recording (the live " +
		"recorder and its verified promotion)"
)

// waitQuery is the reason a credential part in a query parameter is skipped.
func waitQuery(name string) string {
	return fmt.Sprintf("the credential part %s goes in a query parameter: the gateway places it, but "+
		"agentcc-mcpmock's /__received keeps no query string to witness it", name)
}

// waitURLVariable is the reason a credential part in a URL variable is
// skipped.
func waitURLVariable(name string) string {
	return fmt.Sprintf("the credential part %s fills the URL variable {%s}: the gateway renders it, but "+
		"agentcc-mcpmock keeps a request path verbatim, not as a hash, so L3 cannot witness it without logging it",
		name, name)
}

// waitCustomerSet is the reason a non-secret part with no value is skipped.
func waitCustomerSet(name string) string {
	return fmt.Sprintf("the credential part %s is a non-secret header the customer sets: the push carries only "+
		"secret parts and the catalog's constants, so the gateway refuses this provider's credential "+
		"rather than send it without the header", name)
}

// waitHeldBack is the reason a part L3 could push is not pushed: another
// part of the same credential holds it back.
func waitHeldBack(name, held string) string {
	return fmt.Sprintf("the credential part %s is not pushed: %s holds back the whole credential, since the gateway "+
		"takes a catalog provider's credential whole and refuses one pushed in part; %s's row says why",
		name, held, held)
}

// waitNoSecretPart is the reason the parts of an api_key credential with no
// secret part are skipped: nothing is pushed for the gateway to add them to.
func waitNoSecretPart(name string) string {
	return fmt.Sprintf("the credential part %s is a constant, and the provider declares no secret part: the gateway "+
		"adds constants only to a pushed secret, so nothing carries it", name)
}

// waitLaterAuthorization is the reason an Authorization part after the
// secret's own is skipped.
func waitLaterAuthorization() string {
	return "the credential part Authorization is a later secret part: the push sends the Bearer prefix only on the " +
		"secret's own value, never on one of its parts, so nothing places it yet"
}

// waitPrefix is the reason a credential part with a prefix the gateway
// cannot send is skipped.
func waitPrefix(name, prefix string) string {
	what := fmt.Sprintf("carries the prefix %q", prefix)
	if prefix == "" {
		what = "has no prefix"
	}
	return fmt.Sprintf("the credential part %s %s: the pushed secret has no prefix field, and the gateway "+
		"prefixes only Authorization, always with Bearer, so nothing places it yet",
		name, what)
}

// waitUserVariable is the reason a per-user URL variable is skipped.
func waitUserVariable(name string) string {
	return fmt.Sprintf("the URL variable {%s} is per user: a per-user URL needs per-user resolution, which is not built; "+
		"L3 renders a sample value and checks the path the gateway sends", name)
}
