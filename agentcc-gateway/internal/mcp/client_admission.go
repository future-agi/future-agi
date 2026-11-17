package mcp

// ClientBinding is what an upstream client was built for: its catalog
// reference and its endpoint. A client never follows a changed binding.
type ClientBinding struct{ CatalogRef, URL string }

// ClientAdmissionRequest is a server's binding as it is NOW, never a token.
// Tenancy builds it from the org's current config and evaluates it at entry
// construction, at every credential lookup and before every send; no earlier
// answer is kept to stand in for a later one.
type ClientAdmissionRequest struct {
	OrgID, ServerID, CatalogRef, URL, CredentialMode string
	SharedOAuth, Managed, Builtin, InlineAuth        bool
	// Bound is the binding an existing client was built for, nil while no
	// client exists. A policy refuses a client whose binding has changed.
	Bound *ClientBinding
	// Inventory also asks for the offline tool list. Only entry construction
	// does; a credential lookup or a send needs the decision alone.
	Inventory bool
}

// ClientAdmission is the policy's answer for one request. A refused server
// stays visible without a client or an upstream discovery: Tools (Inventory
// requests only) is its offline inventory.
type ClientAdmission struct {
	Denial *Denial
	Tools  []Tool
}

// ClientAdmissionPolicy is the single admission decision: one implementation,
// a pure function of the request and the catalog as it is at the call.
type ClientAdmissionPolicy interface {
	AdmitClient(ClientAdmissionRequest) ClientAdmission
}

// ClientAdmissionPool is a pool's credential-lookup gate. It evaluates the
// policy now, for the server's current binding, on every call; it never
// returns a decision made earlier. Resolver adapters ask it before any token
// lookup.
type ClientAdmissionPool interface {
	ClientAdmission(serverID string) *Denial
}
