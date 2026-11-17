package models

// NativeProtocol identifies an internal transport family, not capability support.
// Zero is unknown and cannot qualify a native dispatch.
type NativeProtocol uint8

const (
	NativeProtocolUnknown NativeProtocol = iota
	NativeMessages
	NativeResponses
	NativeGenAI
)

// NativeBody exposes the existing pass-through checks and final request bytes.
// Body must return a private-memory copy; consumers must not log or persist it.
type NativeBody interface {
	PassThroughRequest
	Body() []byte
}

// ComputerUseRequirement restricts dispatcher mode and accepted tool schema.
// A requirement never asserts capability support or authority.
type ComputerUseRequirement struct {
	Required      bool
	Protocol      NativeProtocol
	Action        string
	Stream        bool
	ToolProtocol  string
	VendorVersion string
}

// NativeRouteBinding is comparable scalar evidence, not authority. It contains
// no credentials, raw URLs or credential fingerprints; the actual provider and
// credential instance must be checked privately at dispatch.
type NativeRouteBinding struct {
	CanonicalModel     string
	SentModel          string
	ModelRevision      string
	ProviderID         string
	ProviderRevision   string
	ProviderRouteID    string
	RouteRevision      string
	EndpointType       string
	EndpointID         string
	UpstreamProvider   string
	CredentialType     CredentialType
	Protocol           NativeProtocol
	Action             string
	Stream             bool
	ToolProtocol       string
	VendorVersion      string
	CapabilityRevision string
}
