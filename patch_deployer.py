import re

with open("agents/deployer.py", "r") as f:
    content = f.read()

# Replace the specific lines inside _format_evidence
new_content = re.sub(
    r'ref = ev\.get\("reference", ""\)\s*reasoning = ev\.get\("reasoning", ""\)\s*ev_type = ev\.get\("type", "\?"\)',
    '# Try exact keys first, fall back to capitalized or alternate keys\n'
    '                ev_type = ev.get("type", ev.get("Type", ev.get("source", "?")))\n'
    '                ref = ev.get("reference", ev.get("Reference", ev.get("detail", "")))\n'
    '                reasoning = ev.get("reasoning", ev.get("Reasoning", ""))\n'
    '                # If nested in "evidence"\n'
    '                if ev_type == "?" and not ref and "evidence" in ev and isinstance(ev["evidence"], dict):\n'
    '                    nested = ev["evidence"]\n'
    '                    ev_type = nested.get("type", "?")\n'
    '                    ref = nested.get("reference", "")\n'
    '                    reasoning = nested.get("reasoning", "")',
    content
)

with open("agents/deployer.py", "w") as f:
    f.write(new_content)
