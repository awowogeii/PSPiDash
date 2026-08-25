"""config.yaml loader.

The standard library has no YAML parser and this project allows no third-party
runtime dependencies, so ``parse_simple_yaml`` implements the small subset
config.yaml actually uses: nested ``key: value`` mappings by indentation,
scalars (int / float / bool / null / quoted or bare strings), flow lists
(``[a, b]``), block lists of scalars (``- item``, as the settings page writes
them), and ``#`` comments. Multi-line strings are not supported.
"""
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = "config.yaml"


@dataclass
class Config:
    mac: str = "00:00:00:00:00:00"
    channel: int = 1  # serial-port channel; config.yaml key ends with "_channel"
    poll_hz: float = 10.0
    scaling_overrides: dict = field(default_factory=dict)


def _scalar(text):
    text = text.strip()
    if not text:
        return None
    if text[0] == "[" and text[-1] == "]":
        inner = text[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if (text[0] == text[-1]) and text[0] in "'\"" and len(text) >= 2:
        return text[1:-1]
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def parse_simple_yaml(text):
    """Parse the YAML subset described in the module docstring into a dict."""
    root = {}
    stack = [(-1, root, None, None)]  # (indent, container, parent, key)
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if body == "-" or body.startswith("- "):
            # scalar list item; YAML allows the dash at the parent key's indent
            while len(stack) > 1 and indent < stack[-1][0]:
                stack.pop()
            top_indent, container, parent, key = stack[-1]
            if parent is not None and isinstance(container, dict) and not container:
                container = []
                parent[key] = container
                stack[-1] = (top_indent, container, parent, key)
            if not isinstance(container, list):
                raise ValueError("config line %d: unexpected list item" % lineno)
            container.append(_scalar(body[1:]))
            continue
        if ":" not in body:
            raise ValueError("config line %d: expected 'key: value'" % lineno)
        key, _, value = body.partition(":")
        key = key.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("config line %d: bad indentation" % lineno)
        parent = stack[-1][1]
        if not isinstance(parent, dict):
            raise ValueError("config line %d: mapping key inside a list" % lineno)
        value = value.strip()
        if value == "" or value == "{}":
            child = {}
            parent[key] = child
            if value == "":
                stack.append((indent, child, parent, key))
        else:
            parent[key] = _scalar(value)
    return root


def config_from_dict(data):
    cfg = Config()
    if "mac" in data and data["mac"] is not None:
        cfg.mac = str(data["mac"])
    channel = data.get("channel")
    if channel is None:
        for key, value in data.items():
            if key.endswith("_channel") and value is not None:
                channel = value
                break
    if channel is not None:
        cfg.channel = int(channel)
    if "poll_hz" in data and data["poll_hz"] is not None:
        cfg.poll_hz = float(data["poll_hz"])
    overrides = data.get("scaling_overrides") or {}
    if not isinstance(overrides, dict):
        raise ValueError("scaling_overrides must be a mapping")
    cfg.scaling_overrides = overrides
    return cfg


def load_config(path=DEFAULT_CONFIG_PATH):
    """Load ``path``; a missing file yields defaults."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return Config()
    return config_from_dict(parse_simple_yaml(text))
