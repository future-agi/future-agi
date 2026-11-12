"""Resolve a Hub ref once so metadata and asynchronous imports use one snapshot."""

from huggingface_hub import HfApi

from model_hub.models.api_key import ApiKey
from tfc.settings.settings import HUGGINGFACE_API_TOKEN
from tfc.utils.lazy_extras import load_extra


def huggingface_token(organization_id):
    key = ApiKey.objects.filter(
        organization_id=organization_id, provider="huggingface"
    ).first()
    # The org's own token when it has one: one this deployment cannot decrypt
    # raises (KeyUnreadable) rather than being swapped for the platform token.
    return (key.actual_key if key and key.key else None) or HUGGINGFACE_API_TOKEN


def revision_info(dataset_path, organization_id, revision="main"):
    """Only Hub datasets are accepted; local paths and executable scripts are not."""
    token = huggingface_token(organization_id)
    # HfApi validates the repo id and resolves branches/tags to an immutable commit.
    commit = (
        HfApi()
        .dataset_info(
            dataset_path, revision=revision or "main", token=token, timeout=30
        )
        .sha
    )
    if not commit:
        raise ValueError("The dataset revision could not be resolved")
    infos = load_extra("datasets", "ml").get_dataset_infos(
        dataset_path, revision=commit, token=token
    )
    return {
        "revision": commit,
        "splits": {name: list(info.splits or {}) for name, info in infos.items()},
        "num_rows": {
            name: {
                # The library uses zero when streaming metadata has no known size.
                split: details.num_examples
                if details.num_examples and details.num_examples > 0
                else None
                for split, details in (info.splits or {}).items()
            }
            for name, info in infos.items()
        },
    }


def import_revision(dataset_path, config, split, organization_id, revision, requested):
    info = revision_info(dataset_path, organization_id, revision)
    configs = info["num_rows"]
    if not config and len(configs) == 1:
        config = next(iter(configs))
    if config not in configs or split not in configs[config]:
        raise ValueError("The subset or split does not exist at this revision")
    total = configs[config][split]
    if total is None or total < 0:
        if not requested:
            raise ValueError("Specify a row count for a split without a known size")
        count = requested
    else:
        count = min(requested, total) if requested else total
    if count <= 0:
        raise ValueError("The selected split has no rows to import")
    return info["revision"], config, count
