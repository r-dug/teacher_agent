#!/usr/bin/env bash
# provision.sh — run on your LOCAL machine ONCE to create the Azure VM.
#
# Prerequisites:
#   - Azure CLI installed  (https://docs.microsoft.com/cli/azure/install-azure-cli)
#   - Azure for Students subscription active
#   - `az login` already done (script will prompt if not)
#
# Usage:
#   bash scripts/provision.sh

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────

RESOURCE_GROUP="pdf-to-audio-rg"
VM_NAME="pdf-to-audio-vm"
LOCATION="eastus2"
VM_SIZE="Standard_D2s_v3"         # 2 vCPU, 8 GB RAM — ~$0.096/hr
ADMIN_USER="appuser"
SSH_KEY_PATH="$HOME/.ssh/pdf_deploy"
AUTO_SHUTDOWN_TIME="0300"        # 3 AM UTC = 11 PM EST — adjust if needed

# ── Pre-flight ────────────────────────────────────────────────────────────────

if ! command -v az &>/dev/null; then
    echo "ERROR: Azure CLI not found."
    echo "Install: https://docs.microsoft.com/cli/azure/install-azure-cli"
    exit 1
fi

if ! az account show &>/dev/null 2>&1; then
    echo "Not logged in to Azure. Running 'az login'..."
    az login
fi

echo "Using subscription: $(az account show --query name -o tsv)"

# Generate a dedicated deploy SSH key if not already present
if [ ! -f "$SSH_KEY_PATH" ]; then
    echo "Generating SSH key at $SSH_KEY_PATH ..."
    ssh-keygen -t ed25519 -C "deploy@pdf-to-audio" -f "$SSH_KEY_PATH" -N ""
fi

# ── Provision ─────────────────────────────────────────────────────────────────

echo ""
echo "Creating resource group '$RESOURCE_GROUP' in '$LOCATION'..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none

echo "Creating VM '$VM_NAME' (Debian 12, $VM_SIZE) — takes ~2 minutes..."
az vm create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$VM_NAME" \
    --image "Debian:debian-12:12-gen2:latest" \
    --size "$VM_SIZE" \
    --admin-username "$ADMIN_USER" \
    --ssh-key-values "${SSH_KEY_PATH}.pub" \
    --public-ip-sku Standard \
    --output none

echo "Opening ports 80 (HTTP) and 443 (HTTPS)..."
az vm open-port \
    --resource-group "$RESOURCE_GROUP" \
    --name "$VM_NAME" \
    --port 80 --priority 1001 --output none
az vm open-port \
    --resource-group "$RESOURCE_GROUP" \
    --name "$VM_NAME" \
    --port 443 --priority 1002 --output none

echo "Configuring auto-shutdown at ${AUTO_SHUTDOWN_TIME} UTC (saves credits)..."
az vm auto-shutdown \
    --resource-group "$RESOURCE_GROUP" \
    --name "$VM_NAME" \
    --time "$AUTO_SHUTDOWN_TIME" \
    --output none

# ── Done ──────────────────────────────────────────────────────────────────────

PUBLIC_IP=$(az vm show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$VM_NAME" \
    --show-details \
    --query publicIps \
    --output tsv)

# Write SSH config entry for convenience
SSH_CONFIG="$HOME/.ssh/config"
if ! grep -q "Host pdf-to-audio" "$SSH_CONFIG" 2>/dev/null; then
    cat >> "$SSH_CONFIG" << EOF

Host pdf-to-audio
  HostName $PUBLIC_IP
  User $ADMIN_USER
  IdentityFile $SSH_KEY_PATH
  ServerAliveInterval 60
EOF
    echo "Added 'pdf-to-audio' entry to ~/.ssh/config"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  VM ready!"
echo ""
echo "  Public IP : $PUBLIC_IP"
echo "  SSH       : ssh pdf-to-audio"
echo "              (or: ssh -i $SSH_KEY_PATH $ADMIN_USER@$PUBLIC_IP)"
echo ""
echo "  Auto-shutdown: ${AUTO_SHUTDOWN_TIME} UTC daily"
echo "  Start VM when needed: az vm start -g $RESOURCE_GROUP -n $VM_NAME"
echo ""
echo "  Next steps:"
echo "  1. Point your domain's A record → $PUBLIC_IP"
echo "     (Cloudflare: ~5 min   Other registrars: up to 1 hr)"
echo ""
echo "  2. Copy deploy.sh to the server:"
echo "     scp -i $SSH_KEY_PATH scripts/deploy.sh $ADMIN_USER@$PUBLIC_IP:~/"
echo ""
echo "  3. SSH in and run it:"
echo "     ssh pdf-to-audio"
echo "     bash deploy.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
