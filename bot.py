import requests
from bs4 import BeautifulSoup
import asyncio
import re
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import RetryAfter
from pymongo import MongoClient
from bson import ObjectId

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
    print(f"🌐 Health server running on {port}")
    server.serve_forever()

# ===== FORMAT =====
def format_caption(title):
    ep = re.search(r'Episode\s*(\d+)', title)
    ep = ep.group(1) if ep else "?"

    clean = re.sub(r'Episode.*', '', title).strip()

    return f"""🔥{clean}

🟡 Episode : {ep}
🟡 Subtitle : Eng sub ✅
🟡 Quality  : 1080p ✨✅

@Donghua_Xin
"""

# ===== SEND =====
async def send_post(app, post):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Watch Now", url=post["link"])]
    ])

    caption = format_caption(post["title"])

    try:
        await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=post.get("image", ""),
            caption=caption,
            reply_markup=keyboard
        )
    except:
        await app.bot.send_message(chat_id=CHAT_ID, text=caption)

# ===== SCRAPE EPISODES =====
def get_episodes(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")

    links = soup.find_all("a", href=True)
    eps = []

    for a in links:
        href = a["href"]

        if "episode" not in href:
            continue

        title = a.text.strip()

        if not re.search(r'episode\s*\d+', title.lower()):
            continue

        eps.append({
            "title": title,
            "link": href
        })

    # remove duplicates
    unique = {e["link"]: e for e in eps}.values()

    # sort
    def get_ep(x):
        m = re.search(r'episode\s*(\d+)', x["title"].lower())
        return int(m.group(1)) if m else 0

    return sorted(unique, key=get_ep)

# ===== SEARCH =====
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    query = " ".join(context.args)
    slug = query.lower().replace(" ", "-")

    headers = {"User-Agent": "Mozilla/5.0"}

    seasons = []

    # check seasons
    for i in range(1, 8):
        if i == 1:
            url = f"https://animexin.dev/{slug}/"
        else:
            url = f"https://animexin.dev/{slug}-season-{i}/"

        try:
            res = requests.get(url, headers=headers, timeout=10)

            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")

                if soup.find("a", href=True):
                    seasons.append((f"Season {i}", url))

        except:
            continue

    if not seasons:
        await update.message.reply_text("❌ Series not found")
        return

    # multiple seasons
    if len(seasons) > 1:
        buttons = []

        for name, url in seasons:
            doc = collection.insert_one({
                "season": True,
                "url": url,
                "temp": True
            })

            buttons.append([
                InlineKeyboardButton(name, callback_data=str(doc.inserted_id))
            ])

        await update.message.reply_text(
            f"📺 {query} Seasons",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    else:
        # single season → show eps directly
        eps = get_episodes(seasons[0][1])

        buttons = []

        for ep in eps[:50]:
            doc = collection.insert_one({
                "title": ep["title"],
                "link": ep["link"],
                "temp": True
            })

            num = re.search(r'\d+', ep["title"]).group()

            buttons.append([
                InlineKeyboardButton(f"Ep {num}", callback_data=str(doc.inserted_id))
            ])

        await update.message.reply_text(
            f"📺 {query} Episodes",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# ===== BUTTON =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = collection.find_one({"_id": ObjectId(query.data)})

    if not data:
        await query.message.reply_text("❌ Expired")
        return

    # season click
    if data.get("season"):
        eps = get_episodes(data["url"])

        buttons = []

        for ep in eps[:50]:
            doc = collection.insert_one({
                "title": ep["title"],
                "link": ep["link"],
                "temp": True
            })

            num = re.search(r'\d+', ep["title"]).group()

            buttons.append([
                InlineKeyboardButton(f"Ep {num}", callback_data=str(doc.inserted_id))
            ])

        await query.message.reply_text(
            "📺 Episodes",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # episode click
    await send_post(context.application, data)

    if data.get("temp"):
        collection.delete_one({"_id": data["_id"]})

# ===== CLEANUP =====
async def cleanup_db():
    while True:
        collection.delete_many({"temp": True})
        await asyncio.sleep(600)

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Private bot")
        return
    await update.message.reply_text("🔥 Donghua Bot Ready")

# ===== MAIN =====
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def startup(app):
        asyncio.create_task(cleanup_db())

    app.post_init = startup

    print("🔥 Bot Running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
