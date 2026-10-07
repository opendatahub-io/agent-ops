#!/usr/bin/env bash
# Print what to send a new user: the dashboard link and the one CLI command. Contains no secrets.
set -euo pipefail
. "$(dirname "$0")/env.sh"
gw="https://$(oc -n "${NAMESPACE}" get route openshell -o jsonpath='{.spec.host}')"
ca=$(oc -n "${NAMESPACE}" get secret openshell-client-tls -o jsonpath='{.data.ca\.crt}' | base64 -d)
cat <<EOF
Dashboard:  https://${DASHBOARD_HOST}   (sign in with your company account)

CLI (once, then 'openshell gateway login ${NAMESPACE}' when your session expires):

  mkdir -p ~/.config/openshell/gateways/${NAMESPACE}/mtls
  cat > ~/.config/openshell/gateways/${NAMESPACE}/mtls/ca.crt <<'CA'
${ca}
CA
  openshell gateway add ${gw} --name ${NAMESPACE} \\
    --oidc-issuer ${ISSUER} --oidc-client-id ${CLI_CLIENT} --oidc-audience ${GATEWAY_AUDIENCE}

No browser on this machine? Prefix the command with OPENSHELL_NO_BROWSER=1 to log in with a code.
EOF
