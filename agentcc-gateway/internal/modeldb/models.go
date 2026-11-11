package modeldb

import (
	_ "embed"
	"log"
)

//go:embed litellm.json
var liteLLMData []byte

// priceScheduleData holds dated prices kept apart from litellm.json, which
// the pricing refresh rewrites wholesale.
//
//go:embed price_schedule.json
var priceScheduleData []byte

var BundledModels map[string]*ModelInfo

func init() {
	var err error
	BundledModels, err = parseLiteLLM(liteLLMData)
	if err != nil {
		log.Fatalf("failed to parse embedded litellm pricing data: %v", err)
	}
	addDecisionModels(BundledModels)
	schedule, err := parsePriceSchedule(priceScheduleData)
	if err != nil {
		log.Fatalf("failed to parse embedded price schedule: %v", err)
	}
	if err := attachPriceSchedule(BundledModels, schedule); err != nil {
		log.Fatalf("failed to attach embedded price schedule: %v", err)
	}
}
