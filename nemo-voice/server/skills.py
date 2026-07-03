"""Markdown-defined skills for the voice agent (OpenClaw SKILL.md pattern).

Drop a folder ``skills/<name>/SKILL.md`` with frontmatter and the voice agent
gains a new tool — no code change. Each skill runs a fixed ``argv`` template
with ``{param}`` substitution. Execution is ``shell=False``, so what Avazbek
says (the params) can never inject a command — only the operator-authored argv
template runs. Good for self-contained CLIs (weather via wttr.in); an
external-API skill puts its key in the argv via ``$ENVVAR`` the operator sets.

Frontmatter (one ``key: value`` per line, JSON for lists/objects):
    name: get_weather
    description: Get the current weather for a place.
    params: ["location"]
    argv: ["curl", "-s", "wttr.in/{location}?format=3"]
    timeout: 15
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

LOG = logging.getLogger("nemo.skills")

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_MAX_OUTPUT = 2000
_MAX_PARAM = 200
_DEFAULT_TIMEOUT = 15


def _parse_frontmatter(text: str) -> dict:
    """Minimal `---`-delimited frontmatter parser (no YAML dep)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v[:1] in "[{":
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
        out[k] = v
    return out


def _load_skills() -> dict[str, dict]:
    """Discover + validate every skills/<name>/SKILL.md."""
    out: dict[str, dict] = {}
    if not _SKILLS_DIR.is_dir():
        return out
    for md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        try:
            fm = _parse_frontmatter(md.read_text())
        except OSError:
            continue
        name, argv = fm.get("name"), fm.get("argv")
        if not isinstance(name, str) or not _is_str_list(argv):
            LOG.warning("skill %s: missing/invalid name or argv", md.parent.name)
            continue
        params = fm.get("params") if _is_str_list(fm.get("params")) else []
        out[name] = {
            "name": name,
            "description": fm.get("description") or name,
            "params": params,
            "argv": argv,
            "timeout": _as_int(fm.get("timeout"), _DEFAULT_TIMEOUT),
            # The skill's own folder — auto-substituted as {skill_dir} so a skill
            # can invoke a helper script next to its SKILL.md, cwd-independent.
            "dir": str(md.parent),
        }
        LOG.info("loaded skill: %s", name)
    return out


def _is_str_list(v: object) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _as_int(v: object, default: int) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


_SKILLS = _load_skills()
TOOL_NAMES = set(_SKILLS)


def _to_function(skill: dict) -> dict:
    props = {
        p: {"type": "string", "description": f"value for {p}"} for p in skill["params"]
    }
    return {
        "name": skill["name"],
        "description": skill["description"],
        "parameters": {
            "type": "object",
            "properties": props,
            "required": skill["params"],
        },
    }


FUNCTIONS: list[dict] = [_to_function(s) for s in _SKILLS.values()]


def _substitute(arg: str, values: dict) -> str:
    for k, v in values.items():
        arg = arg.replace("{" + k + "}", str(v)[:_MAX_PARAM])
    return arg


def dispatch(name: str, args: dict) -> str:
    """Run a skill's argv (shell=False) and return its output as JSON."""
    skill = _SKILLS.get(name)
    if skill is None:
        return json.dumps({"error": f"unknown skill {name}"})
    # Param values (from the voice agent) substitute as DATA into fixed argv
    # slots; {skill_dir} is operator-trusted. shell=False → no injection.
    values = {p: args.get(p, "") for p in skill["params"]}
    values["skill_dir"] = skill["dir"]
    argv = [_substitute(a, values) for a in skill["argv"]]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=skill["timeout"],
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "the skill timed out"})
    except Exception as exc:  # noqa: BLE001 — never crash the voice turn
        LOG.warning("skill %s failed: %s", name, exc)
        return json.dumps({"error": str(exc)})
    output = (proc.stdout or proc.stderr or "").strip()[:_MAX_OUTPUT]
    return json.dumps({"result": output or "(no output)"})
