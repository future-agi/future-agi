import logging
import sys

PACKAGE_LOGGER_NAME = "fi.opt"


def setup_logging(
    level=logging.INFO,
    log_to_console: bool = True,
    log_to_file: bool = False,
    log_file: str = "prompt_optimizer.log",
    disabled: bool = False,
    filemode: str = "a",
):
    """
    Provides a flexible way to configure the root logger for library.

    This function should be called once at the beginning of the user's script
    to control the logging output of the optimizer.

    Args:
        level (str): The logging level to set (e.g., "logging.DEBUG", "logging.INFO", "logging.WARNING").
                     Defaults to "logging.INFO".
        log_file (Optional[str]): If provided, logs will be written to this file.
        disabled (bool): If True, all logging will be disabled. Defaults to False.
        filemode (str): The mode to open the log file in ('w' for write, 'a' for append).
                        Defaults to 'a'.
    """
    # Configure third-party loggers to be less verbose
    third_party_loggers = ["LiteLLM", "openai", "httpcore"]
    for logger_name in third_party_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    if not log_to_console and not log_to_file:
        # If both are disabled, add a NullHandler to prevent any output
        logger.addHandler(logging.NullHandler())
        print("Prompt Optimizer logging is disabled.")
        return

    # Add a handler for logging to the console (stdout)
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # Add a handler for logging to a file
    if log_to_file:
        file_handler = logging.FileHandler(log_file, filemode)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Provide clear feedback to the user about the configuration
    log_destinations = []
    if log_to_console:
        log_destinations.append("console")
    if log_to_file:
        log_destinations.append(f"file ('{log_file}')")

    if disabled:
        disable_optimizer_logging()
        return
    else:
        return logger


def disable_optimizer_logging() -> None:
    """
    Completely disables all logging output from the prompt_optimizer library
    by removing all handlers and adding a NullHandler.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
