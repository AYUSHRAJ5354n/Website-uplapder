import requests
from bs4 import BeautifulSoup
import asyncio
import re
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
CHAT_ID = int(os.getenv("CHAT_ID"))
MONGO_URL = os.getenv("MONGO_URL")

# ===== DB =====
client = MongoClient(MONGO_URL)
db = client["donghua_bot"]
collection = db["posts"]

# ===== HEALTH SERVER =====
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    port = int(os.getenv("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# ===== HELPERS =====
def extract_episode(title):
    match = re.search(r'episode\s*(\d+)', title.lower())
    return int(match.group(1)) if match else None

def extract_series(title):
    return re.sub(r'episode.*', '', title, flags=re.IGNORECASE).strip().lower()

def format_caption(title):
    ep = extract_episode(title)
    if not ep:
        return None

    clean = re.sub(r'episode.*', '', title, flags=re.IGNORECASE).strip()

    return f"""🔥{clean}

🟡 Episode : {ep}
🟡 Subtitle : Eng sub ✅
🟡 Quality  : 1080p ✨✅

@Donghua_Xin
"""

def get_image(url):
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]

        return None
    except:
        return None

# ===== 🔥 FINAL SCRAPER (LATEST RELEASE ONLY) =====
def scrape():
    url = "https://animexin.dev/"
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(res.text, "html.parser")

    data = []

    # Find Latest Release section
    section = soup.find("h2", string=lambda x: x and "Latest Release" in x)
    if not section:
        return []

    parent = section.find_parent()

    # ONLY visible cards (NOT view all)
    cards = parent.find_all("article")

    for card in cards[:10]:  # limit to top 10
        try:
            a = card.find("a", href=True)
            if not a:
                continue

            link = a["href"]

            if "page" in link or "category" in link:
                continue

            title = card.get_text(" ", strip=True)

            ep = extract_episode(title)
            if not ep:
                continue  # skip invalid posts

            img = card.find("img")
            image = img["src"] if img else None

            data.append({
                "title": title,
                "link": link,
                "image": image,
                "episode": ep,
                "series": extract_series(title)
            })

        except:
            continue

    return data

# ===== SEND =====
async def send_post(app, post, chat_id):
    caption = format_caption(post["title"])
    if not caption:
        return

    if not post.get("image"):
        post["image"] = get_image(post["link"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Watch Now", url=post["link"])]
    ])

    try:
        await app.bot.send_photo(
            chat_id=chat_id,
            photo=post["image"],
            caption=caption,
            reply_markup=keyboard
        )
    except:
        await app.bot.send_message(chat_id=chat_id, text=caption)

# ===== AUTO UPDATE =====
async def auto_update(app):
    await asyncio.sleep(10)

    while True:
        posts = scrape()

        for post in posts:
            series = post["series"]
            ep = post["episode"]

            last = collection.find_one(
                {"series": series},
                sort=[("episode", -1)]
            )

            if last and ep <= last["episode"]:
                continue  # skip old

            await send_post(app, post, CHAT_ID)

            collection.insert_one(post)

            await asyncio.sleep(3)

        await asyncio.sleep(300)

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("❌ Private bot")

    await update.message.reply_text("🔥 RSS Donghua Bot Ready")

# ===== UPDATE =====
async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = scrape()
    count = 0

    for post in posts:
        series = post["series"]
        ep = post["episode"]

        last = collection.find_one(
            {"series": series},
            sort=[("episode", -1)]
        )

        if last and ep <= last["episode"]:
            continue

        await send_post(context.application, post, CHAT_ID)
        collection.insert_one(post)
        count += 1

    await update.message.reply_text(f"🚀 Uploaded {count} new episodes")

# ===== REUPLOAD (ONLY HOMEPAGE CURRENT) =====
async def reupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    posts = scrape()
    count = 0

    for post in posts:
        await send_post(context.application, post, CHAT_ID)
        await asyncio.sleep(2)
        count += 1

    await update.message.reply_text(f"🔁 Reuploaded {count} latest homepage episodes")

# ===== STATS =====
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = collection.count_documents({})
    await update.message.reply_text(f"📊 Total stored episodes: {total}")

# ===== CLEAN =====
async def clean_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = collection.delete_many({}).deleted_count

    await update.message.reply_text(
        f"💀 FULL RESET DONE\nDeleted: {deleted}"
    )

# ===== MAIN =====
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("reupload", reupload))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("clean", clean_db))

    async def startup(app):
        asyncio.create_task(auto_update(app))

    app.post_init = startup

    print("🔥 Bot Running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
