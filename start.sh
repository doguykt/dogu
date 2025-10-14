cd $RENDER_PROJECT_ROOT || exit 1
#!/bin/bash
# Komutu çalıştırmadan önce, deponun kök dizinine geçiyoruz
# Bu, Render'ın ortamına göre dosya yolunu düzeltir
cd $RENDER_PROJECT_ROOT || exit 1 

pip install --upgrade pip
pip install -r requirements.txt
python trend_fiyat_bot_final.py