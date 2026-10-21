"""User profile helpers for the sample app (scenario A1: an unhandled exception)."""


def display_name(profile):
    """Return the user's display name, or "anonymous" when the upstream payload is empty."""
    if not profile or not profile.get("name"):
        return "anonymous"
    return profile["name"].strip()
