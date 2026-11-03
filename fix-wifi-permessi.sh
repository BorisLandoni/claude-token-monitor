#!/usr/bin/env bash
# Permette al servizio Claude Monitor (utente corrente) di configurare il WiFi
# tramite NetworkManager. Da eseguire UNA volta sul Raspberry:
#   bash ~/claude-token-monitor/fix-wifi-permessi.sh
set -e

RULE=/etc/polkit-1/rules.d/50-claude-monitor-network.rules
U="${SUDO_USER:-$USER}"

if ! command -v nmcli >/dev/null 2>&1; then
    echo "nmcli non trovato: questo Raspberry non usa NetworkManager."
    exit 1
fi

sudo tee "$RULE" >/dev/null <<EOF
// Claude Monitor: consente all'utente "$U" di gestire le reti (WiFi) da servizio
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        subject.user === "$U") {
        return polkit.Result.YES;
    }
});
EOF
sudo chmod 644 "$RULE"
sudo systemctl restart polkit 2>/dev/null || true
echo "OK: regola installata per l'utente $U. Ora configura il WiFi dal monitor."
