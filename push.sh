#!/data/data/com.termux/files/usr/bin/bash
set -e
python3 -m py_compile app.py morocco_data.py islamic_content.py
git add .
git commit -m "Update Noor Al-Maghrib Bot" || true
git branch -M main
git push -u origin main
echo "Pushed to GitHub successfully!"
