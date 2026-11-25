import random
import string
from datetime import UTC, datetime


def generate_unique_dataset_name(prefix="Dataset-", separator="-"):
    """Generates a unique name using the current timestamp with separators for readability.

    Args:
        prefix (str): Optional. A prefix for the generated name.
        separator (str): The separator to use between date and time components.

    Returns:
        str: A unique name based on the current timestamp
    """
    # Get the current datetime with desired precision
    current_time = datetime.now()
    # Format the datetime into a string with separators
    time_str = current_time.strftime(
        f"%Y{separator}%m{separator}%d{separator}%H{separator}%M{separator}%S"
    )
    # Combine the prefix and the formatted time string to create a unique name
    return prefix + time_str


def generate_eval_display_name(eval_display_name: str) -> str:
    # Get current UTC timestamp in human-readable format
    timestamp = datetime.now(UTC).strftime("%B%d_%Y_%H%M%S")

    # Generate a random suffix
    random_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))

    # Combine to form the display name
    eval_display_name = f"{eval_display_name}_{timestamp}_{random_suffix}"

    return eval_display_name
