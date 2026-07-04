"""Custom exception hierarchy for the repricing engine.

Never swallow exceptions silently — raise one of these so callers can react.
"""


class RepricingError(Exception):
    """Base class for all repricing-engine errors."""


class ConfigurationError(RepricingError):
    """Raised when configuration / settings are invalid or missing."""


class IngestionError(RepricingError):
    """Raised when a CSV source cannot be read or parsed into models."""


class NormalizationError(RepricingError):
    """Raised when a value cannot be normalized (e.g. an unparseable price)."""


class MatchingError(RepricingError):
    """Raised when the matching pipeline fails irrecoverably."""


class AiQualityGateError(RepricingError):
    """Raised when the AI quality gate fails (after retries)."""


class SourceFetchError(RepricingError):
    """Raised when a source provider cannot complete a search.

    Orchestration guards each provider so one failure can't sink the batch;
    this is the typed error a provider raises before that guard logs and degrades.
    """


class PdpVerificationError(RepricingError):
    """Raised when PDP verification fails irrecoverably for a candidate.

    Per-candidate failures are normally caught and degraded (the original offer
    is kept, ``pdp_verified=False``); this is reserved for unexpected faults.
    """
