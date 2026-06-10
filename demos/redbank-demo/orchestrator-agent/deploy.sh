#!/bin/bash

# Deploy the RedBank Orchestrator Agent to OpenShift via Helm.

SCRIPT_FOLDER="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

function _out() {
  echo "$(date +'%F %H:%M:%S') $@"
}

function setup() {
  local ns="${NAMESPACE:-redbank-demo}"
  local agent_name="redbank-orchestrator"

  # Accept both top-level var names and orchestrator-specific ones
  local api_key="${OPENAI_API_KEY:-${API_KEY:-}}"
  local base_url="${LLM_BASE_URL:-${BASE_URL:-}}"
  local model_id="${LLM_MODEL:-${MODEL_ID:-}}"

  if [[ -z "${base_url}" || -z "${model_id}" ]]; then
    echo "ERROR: LLM_BASE_URL and LLM_MODEL are required." >&2
    echo "Set them in the top-level .env and re-run." >&2
    exit 1
  fi

  _out "Deploying ${agent_name} to namespace: ${ns}"

  oc new-project "${ns}" 2>/dev/null || oc project "${ns}"

  cd "${SCRIPT_FOLDER}"

  _out "Building orchestrator image"
  oc new-build --strategy=docker --binary --name="${agent_name}" \
    --to="${agent_name}:latest" --namespace "${ns}" 2>/dev/null || true
  oc start-build "${agent_name}" --from-dir=. --follow --namespace "${ns}"

  local img="image-registry.openshift-image-registry.svc:5000/${ns}/${agent_name}:latest"
  local image_repo="${img%:*}"
  local image_tag="latest"

  _out "Deploying orchestrator via Helm"
  local secrets_file
  secrets_file=$(mktemp)
  trap "rm -f ${secrets_file}" EXIT
  umask 077
  printf 'secrets:\n  apiKey: "%s"\n' "${api_key}" > "${secrets_file}"

  helm upgrade --install "${agent_name}" ./charts/agent \
    --namespace "${ns}" \
    -f values.yaml \
    -f "${secrets_file}" \
    --set image.repository="${image_repo}" \
    --set image.tag="${image_tag}" \
    --set env.LLM_BASE_URL="${base_url}" \
    --set env.LLM_MODEL="${model_id}"

  _out "Waiting for rollout..."
  if oc rollout status "deployment/${agent_name}" --namespace "${ns}" --timeout=120s; then
    local route
    route=$(oc get route "${agent_name}" --namespace "${ns}" -o jsonpath='{.spec.host}' 2>/dev/null || true)
    if [[ -n "${route}" ]]; then
      _out "Agent available at: https://${route}"
    fi
  else
    _out "WARNING: Rollout did not complete. Check with:"
    echo "  oc get pods -n ${ns} -l app.kubernetes.io/name=${agent_name}"
    echo "  oc logs -n ${ns} deployment/${agent_name}"
  fi

  _out "Done deploying ${agent_name}"
}

setup
