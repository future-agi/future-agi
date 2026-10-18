// Package video defines provider-neutral video contracts. It has no lifecycle or
// configuration dependencies; capability and tariff packages consume these types.
package video

import (
	"encoding/json"
	"time"
)

type Operation string

const (
	TextToVideo     Operation = "text_to_video"
	ImageFirstFrame Operation = "image_first_frame"
	ImageFirstLast  Operation = "image_first_last"
	Reference       Operation = "reference"
)

type Role string

const (
	FirstFrame     Role = "first_frame"
	LastFrame      Role = "last_frame"
	ReferenceImage Role = "reference_image"
	ReferenceVideo Role = "reference_video"
	ReferenceAudio Role = "reference_audio"
)

type State string

const (
	StateQueued    State = "queued"
	StateRunning   State = "running"
	StateCompleted State = "completed"
	StateFailed    State = "failed"
	StateCancelled State = "cancelled"
)

func (s State) Valid() bool {
	switch s {
	case StateQueued, StateRunning, StateCompleted, StateFailed, StateCancelled:
		return true
	}
	return false
}

type IdempotencyKind string

const (
	IdempotencyNone  IdempotencyKind = "none"
	IdempotencyToken IdempotencyKind = "token"
)

type LookupKind string

const (
	LookupNone      LookupKind = "none"
	LookupByToken   LookupKind = "by_token"
	LookupHeuristic LookupKind = "heuristic"
)

type CancelKind string

const (
	CancelNone             CancelKind = "none"
	CancelQueuedOnly       CancelKind = "queued_only"
	CancelQueuedAndRunning CancelKind = "queued_and_running"
)

type ACLKind string

const (
	ACLPrivateToken  ACLKind = "private_token"
	ACLPrivateSigned ACLKind = "private_signed"
	ACLPublicDefault ACLKind = "public_default"
	ACLUnknown       ACLKind = "unknown"
)

type Billability string

const (
	NotBilled      Billability = "not_billed"
	Billed         Billability = "billed"
	BillingUnknown Billability = "unknown"
)

type WebhookKind string

const (
	WebhookNone            WebhookKind = "none"
	WebhookUnauthenticated WebhookKind = "unauthenticated"
	WebhookSigned          WebhookKind = "signed"
)

type AccessState string

const (
	AccessDocumented AccessState = "documented"
	AccessGated      AccessState = "gated"
	AccessUnverified AccessState = "unverified"
	AccessRetired    AccessState = "retired"
)

type Capabilities struct {
	Service           string
	Revision          string
	Regions           []string
	Models            []ModelCapability
	SubmitIdempotency IdempotencyKind
	CorrelationLookup LookupKind
	CancelSupport     CancelKind
	OutputURLExpiry   time.Duration
	OutputURLRefresh  bool
	OutputACL         ACLKind
	BillsOnFailure    Billability
	Webhook           WebhookKind
	PollMinInterval   time.Duration
}
type ModelCapability struct {
	ModelID           string
	Operations        map[Operation]OperationSpec
	Durations         IntRange
	AutoDuration      *AutoDurationSpec
	Resolutions       []string
	AspectRatios      []string
	FPS               []int
	MaxOutputs        int
	NativeMultiOutput bool
	Audio             AudioSpec
	Inputs            map[Role]InputSpec
	PromptLimit       PromptLimit
	Options           map[string]OptionSpec
	Tariff            *TariffRef
	Access            AccessState
}
type OperationSpec struct {
	Roles         []Role
	RequiredRoles []Role
	AspectRatios  []string
}
type IntRange struct {
	Min, Max int
	Enum     []int
}
type AutoDurationSpec struct {
	Value   int
	Bounded bool
}
type AudioSpec struct {
	Generated string
	InputRefs []Role
	AudioOnly bool
}
type InputSpec struct {
	MinCount, MaxCount         int
	MaxBytes                   int64
	MaxPixels                  int64
	MinDimension, MaxDimension int
	MinRatio, MaxRatio         float64
	Formats                    []string
}
type PromptLimit struct {
	Unit string
	Max  int
}
type OptionSpec struct {
	Type         string
	Values       []string
	Min, Max     int64
	Step, Offset int64
	Default      any
}
type TariffRef struct {
	Unit     Unit
	Revision string
}

