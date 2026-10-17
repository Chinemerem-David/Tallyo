"""Every colour or size a stylesheet asks for with var(--name) must be defined somewhere.
An undefined one is drawn as 'nothing' by the browser (this once made chart bars invisible)."""
import re, unittest
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parent.parent / "static" / "css"


class CssTokens(unittest.TestCase):
    def test_no_undefined_variables(self):
        defined, used = set(), {}
        for f in CSS_DIR.glob("*.css"):
            text = f.read_text()
            defined |= set(re.findall(r"(--[a-z0-9-]+)\s*:", text))
            for name in re.findall(r"var\((--[a-z0-9-]+)", text):
                used.setdefault(name, set()).add(f.name)
        missing = {n: sorted(fs) for n, fs in used.items() if n not in defined}
        self.assertEqual(missing, {})

    def test_every_theme_colour_exists_in_both_themes(self):
        text = (CSS_DIR / "base.css").read_text()
        light = set(re.findall(r"(--[a-z0-9-]+)\s*:", text.split('html[data-theme="dark"]')[0]))
        dark_block = text.split('html[data-theme="dark"]')[1].split("/* 2. RESET")[0]
        dark = set(re.findall(r"(--[a-z0-9-]+)\s*:", dark_block))
        colours = {n for n in light if n not in {"--font-sans", "--font-serif", "--sidebar-width"} and not re.match(r"--(space|radius)", n)}
        self.assertEqual(sorted(colours - dark - {"--sidebar-text", "--sidebar-muted", "--sidebar-active-bg", "--sidebar-active-text"}), [])


if __name__ == "__main__":
    unittest.main()
