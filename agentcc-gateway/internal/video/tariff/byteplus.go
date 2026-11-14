package tariff

import "fmt"

const Revision = "byteplus-2026-10-02"

// Online list rates only. Promotions, offline and draft pricing are excluded.
func BytePlusRate(model, resolution string, inputVideo, audio bool) (Rate, error) {
	var without, with float64
	switch model {
	case "dreamina-seedance-2-5-260628":
		switch resolution {
		case "480p", "720p":
			without, with = 10.7, 6.4
		case "1080p":
			without, with = 11.7, 7
		}
	case "dreamina-seedance-2-0-260128":
		switch resolution {
		case "480p", "720p":
			without, with = 7, 4.3
		case "1080p":
			without, with = 7.7, 4.7
		case "4k":
			without, with = 4, 2.4
		}
	case "dreamina-seedance-2-0-fast-260128":
		if resolution == "480p" || resolution == "720p" {
			without, with = 5.6, 3.3
		}
	case "dreamina-seedance-2-0-mini-260615":
		if resolution == "480p" || resolution == "720p" {
			without, with = 3.5, 2.1
		}
	case "seedance-1-5-pro-251215":
		if standardResolution(resolution) {
			without = 1.2
			if audio {
				without = 2.4
			}
		}
	case "seedance-1-0-pro-250528":
		if standardResolution(resolution) && !audio {
			without = 2.5
		}
	case "seedance-1-0-pro-fast-251015":
		if standardResolution(resolution) && !audio {
			without = 1
		}
	}
	rate := without
	if inputVideo {
		rate = with
	}
	if rate == 0 {
		return Rate{}, fmt.Errorf("%w: byteplus model/resolution/input combination", ErrUnpriced)
	}
	return Rate{Unit: VideoTokens, Revision: Revision, Currency: "USD", USDPerMillion: rate}, nil
}
func standardResolution(s string) bool { return s == "480p" || s == "720p" || s == "1080p" }