type Source struct {
	URL  string `json:"url,omitempty"`
	Data string `json:"data,omitempty"`
}
type Input struct {
	Role      Role   `json:"role"`
	MediaType string `json:"media_type"`
	Source    Source `json:"source"`
	// Filled by ingress, never accepted from client JSON.
	Digest          string  `json:"-"`
	DurationSeconds float64 `json:"-"`
	Width           int     `json:"-"`
	Height          int     `json:"-"`
	Bytes           int64   `json:"-"`
}
type Request struct {
	Model           string            `json:"model"`
	Prompt          string            `json:"prompt"`
	Inputs          []Input           `json:"inputs,omitempty"`
	DurationSeconds float64           `json:"duration_seconds,omitempty"`
	Resolution      string            `json:"resolution,omitempty"`
	AspectRatio     string            `json:"aspect_ratio,omitempty"`
	FPS             int               `json:"fps,omitempty"`
	Audio           *bool             `json:"audio,omitempty"`
	N               int               `json:"n,omitempty"`
	ProviderOptions map[string]any    `json:"provider_options,omitempty"`
	Metadata        map[string]string `json:"metadata,omitempty"`
	EndUserID       string            `json:"end_user_id,omitempty"`
}
type JobView struct {
	ID, OrgID, KeyID, Service, ModelID, Region, AccountRef, EndUserHash string
	Request                                                             Request
	AttemptID                                                           string
	AttemptStartedAt                                                    time.Time
	SubmitBy                                                            time.Time
}
type Correlation struct {
	Token string
	Data  map[string]string
}
type ProviderRef struct {
	JobID, Service, ModelID, Region, AccountRef, ProviderJobID, ProviderState string
	Outputs                                                                   []OutputRef
}
type OutputRef struct {
	Handle, URL, ContentType string
	Width, Height            int
	DurationSeconds          float64
	Bytes                    int64
	ExpiresAt                *time.Time
}
type FetchMeta struct {
	ContentType     string
	Bytes           int64
	Width, Height   int
	DurationSeconds float64
}

// Unit is shared with tariff without creating a provider -> lifecycle dependency.
type Unit string

const (
	VideoTokens      Unit = "video_tokens"
	OutputSeconds    Unit = "output_seconds"
	InputSeconds     Unit = "input_seconds"
	Images           Unit = "images"
	Credits          Unit = "credits"
	MultimodalTokens Unit = "multimodal_tokens"
	StorageBytes     Unit = "storage_bytes"
)

type UsageSource string

const (
	UsageEstimate         UsageSource = "estimate"
	UsageProviderReported UsageSource = "provider_reported"
	UsageReconciled       UsageSource = "reconciled"
)

type UsageLine struct {
	Unit           Unit        `json:"unit"`
	Quantity       float64     `json:"quantity"`
	Source         UsageSource `json:"source"`
	TariffRevision string      `json:"tariff_revision,omitempty"`
	Currency       string      `json:"currency,omitempty"`
	USD            float64     `json:"usd,omitempty"`
}
type Usage struct {
	Lines []UsageLine `json:"lines"`
}
type SubmitResult struct {
	ProviderJobID, ProviderState string
	Normalized                   State
	ProviderRequestID            string
	Raw                          json.RawMessage
}
type Observation struct {
	ProviderState string
	Normalized    State
	Progress      *int
	Outputs       []OutputRef
	Usage         *Usage
	Error         *ProviderError
	RetryAfter    *time.Duration
	Raw           json.RawMessage
}
type CancelState string

const (
	CancelConfirmed    CancelState = "confirmed"
	CancelRequested    CancelState = "requested"
	CancelNotAvailable CancelState = "not_available"
)

type CancelResult struct {
	State          CancelState
	ReleasesCharge bool
}
type ReconcileResult struct {
	Found         bool
	ProviderJobID string
	ProvenAbsent  bool
}
