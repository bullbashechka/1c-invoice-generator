"""Anonymizer package exports."""

__all__ = ["AnonymizerRegistry"]


def __getattr__(name):
    if name == "AnonymizerRegistry":
        from .registry import AnonymizerRegistry
        return AnonymizerRegistry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
