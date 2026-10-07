#!/usr/bin/env bash

set -euo pipefail

# The validated gateway build is upstream main @ 8719fc9, which has no tagged CLI release.
# The installer below comes from that commit; it installs the rolling "dev" CLI, which can be
# newer than the gateway. The CLI used in validation was built from source at 8719fc9.
readonly OPENSHELL_VERSION="dev"
readonly INSTALLER_COMMIT="8719fc9f37a93dd96435cf6753ae53c8ee8809e6"
readonly INSTALLER_SHA256="5c98a86a4b811c471b212219cb2a62d458244220ffa71ac8e3baf3700b17b871"
readonly INSTALLER_URL="https://raw.githubusercontent.com/NVIDIA/OpenShell/${INSTALLER_COMMIT}/install.sh"

usage() {
    cat <<'EOF'
Usage: install-openshell-cli.sh [--verify-only]

Downloads the commit-pinned OpenShell installer, verifies its SHA-256, and
installs the pinned OpenShell CLI release. Use --verify-only to validate the
upstream installer without executing it.
EOF
}

verify_sha256() {
    local file="$1"
    local actual_sha256

    if command -v sha256sum &> /dev/null; then
        actual_sha256="$(sha256sum "${file}" | awk '{print $1}')"
    elif command -v shasum &> /dev/null; then
        actual_sha256="$(shasum --algorithm 256 "${file}" | awk '{print $1}')"
    else
        echo "ERROR: sha256sum or shasum is required to verify the installer." >&2
        return 1
    fi

    [[ "${actual_sha256}" == "${INSTALLER_SHA256}" ]]
}

verify_only=false
case "${1:-}" in
    "") ;;
    --verify-only) verify_only=true ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

if [[ $# -gt 1 ]]; then
    usage >&2
    exit 2
fi

if ! command -v curl &> /dev/null; then
    echo "ERROR: curl is required to download the OpenShell installer." >&2
    exit 1
fi

installer_file="$(mktemp "${TMPDIR:-/tmp}/openshell-install.XXXXXX")"
trap 'rm -f -- "${installer_file}"' EXIT

echo "Downloading OpenShell installer from commit ${INSTALLER_COMMIT}..."
curl --fail --silent --show-error --location --output "${installer_file}" "${INSTALLER_URL}"

if ! verify_sha256 "${installer_file}"; then
    echo "ERROR: OpenShell installer SHA-256 verification failed; refusing to execute it." >&2
    exit 1
fi

echo "Verified OpenShell installer SHA-256: ${INSTALLER_SHA256}"

if [[ "${verify_only}" == true ]]; then
    exit 0
fi

echo "Installing OpenShell ${OPENSHELL_VERSION}..."
env OPENSHELL_VERSION="${OPENSHELL_VERSION}" sh "${installer_file}"
