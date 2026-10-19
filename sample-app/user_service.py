"""User profile helpers for the sample app (scenario A1: an unhandled exception)."""


def display_name(profile):
    """Return the user's display name, or "anonymous" when the upstream payload is empty."""
    return profile["name"].strip()
