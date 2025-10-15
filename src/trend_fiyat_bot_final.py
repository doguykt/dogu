import asyncio
import re
import json
import logging
import sqlite3
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# === Telegram Bot Token ===
BOT_TOKEN = "8378902892:AAHCqldjSFsjqP02aIzjzfk18V0ig-aGy64"  # ← kendi token'ını buraya gir

# === Veritabanı ===
DB_FILE = "urunler.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# === Veritabanı oluştur ===
def veritabani_olustur():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS urunler (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            fiyat REAL,
            kampanyali REAL,
            stok TEXT,
            hedef REAL,
            kullanici_id INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# === Fiyat ve stok çekme ===
REQUEST_TIMEOUT = 12

async def fetch_trendyol(url: str):
    def _clean_price(txt):
        if not txt:
            return None
        txt = re.sub(r"[^\d,\.]", "", txt).strip()
        txt = txt.replace(".", "").replace(",", ".")
        try:
            return round(float(txt), 2)
        except:
            return None

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # --- 1) Requests yöntemi ---
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")

            ld = soup.find("script", type="application/ld+json")
            if ld:
                try:
                    j = json.loads(ld.string)
                    offers = j.get("offers")
                    if isinstance(offers, dict):
                        p = offers.get("price")
                        price = _clean_price(str(p))
                        stok = "Stokta" if "instock" in offers.get("availability", "").lower() else "Tükendi"
                        if price is not None:
                            return price, None, stok
                except Exception:
                    pass

            price_tags = soup.find_all(["span", "div"], {"class": re.compile(r"prc|price", re.I)})
            prices = []
            for tag in price_tags:
                txt = (tag.get_text() or "").strip()
                pr = _clean_price(txt)
                if pr:
                    prices.append(pr)
            if prices:
                prices = sorted(set(prices))
                return prices[-1], (prices[0] if len(prices) > 1 else None), "Stokta"
    except Exception:
        pass

    # --- 2) Playwright yöntemi (JS ile yüklenen sayfalar için) ---
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(user_agent=headers["User-Agent"])
            await page.goto(url, timeout=20000)
            await page.wait_for_timeout(1500)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            prices = []
            for tag in soup.find_all(["span", "div"], {"class": re.compile(r"prc|price", re.I)}):
                pr = _clean_price(tag.get_text())
                if pr:
                    prices.append(pr)

            stok = "Stokta"
            if soup.find(text=re.compile(r"Tükendi|Stokta yok|Stok tükendi", re.I)):
                stok = "Tükendi"

            await browser.close()
            if prices:
                prices = sorted(set(prices))
                return prices[-1], (prices[0] if len(prices) > 1 else None), stok
    except PWTimeout:
        pass
    except Exception:
        pass

    return None, None, None


# === Komutlar ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Trendyol Fiyat Takip Botuna Hoş Geldin!\n\n"
                                    "Kullanım:\n"
                                    "/ekle <link> <hedef_fiyat>\n"
                                    "/liste – Ürünlerini Gör\n"
                                    "/sil <id> – Ürünü Sil\n"
                                    "/kontrol – Manuel Kontrol Et")

async def ekle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Kullanım: /ekle <Trendyol link> <hedef fiyat>")
        return

    link = context.args[0]
    try:
        hedef = float(context.args[1])
    except:
        await update.message.reply_text("⚠️ Lütfen geçerli bir hedef fiyat girin.")
        return

    await update.message.reply_text("🔍 Ürün bilgileri çekiliyor...")

    orj, kamp, stok = await fetch_trendyol(link)
    if orj is None:
        await update.message.reply_text("❌ Fiyat alınamadı. Linki kontrol edin veya sayfa JS ile yükleniyor olabilir.")
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO urunler (link, fiyat, kampanyali, stok, hedef, kullanici_id) VALUES (?, ?, ?, ?, ?, ?)",
              (link, orj, kamp, stok, hedef, update.effective_user.id))
    conn.commit()
    conn.close()

    msg = f"✅ Ürün eklendi!\n\nOrijinal Fiyat: {orj} TL"
    if kamp: msg += f"\nKampanyalı: {kamp} TL"
    msg += f"\nStok: {stok}"
    await update.message.reply_text(msg)


async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, link, fiyat, kampanyali, stok, hedef FROM urunler WHERE kullanici_id=?", (update.effective_user.id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 Henüz eklenmiş ürün yok.")
        return

    text = "📋 Ürün Listen:\n\n"
    for id_, link, fiyat, kamp, stok, hedef in rows:
        text += f"🆔 {id_}\n💰 Fiyat: {fiyat} TL"
        if kamp: text += f" | Kampanya: {kamp} TL"
        text += f"\n🎯 Hedef: {hedef} TL | 📦 {stok}\n🔗 {link}\n\n"
    await update.message.reply_text(text)


async def sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Kullanım: /sil <id>")
        return

    urun_id = context.args[0]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM urunler WHERE id=? AND kullanici_id=?", (urun_id, update.effective_user.id))
    conn.commit()
    conn.close()

    await update.message.reply_text("🗑️ Ürün silindi.")


async def kontrol_et(app):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, link, fiyat, kampanyali, stok, hedef, kullanici_id FROM urunler")
    urunler = c.fetchall()
    conn.close()

    for id_, link, eski_fiyat, eski_kamp, eski_stok, hedef, uid in urunler:
        orj, kamp, stok = await fetch_trendyol(link)
        if orj is None:
            continue

        mesaj = None
        if orj != eski_fiyat or stok != eski_stok:
            mesaj = f"📢 Fiyat/Stok Güncellendi!\n\nYeni: {orj} TL\nEski: {eski_fiyat} TL\nStok: {stok}\n🔗 {link}"
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("UPDATE urunler SET fiyat=?, kampanyali=?, stok=? WHERE id=?", (orj, kamp, stok, id_))
            conn.commit()
            conn.close()

        if orj <= hedef:
            mesaj = f"🎯 Hedef fiyata ulaşıldı!\nYeni fiyat: {orj} TL\n🔗 {link}"

        if mesaj:
            try:
                await app.bot.send_message(chat_id=uid, text=mesaj)
            except Exception as e:
                logger.warning(f"Mesaj gönderilemedi: {e}")


# === Scheduler ve Bot başlat ===
def start_scheduler(app):
    scheduler = BackgroundScheduler(timezone="Europe/Istanbul")
    scheduler.add_job(lambda: asyncio.create_task(kontrol_et(app)), trigger=IntervalTrigger(minutes=10))
    scheduler.start()
    logger.info("⏱️ Otomatik kontrol başlatıldı (10 dakikada bir).")


async def main():
    veritabani_olustur()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ekle", ekle))
    app.add_handler(CommandHandler("liste", liste))
    app.add_handler(CommandHandler("sil", sil))
    app.add_handler(CommandHandler("kontrol", lambda u, c: asyncio.create_task(kontrol_et(app))))

    start_scheduler(app)

    logger.info("🤖 Bot başlatıldı!")
    await app.run_polling()


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())

