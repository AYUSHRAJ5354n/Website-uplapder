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

URL = "https://animexin.dev/"

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

# ===== STRICT FILTER =====
def is_valid_post(title):
    title = title.lower()

    # must contain episode number
    if not re.search(r'episode\s*\d+', title):
        return False

    # block unwanted types
    blocked = ["season", "movie", "trailer", "pv"]
    if any(word in title for word in blocked):
        return False

    return True

# ===== FORMAT =====
def format_caption(title):
    ep = re.search(r'Episode\s*(\d+)', title)
    ep = ep.group(1) if ep else "?"

    extra = re.search(r'\[(\d+)\]', title)
    extra = f" ({extra.group(1)})" if extra else ""

    clean = re.sub(r'Episode.*', '', title).strip()

    return f"""🔥{clean}

🟡 Episode : {ep}{extra}
🟡 Subtitle : Eng sub ✅
🟡 Quality  : 1080p ✨✅

@Donghua_Xin
"""

# ===== SCRAPER =====
def scrape():
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(URL, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")

    posts = soup.select("article")
    data = []

    for post in posts:
        try:
            title = post.select_one("h2, h3").text.strip()

            if not is_valid_post(title):
                continue

            link = post.select_one("a")["href"]

            img = post.select_one("img")
            image = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
            )

            data.append({
                "title": title,
                "link": link,
                "image": image
            })

        except:
            continue

    return data

# ===== SEND =====
async def send_post(app, post):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Watch Now", url=post["link"])]
    ])

    caption = format_caption(post["title"])

    while True:
        try:
            await app.bot.send_photo(
                chat_id=CHAT_ID,
                photo=post["image"],
                caption=caption,
                reply_markup=keyboard
            )
            break

        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)

        except Exception as e:
            print("Send error:", e)
            break

# ===== AUTO UPDATE =====
async def auto_update(app):
    await asyncio.sleep(10)

    while True:
        try:
            posts = scrape()

            for post in posts:
                if not collection.find_one({"link": post["link"]}):
                    await send_post(app, post)
                    collection.insert_one(post)
                    await asyncio.sleep(5)

        except Exception as e:
            print("Loop error:", e)

        await asyncio.sleep(300)

# ===== SEARCH (TEMP SYSTEM) =====
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    query = " ".join(context.args)
    url = f"https://animexin.dev/?s={query.replace(' ', '+')}"

    res = requests.get(url)
    soup = BeautifulSoup(res.text, "html.parser")

    posts = soup.select("article")

    buttons = []

    for post in posts[:20]:
        try:
            title = post.select_one("h2, h3").text.strip()

            if not is_valid_post(title):
                continue

            link = post.select_one("a")["href"]

            img = post.select_one("img")
            image = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
            )

            doc = collection.insert_one({
                "title": title,
                "link": link,
                "image": image,
                "temp": True
            })

            buttons.append([
                InlineKeyboardButton(title[:30], callback_data=str(doc.inserted_id))
            ])

        except:
            continue

    if not buttons:
        await update.message.reply_text("❌ No results")
        return

    await update.message.reply_text(
        f"🔍 Results for {query}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ===== BUTTON =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    post = collection.find_one({"_id": ObjectId(query.data)})

    if not post:
        await query.message.reply_text("❌ Expired")
        return

    await send_post(context.application, post)

    if post.get("temp"):
        collection.delete_one({"_id": post["_id"]})

# ===== CLEANUP =====
async def cleanup_db():
    while True:
        try:
            collection.delete_many({"temp": True})
        except:
            pass

        await asyncio.sleep(600)

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Private bot. Get out 🤣")
        return
    await update.message.reply_text("🔥 RSS Donghua Bot Ready")

async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    posts = scrape()
    for post in posts:
        if not collection.find_one({"link": post["link"]}):
            await send_post(context.application, post)
            collection.insert_one(post)

    await update.message.reply_text("✅ Updated")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    total = collection.count_documents({"temp": {"$ne": True}})
    await update.message.reply_text(f"📊 Total Episodes Stored: {total}")

# ===== MAIN =====
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        asyncio.create_task(auto_update(app))
        asyncio.create_task(cleanup_db())

    app.post_init = on_startup

    print("🔥 Bot Running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
