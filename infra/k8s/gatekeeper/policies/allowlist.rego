package allowlist

violation[{"msg": msg}] {
    # If the file being patched is not in the allowlist
    not allowed_path(input.file_path)
    msg := sprintf("Patch targets unauthorized file path: %v", [input.file_path])
}

allowed_path(path) {
    startswith(path, "sample-app/")
}

allowed_path(path) {
    path == "infra/k8s/sample-app.yaml"
}
