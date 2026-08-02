"""Project registry — the single source of truth linking each project to where its
pieces live: the folder on disk, git remote, ClickUp list, Slack dev-log channel,
and plan.md. This is what lets "let's work on project iC" resolve instantly instead
of searching GitHub + ClickUp + Slack blindly.

Stored as JSON at config.PROJECTS_REGISTRY:
  { "<canonical name>": {name, folder, git_remote, clickup_list_id, clickup_url,
                         slack_channel_id, plan, kind, created} , ... }
"""

import json
import os
import re

from . import config


def _key(name):
    return re.sub(r"[\s_\-]+", " ", (name or "").lower()).strip()


def _load():
    try:
        with open(config.PROJECTS_REGISTRY, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        os.makedirs(os.path.dirname(config.PROJECTS_REGISTRY), exist_ok=True)
        with open(config.PROJECTS_REGISTRY, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def register(name, **fields):
    """Create/merge a project entry. Returns the stored record."""
    d = _load()
    rec = d.get(_key(name), {})
    rec["name"] = rec.get("name") or name
    rec.update({k: v for k, v in fields.items() if v is not None})
    d[_key(name)] = rec
    _save(d)
    return rec


def get(name):
    """Look up a project by name — exact, then fuzzy (substring either way)."""
    d = _load()
    k = _key(name)
    if k in d:
        return d[k]
    for key, rec in d.items():
        if k and (k in key or key in k):
            return rec
    return None


def all_projects():
    return list(_load().values())
