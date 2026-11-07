"""Builds design-system/tokens.css from design-system/tokens.json, the one source of the cockpit's tokens.

The layout is the one the Design System artifact compiles (its format reference, "tokens.css as compiled"): the
first theme's colours and shadows on :root and [data-theme="<first>"], one [data-theme="<id>"] block per further
theme with what that theme changes, then :root with spacing, radius, the other token families and --font-<family>,
and one class per type style. One block more than the artifact writes: the System setting follows the device's dark
mode (:root without a data-theme), because the design system's README says dark follows the system preference and the
app's System choice has always worked that way.

tokens.json is mirrored from the artifact, where other people can edit it, so every value is checked against the
format's grammar before it is written: a value like "red; } body { …" must never reach the page.

It also writes web/ds-components.css: the sections of design-system/components/bundle.css the cockpit has adopted
(ADOPTED), word for word, inside one cascade layer. The layer sits under the app's own rules, so an adopted component
shows exactly where the app no longer has a rule of its own for it, and a page's own spacing keeps its say. The file
first fixes the layer order, base before design-system: the page's element defaults (style.css, "button { color:
inherit }") sit in base, under the components, because a rule outside every layer would beat them all. The other
sections (Hank's drawer, tiles, camera, tables ...) would restyle pages that keep their own look; a section joins
when a page adopts it.
Usage: python scripts/build-tokens.py [--check]   (--check: exit 1 when either file is not what its source gives)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DESIGN = REPO / "design-system"
SOURCE = DESIGN / "tokens.json"
TARGET = DESIGN / "tokens.css"
BUNDLE = DESIGN / "components" / "bundle.css"
COMPONENTS = REPO / "web" / "ds-components.css"
ADOPTED = ("Button", "StatusChip", "Banner", "SegmentedControl", "Disclosure", "Card", "DeviceCard", "Timeline",
           "SidebarNav")
UNSAFE_CSS = re.compile(r"(?i)url\(|@import|expression\(|javascript:|</|<!--|\\[0-9a-f]")

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
HEX = re.compile(r"^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
COLOR_FN = re.compile(r"^(?:rgba?|hsla?|oklch|oklab|lab|lch|color)\([a-z0-9.,%\s/+-]*\)$")
ALIAS = re.compile(r"^\{([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\}$")
LENGTH = re.compile(r"^(?:0|-?\d+(?:\.\d+)?(?:px|rem|em|%))$")
PLAIN = re.compile(r"^[A-Za-z0-9 #%(),./+_-]{1,200}$")  # an "other family" value: no url(), no var(), no ; { }
SHADOW = re.compile(r"^[a-z0-9#.,%\s()+-]{1,400}$")
FAMILY = re.compile(r"""^[^;{}<>\\()]{1,200}$""")
WEIGHT = re.compile(r"^(?:[1-9]\d{0,2}|1000)(?: (?:[1-9]\d{0,2}|1000))?$")
TRANSFORM = {"none", "uppercase", "lowercase", "capitalize"}
OWN_FAMILIES = {"name", "version", "color", "type", "spacing", "radius", "shadow"}  # the rest are "other" families


class TokenError(ValueError):
    pass


def prop(name: str) -> str:
    if not NAME.match(name):
        raise TokenError(f"token name {name!r} breaks the grammar")
    return "--" + name.replace(".", "\\.")


def length(value: object, what: str) -> str:
    text = f"{value}px" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
    if not LENGTH.match(text):
        raise TokenError(f"{what}: {value!r} is not a length")
    return text


def color(value: object, what: str) -> str:
    text = str(value).strip().lower() if not ALIAS.match(str(value)) else str(value)
    alias = ALIAS.match(text)
    if alias:
        return f"var({prop(alias.group(1))})"
    if HEX.match(text) or (COLOR_FN.match(text) and "var(" not in text and "url(" not in text):
        return text
    raise TokenError(f"{what}: {value!r} is not a colour")


def shadow(value: object, what: str) -> str:
    text = str(value).strip()
    if text == "none" or (SHADOW.match(text.lower()) and "var(" not in text and "url(" not in text
                          and text.count("(") == text.count(")")):
        return text
    raise TokenError(f"{what}: {value!r} is not a shadow")


def plain(value: object, what: str) -> str:
    text = str(value).strip()
    if PLAIN.match(text) and "url(" not in text and "var(" not in text and text.count("(") == text.count(")"):
        return text
    raise TokenError(f"{what}: {value!r} is not a plain CSS value")


def per_theme(token: dict, themes: list[str], check) -> dict[str, str]:
    """Theme id -> CSS value. A plain string is the first theme; a value only a later theme has is borrowed."""
    raw = token["value"]
    values = {themes[0]: raw} if not isinstance(raw, dict) else {k: v for k, v in raw.items() if k in themes}
    if not values:
        raise TokenError(f"{token['name']}: no value in any theme")
    if themes[0] not in values:
        values[themes[0]] = next(values[t] for t in themes if t in values)
    return {theme: check(value, token["name"]) for theme, value in values.items()}


def declarations(rows: list[tuple[str, str]], indent: str = "  ") -> list[str]:
    return [f"{indent}{name}: {value};" for name, value in rows]


def build(tokens: dict) -> str:
    themes = [t["id"].lower() for t in tokens["color"]["themes"]]
    first = themes[0]
    themed = [(t, per_theme(t, themes, color)) for t in tokens["color"]["tokens"]]
    themed += [(t, per_theme(t, themes, shadow)) for t in tokens.get("shadow", {}).get("tokens", [])]
    out = [f"/* {tokens['name']} — generated from tokens.json */"]
    out += [f':root, [data-theme="{first}"] {{', *declarations([(prop(t["name"]), v[first]) for t, v in themed]), "}"]
    for theme in themes[1:]:
        # what the theme changes, and every alias again so it resolves against this theme's values
        rows = [(prop(t["name"]), v[theme]) for t, v in themed
                if theme in v and (v[theme] != v[first] or v[theme].startswith("var("))]
        out += [f'[data-theme="{theme}"] {{', *declarations(rows), "}"]
        if theme == "dark":
            out += ["@media (prefers-color-scheme: dark) {", "  :root:not([data-theme]) {",
                    *declarations(rows, "    "), "  }", "}"]
    rows = [(prop(t["name"]), length(t["value"], t["name"])) for t in tokens["spacing"]["tokens"]]
    rows += [(prop(t["name"]), length(t["value"], t["name"])) for t in tokens["radius"]["tokens"]]
    for family, body in tokens.items():
        if family not in OWN_FAMILIES and isinstance(body, dict) and isinstance(body.get("tokens"), list):
            rows += [(prop(t["name"]), plain(t["value"], t["name"])) for t in body["tokens"]]
    for key, stack in tokens["type"]["families"].items():
        if not FAMILY.match(stack) or stack.count('"') % 2 or stack.count("'") % 2:
            raise TokenError(f"font family {key!r}: {stack!r} is not a font stack")
        rows.append((prop(f"font-{key.lower()}"), stack))
    out += [":root {", *declarations(rows), "}"]
    for group in tokens["type"]["groups"]:
        for style in group["styles"]:
            if not NAME.match(style["name"]):
                raise TokenError(f"type style {style['name']!r} breaks the grammar")
            family = (style.get("family") or group["family"]).lower()
            parts = [f"font-family: var({prop('font-' + family)})", f"font-size: {length(style['fontSize'], style['name'])}"]
            if "lineHeight" in style:
                lh = style["lineHeight"]
                unitless = isinstance(lh, (int, float)) and not isinstance(lh, bool) and 0 < lh < 10
                parts.append(f"line-height: {lh if unitless else length(lh, style['name'])}")
            if "fontWeight" in style:
                weight = {"normal": "400", "bold": "700"}.get(str(style["fontWeight"]), str(style["fontWeight"]))
                if not WEIGHT.match(weight):
                    raise TokenError(f"{style['name']}: font weight {weight!r}")
                parts.append(f"font-weight: {weight}")
            if "letterSpacing" in style:
                parts.append(f"letter-spacing: {length(style['letterSpacing'], style['name'])}")
            if "textTransform" in style:
                if style["textTransform"] not in TRANSFORM:
                    raise TokenError(f"{style['name']}: text transform {style['textTransform']!r}")
                parts.append(f"text-transform: {style['textTransform']}")
            selector = "." + style["name"].replace(".", "\\.")
            out.append(f"{selector} {{ {'; '.join(parts)}; }}")
    return "\n".join(out) + "\n"


def components(bundle: str) -> str:
    """The adopted sections of bundle.css, in its own order, inside @layer design-system."""
    parts = re.split(r"^/\* ---- (\w+) ---- \*/\n", bundle, flags=re.M)
    sections = dict(zip(parts[1::2], parts[2::2]))
    missing = [name for name in ADOPTED if name not in sections]
    if missing:
        raise TokenError(f"bundle.css has no section {missing}")
    body = "".join(f"/* ---- {name} ---- */\n{text.strip()}\n" for name, text in sections.items() if name in ADOPTED)
    unsafe = UNSAFE_CSS.search(body)
    if unsafe:  # bundle.css comes from the artifact too: nothing in it may load or reach anything
        raise TokenError(f"bundle.css: {unsafe.group(0)!r} in an adopted section")
    lines = [f"  {line}" if line else line for line in body.rstrip("\n").split("\n")]
    head = ("/* Generated by scripts/build-tokens.py from design-system/components/bundle.css: the sections the cockpit "
            "has adopted,\n   in one layer under the app's own rules. Edit bundle.css or ADOPTED and run the script, "
            "never this file. */")
    order = "@layer base, design-system;  /* the page's element defaults (style.css), then the components */"
    return "\n".join([head, order, "@layer design-system {", *lines, "}"]) + "\n"


def main(argv: list[str]) -> int:
    outputs = {TARGET: build(json.loads(SOURCE.read_text(encoding="utf-8"))),
               COMPONENTS: components(BUNDLE.read_text(encoding="utf-8"))}
    if "--check" in argv:
        stale = [path for path, css in outputs.items()
                 if not path.exists() or path.read_text(encoding="utf-8") != css]
        for path in stale:
            print(f"{path.relative_to(REPO)} is out of date: run python scripts/build-tokens.py", file=sys.stderr)
        return 1 if stale else 0
    for path, css in outputs.items():
        path.write_text(css, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
