"""Exception types shared across the package."""


class VautoError(Exception):
    """Base error for anything the tool reports to the user."""


class ConfigError(VautoError):
    """A required setting, token, or account id is missing or invalid."""


class MediaError(VautoError):
    """Media could not be probed, converted, or does not fit a platform."""


class PublishError(VautoError):
    """A platform rejected or failed a post.

    ``transient`` marks failures worth retrying (timeouts, 429, 5xx).
    """

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient
