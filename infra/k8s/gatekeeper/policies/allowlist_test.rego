package allowlist

test_sample_app_file_allowed {
    count(violation) == 0 with input as {"file_path": "sample-app/app.py"}
}

test_nested_sample_app_file_allowed {
    count(violation) == 0 with input as {"file_path": "sample-app/lib/util.py"}
}

test_sample_app_manifest_allowed {
    count(violation) == 0 with input as {"file_path": "infra/k8s/sample-app.yaml"}
}

test_backend_blocked {
    count(violation) == 1 with input as {"file_path": "backend/main.py"}
}

test_agent_code_blocked {
    count(violation) == 1 with input as {"file_path": "agents/validation_officer.py"}
}

test_policy_file_blocked {
    count(violation) == 1 with input as {"file_path": "infra/k8s/gatekeeper/policies/allowlist.rego"}
}

test_other_manifest_blocked {
    count(violation) == 1 with input as {"file_path": "infra/k8s/vault-deployment.yaml"}
}

test_path_traversal_blocked {
    count(violation) == 1 with input as {"file_path": "sample-app/../backend/main.py"}
}

test_prefix_lookalike_blocked {
    count(violation) == 1 with input as {"file_path": "sample-app-evil/app.py"}
}

test_empty_path_blocked {
    count(violation) == 1 with input as {"file_path": ""}
}
