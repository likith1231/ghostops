with open('/home/likith/Downloads/GhostOps — Incident Dashboard-1786884123559.json', 'r') as f:
    new_json_str = f.read()

with open('infra/k8s/monitoring/grafana-dashboard.yaml', 'r') as f:
    lines = f.readlines()

out = []
for line in lines:
    out.append(line)
    if line.startswith("  ghostops-incidents.json: |"):
        break

# Add indented json
for line in new_json_str.split("\n"):
    # Split will yield empty string at end if there's a trailing newline
    if line or not out[-1].endswith("\n"):
        out.append("    " + line + "\n")

with open('infra/k8s/monitoring/grafana-dashboard.yaml', 'w') as f:
    f.writelines(out)

print("File updated successfully.")
