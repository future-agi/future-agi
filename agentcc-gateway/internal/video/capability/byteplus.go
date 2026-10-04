package capability

import (
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"strings"
	"time"
)

const BytePlusRevision = "byteplus-2026-10-02"

// BytePlus contains only the seven IDs pinned in contract section 2. Unknown
// output ACL and billability remain unknown until an authorized smoke verifies them.
func BytePlus() video.Capabilities {
	c := video.Capabilities{Service: "byteplus", Revision: BytePlusRevision, Regions: []string{"ap-southeast-1"}, SubmitIdempotency: video.IdempotencyNone, CorrelationLookup: video.LookupHeuristic, CancelSupport: video.CancelQueuedOnly, OutputURLExpiry: 24 * time.Hour, OutputACL: video.ACLUnknown, BillsOnFailure: video.BillingUnknown, Webhook: video.WebhookUnauthenticated, PollMinInterval: 5 * time.Second}
	for _, row := range []struct {
		id                string
		min, max          int
		res               []string
		last, refs, audio bool
	}{
		{"dreamina-seedance-2-5-260628", 4, 30, []string{"480p", "720p", "1080p"}, true, true, true},
		{"dreamina-seedance-2-0-260128", 4, 15, []string{"480p", "720p", "1080p", "4k"}, true, true, true},
		{"dreamina-seedance-2-0-fast-260128", 4, 15, []string{"480p", "720p"}, true, true, true},
		{"dreamina-seedance-2-0-mini-260615", 4, 15, []string{"480p", "720p"}, true, true, true},
		{"seedance-1-5-pro-251215", 4, 12, []string{"480p", "720p", "1080p"}, true, false, true},
		{"seedance-1-0-pro-250528", 2, 12, []string{"480p", "720p", "1080p"}, true, false, false},
		{"seedance-1-0-pro-fast-251015", 2, 12, []string{"480p", "720p", "1080p"}, false, false, false},
	} {
		formats := []string{"jpeg", "png", "webp", "bmp", "tiff", "gif"}
		if !strings.HasPrefix(row.id, "seedance-1-0-") {
			formats = append(formats, "heic", "heif")
		}
		image := video.InputSpec{MinCount: 1, MaxCount: 1, MaxBytes: 30 * 1024 * 1024, MaxPixels: 36_000_000, MinDimension: 300, MaxDimension: 6000, MinRatio: 0.4, MaxRatio: 2.5, Formats: formats}
		m := video.ModelCapability{ModelID: row.id, Operations: map[video.Operation]video.OperationSpec{video.TextToVideo: {}, video.ImageFirstFrame: {Roles: []video.Role{video.FirstFrame}, RequiredRoles: []video.Role{video.FirstFrame}}}, Inputs: map[video.Role]video.InputSpec{video.FirstFrame: image}, Durations: video.IntRange{Min: row.min, Max: row.max}, Resolutions: row.res, AspectRatios: []string{"21:9", "16:9", "4:3", "1:1", "3:4", "9:16", "adaptive"}, FPS: []int{24}, MaxOutputs: 1, PromptLimit: video.PromptLimit{Unit: "bytes", Max: 16 * 1024}, Options: map[string]video.OptionSpec{
			"watermark": {Type: "bool", Default: false}, "camera_fixed": {Type: "bool", Default: false}, "return_last_frame": {Type: "bool", Default: false}, "frames": {Type: "int", Min: 29, Max: 289, Step: 4, Offset: 25}, "service_tier": {Type: "enum", Values: []string{"default"}, Default: "default"},
		}, Tariff: &video.TariffRef{Unit: video.VideoTokens, Revision: BytePlusRevision}, Access: video.AccessDocumented, Audio: video.AudioSpec{Generated: "never"}}
		if row.audio {
			m.Audio.Generated = "optional_default_true"
		}
		if row.last {
			m.Inputs[video.LastFrame] = image
			m.Operations[video.ImageFirstLast] = video.OperationSpec{Roles: []video.Role{video.FirstFrame, video.LastFrame}, RequiredRoles: []video.Role{video.FirstFrame, video.LastFrame}}
		}
		if row.refs {
			m.Access = video.AccessGated
			m.AutoDuration = &video.AutoDurationSpec{Value: -1, Bounded: true}
			// Where the pinned source has no verified count, permit a single reference
			// per role; widening these limits requires a new evidence-backed revision.
			if strings.Contains(row.id, "2-5-") {
				image.MaxCount = 30
				m.Audio.AudioOnly = true
				for _, op := range []video.Operation{video.ImageFirstFrame, video.ImageFirstLast} {
					spec := m.Operations[op]
					spec.AspectRatios = []string{"adaptive"}
					m.Operations[op] = spec
				}
			}
			m.Inputs[video.ReferenceImage] = image
			m.Inputs[video.ReferenceVideo] = video.InputSpec{MinCount: 1, MaxCount: 1, Formats: []string{"mp4", "quicktime"}}
			m.Inputs[video.ReferenceAudio] = video.InputSpec{MinCount: 1, MaxCount: 1, Formats: []string{"mpeg", "mp3", "wav"}}
			m.Audio.InputRefs = []video.Role{video.ReferenceAudio}
			m.Operations[video.Reference] = video.OperationSpec{Roles: []video.Role{video.ReferenceImage, video.ReferenceVideo, video.ReferenceAudio}}
		} else {
			m.Options["seed"] = video.OptionSpec{Type: "int", Min: -1, Max: 2147483647}
		}
		c.Models = append(c.Models, m)
	}
	return c
}
