# OPA Gatekeeper Policy Enforcement

This directory contains Open Policy Agent (OPA) Gatekeeper constraints and templates to enforce security and resource limits on Kubernetes deployments.
These policies are used both by the cluster (via Gatekeeper admission controller) and by the GhostOps Validation Officer agent (via `conftest`) to reject unsafe AI-generated patches.

## Installation

Install Gatekeeper into your local Kind cluster using Helm:

```bash
helm repo add gatekeeper https://open-policy-agent.github.io/gatekeeper/charts
helm repo update

# Install Gatekeeper
helm install gatekeeper/gatekeeper \
  --name-template=gatekeeper \
  --namespace gatekeeper-system \
  --create-namespace

# Apply the ConstraintTemplates and Constraints
kubectl apply -f policies/
```

## Policies

- **K8sContainerLimits**: Ensures that CPU and memory limits are defined on all containers.
- **K8sNoPrivileged**: Denies containers that request `privileged: true` or `hostNetwork: true`.
- **allowlist.rego**: (Agent only) Enforces that patches only touch authorized files (e.g. `sample-app/`).
