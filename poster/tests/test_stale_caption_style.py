"""#50: a draft style that prompts/ no longer has must not hide behind the
style dropdown.

A replayed pickup or a restored batch can hand a draft a removed style. No
<option> matched it, so the browser showed the first option while Regenerate
sent the stale value and failed. `knownStyle()` now gates every draft style.

The source-level tests run everywhere. The behavior tests execute the real
functions under node. CI pins node for this job; elsewhere they skip without it.
"""

import json
import re
import shutil
import subprocess

import pytest

from tests.test_frontend_robustness import _function_body, _script


def _function(signature: str) -> str:
    name = signature.split("(", 1)[0]
    return f"function {signature} {_function_body(name)}"


def test_every_draft_style_assignment_goes_through_known_style():
    """A draft style written without the gate is the #50 failure mode."""
    script = _script()
    target = r"\b(?:s|draft|state\.slots\[[^\]]+\])\.style = "
    assignments = re.findall(rf"^.*{target}.*$", script, re.M)
    ungated = [
        line.strip() for line in assignments
        if "knownStyle(" not in line
        # The account caption-default picker writes a style the server just
        # validated and persisted, so it cannot be unknown.
        and line.strip() != "state.slots[accountId].style = style;"
        # The dropdown writes one of its own options, which come from the list.
        and "].style = this.value" not in line
    ]
    assert ungated == []
    assert len(assignments) >= 7


def test_caption_requests_send_the_gated_style():
    """Generate and Regenerate both send the same value the dropdown shows."""
    script = _script()
    gated = "formData.append('style', s.style = knownStyle(s.style, slot));"
    assert script.count(gated) == 2
    assert "formData.append('style', s.style ||" not in script


def test_style_options_selects_the_gated_style():
    body = _function_body("styleOptions")
    assert "knownStyle(state.slots[slot].style, slot)" in body


def _run_node(program: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available; the behavior check needs a JS runtime")
    # esc() needs a DOM; the stub keeps the option markup readable.
    source = "const esc = s => String(s);\nconst escAttr = esc;\n" + "\n".join(
        _function(sig) for sig in ("knownStyle(style, slot)", "styleOptions(slot)")
    )
    proc = subprocess.run(
        [node, "--input-type=module"],
        input=source + "\n" + program,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


_STATE = """
const state = {
  captionStyles: [
    { name: 'sports', display_name: 'Sports' },
    { name: 'generic', display_name: 'Generic' },
  ],
  defaultCaptionStyle: 'generic',
  accountState: { caption_defaults: { A: 'sports', B: 'retired-default' } },
  slots: {
    A: { style: 'retired-style' },
    B: { style: 'retired-style' },
    C: { style: 'sports' },
  },
};
"""


def test_unknown_style_falls_back_and_the_dropdown_shows_what_is_sent():
    result = _run_node(_STATE + """
const html = { A: styleOptions('A'), B: styleOptions('B'), C: styleOptions('C') };
const selected = Object.fromEntries(Object.entries(html).map(
  ([slot, h]) => [slot, (h.match(/value="([^"]+)" selected/) || [])[1]]));
console.log(JSON.stringify({
  styles: Object.fromEntries(Object.entries(state.slots).map(([k, v]) => [k, v.style])),
  selected,
  empty: knownStyle(undefined, 'C'),
}));
""")
    # A: the account default. B: its default is also stale, so the server default.
    # C: a known style is kept.
    assert result["styles"] == {"A": "sports", "B": "generic", "C": "sports"}
    assert result["selected"] == result["styles"]
    assert result["empty"] == "generic"


def test_styles_not_loaded_yet_keep_the_draft_style():
    """Before /api/accounts answers there is no list to check against, so the
    gate must not overwrite a draft style it cannot judge."""
    result = _run_node(_STATE + """
state.captionStyles = [];
console.log(JSON.stringify({
  kept: knownStyle('meme-humor', 'A'),
  accountDefault: knownStyle('', 'A'),
  empty: knownStyle('', 'C'),
}));
""")
    assert result == {
        "kept": "meme-humor", "accountDefault": "sports", "empty": "generic",
    }
