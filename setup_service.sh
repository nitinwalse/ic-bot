#!/bin/bash
# बॉटला कायमस्वरूपी सेवा (service) बनवणे — सर्व्हर restart झाला किंवा बॉट पडला तरी आपोआप सुरू होईल.
set -e
DIR="$HOME/ic"
PY="$(command -v python3)"
sudo tee /etc/systemd/system/icbot.service > /dev/null << UNIT
[Unit]
Description=Nitin NIFTY Iron Condor Bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$PY -u $DIR/bot.py
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable icbot
sudo systemctl restart icbot
sleep 3
sudo systemctl --no-pager status icbot | head -5
echo ""
echo "✅ icbot सेवा सुरू झाली. Log पाहण्यासाठी:  tail -f ~/ic/bot.log"
