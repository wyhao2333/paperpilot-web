"""Resolve task models without copying credentials between endpoints."""


def ai_for(settings, purpose="translation"):
    legacy = settings["ai"]
    selected = settings.get("ai_tasks", {}).get(purpose)
    if not selected:
        return legacy
    provider = selected["provider"]
    profile = settings.get("ai_profiles", {}).get(provider)
    if profile is None:
        profile = legacy if legacy.get("provider") == provider else {}
    return {**profile, "provider": provider, "model": selected["model"]}


def translation_concurrency(settings, engine):
    requested = max(1, min(16, int(settings.get("translation_concurrency", 6))))
    # Public web endpoints and entry-level official MT plans have tighter limits.
    caps = {"baidu": 1, "mymemory": 2, "bing": 2, "google": 4,
            "youdao": 2, "microsoft": 4}
    return min(requested, caps.get(engine, 16))
