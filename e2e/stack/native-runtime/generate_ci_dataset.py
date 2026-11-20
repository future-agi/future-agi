"""Print the synthetic retained-CI input. No customer records or network inputs."""

import json


def case(name, request, expected_action, *, age_days, disputed=False):
    return {
        "name": name,
        "request": request,
        "state": {"age_days": age_days, "amount": 25, "disputed": disputed},
        "expected_action": expected_action,
    }


if __name__ == "__main__":
    # These prompts are the inputs recognized by j2-fixture.ts's model rules.
    # The expected actions are the retained pipeline's independent ledger oracle.
    dataset = {
        "evidence_class": "synthetic fixture",
        "pipeline": "synthetic-support-regression",
        "cases": [
            case(
                "refund",
                "My ceramic mug arrived cracked. Please refund this damaged order.",
                "issue_refund",
                age_days=10,
            ),
            case(
                "chargeback",
                "I do not recognize this charge on my card statement. Dispute the charge.",
                "open_chargeback",
                age_days=10,
                disputed=True,
            ),
            case(
                "shipping",
                "Where is my package? Please track the shipment.",
                "track_shipment",
                age_days=5,
            ),
        ],
    }
    print(json.dumps(dataset, indent=2))
