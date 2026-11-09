package inclusive

import (
	"encoding/json"
	"fmt"
	"math/big"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
)

const MaxGenerations = 8
const MaxOutputTokens = 65536

// Bound uses the exact listing's image occurrence price plus a conservative
// token card at the enforced output cap; Lyria uses its listing price times n.
// A provider context ceiling bounds input, not the old bytes/4 heuristic.
// Unknown native extensions, audio/video/file inputs and server tools refuse
// before dispatch. Remote image URLs are counted, never fetched for billing.
func Bound(req *models.ChatCompletionRequest, q *models.InclusivePrice) (int64, error) {
	if req == nil || q == nil || q.Model != req.Model || len(req.Extra) != 0 || req.ServiceTier != "" {
		return 0, fmt.Errorf("unbounded or mismatched inclusive request")
	}
	n := 1
	if req.N != nil {
		n = *req.N
	}
	if n < 1 || n > MaxGenerations {
		return 0, fmt.Errorf("generation count must be between 1 and 8")
	}
	for _, tool := range req.Tools {
		if tool.Type != "function" || len(tool.Raw) != 0 {
			return 0, fmt.Errorf("server tools have no admission bound")
		}
	}
	images, err := imageCount(req)
	if err != nil {
		return 0, err
	}
	if q.Generation != "" {
		if images != 0 || len(req.Tools) != 0 {
			return 0, fmt.Errorf("unsupported Lyria input")
		}
		r, err := amount(q.Generation)
		if err != nil {
			return 0, err
		}
		_, hold, err := charge(r.Mul(r, big.NewRat(int64(n), 1)))
		return hold, err
	}
	if req.Audio != nil {
		return 0, fmt.Errorf("audio has no image-family bound")
	}
	for _, modality := range req.Modalities {
		switch modality {
		case "text":
		case "image":
			images += int64(n)
		default:
			return 0, fmt.Errorf("unbounded output modality")
		}
	}
	out := 0
	for _, v := range []*int{req.MaxTokens, req.MaxCompletionTokens} {
		if v != nil {
			if *v < 1 || *v > MaxOutputTokens {
				return 0, fmt.Errorf("output cap must be between 1 and 65536")
			}
			out = max(out, *v)
		}
	}
	if out == 0 {
		return 0, fmt.Errorf("explicit max_tokens required for inclusive billing")
	}
	if q.OutputLimit > 0 && int64(out) > q.OutputLimit {
		return 0, fmt.Errorf("output cap exceeds listing limit")
	}
	if q.ContextLimit < 1 || q.ContextLimit > 4000000 {
		return 0, fmt.Errorf("listing has no bounded input context")
	}
	if q.RouterUniverse != "" {
		var universe []routerbilling.Candidate
		if json.Unmarshal([]byte(q.RouterUniverse), &universe) != nil {
			return 0, routerbilling.ErrBound
		}
		plan, err := routerbilling.Bound(q.Model, q.SnapshotModels, false, universe, routerbilling.Caps{InputTokens: int(q.ContextLimit) * n, MaxTokens: out, N: n, Images: int(images)}, 1000_000_000)
		return plan.HoldMicros(), err
	}
	input, err := maxRate(q.Prompt, q.CacheRead, q.CacheWrite)
	if err != nil {
		return 0, err
	}
	output, err := maxRate(q.Completion, q.Reasoning)
	if err != nil {
		return 0, err
	}
	image, err := amount(q.Image)
	if err != nil {
		return 0, err
	}
	usd := input.Mul(input, big.NewRat(q.ContextLimit*int64(n), 1))
	usd.Add(usd, output.Mul(output, big.NewRat(int64(out)*int64(n), 1)))
	usd.Add(usd, image.Mul(image, big.NewRat(images, 1)))
	_, hold, err := charge(usd)
	if hold == 0 && err == nil {
		err = fmt.Errorf("paid listing has no positive hold")
	}
	return hold, err
}

func maxRate(values ...string) (*big.Rat, error) {
	result := new(big.Rat)
	for _, v := range values {
		if v == "" {
			v = "0"
		}
		r, err := amount(v)
		if err != nil {
			return nil, err
		}
		if r.Cmp(result) > 0 {
			result = r
		}
	}
	return result, nil
}

func imageCount(req *models.ChatCompletionRequest) (int64, error) {
	var n int64
	size := 0
	for _, msg := range req.Messages {
		size += len(msg.Content)
		if size > 32*1024*1024 || len(msg.ThinkingBlocks) != 0 || len(msg.Annotations) != 0 || len(msg.Images) != 0 {
			return 0, fmt.Errorf("opaque or oversized content")
		}
		if len(msg.Content) == 0 || string(msg.Content) == "null" {
			continue
		}
		var text string
		if json.Unmarshal(msg.Content, &text) == nil {
			continue
		}
		var parts []map[string]json.RawMessage
		if json.Unmarshal(msg.Content, &parts) != nil {
			return 0, fmt.Errorf("unknown content shape")
		}
		for _, part := range parts {
			switch string(part["type"]) {
			case `"text"`:
				if len(part) != 2 || part["text"] == nil {
					return 0, fmt.Errorf("opaque text part")
				}
			case `"image_url"`:
				if len(part) != 2 || part["image_url"] == nil {
					return 0, fmt.Errorf("opaque image part")
				}
				n++
				if n > 64 {
					return 0, fmt.Errorf("image count exceeds 64")
				}
			default:
				return 0, fmt.Errorf("unsupported media input")
			}
		}
	}
	return n, nil
}
