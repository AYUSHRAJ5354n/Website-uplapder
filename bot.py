import requests
from bs4 import BeautifulSoup
import asyncio
import re
from datetime import datetime
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)
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
            link = post.select_one("a")["href"]

            img = post.select_one("img")
            image = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("src")
            )

            ep = re.search(r'Episode\s*(\d+)', title)
            ep = int(ep.group(1)) if ep else 0

            name = re.sub(r'Episode.*', '', title).strip().lower()

            data.append({
                "title": title,
                "link": link,
                "image": image,
                "name": name,
                "episode": ep,
                "date": datetime.now().strftime("%d/%m/%y")
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
            print(f"⏳ Flood wait {e.retry_after}s")
            await asyncio.sleep(e.retry_after)

        except Exception as e:
            print("Send error:", e)
            break

# ===== AUTO LOOP =====
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

# ===== COMMANDS =====
def is_owner(uid):
    return uid == OWNER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Private bot. Get out 🤣")
        return
    await update.message.reply_text("🔥 RSS Donghua Bot Ready")

async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    posts = scrape()
    for post in posts:
        if not collection.find_one({"link": post["link"]}):
            await send_post(context.application, post)
            collection.insert_one(post)

    await update.message.reply_text("✅ Updated")

# ===== SEARCH =====
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    query = " ".join(context.args).lower()

    results = list(collection.find({"name": {"$regex": query}}))

    if not results:
        await update.message.reply_text("❌ Not found")
        return

    results = sorted(results, key=lambda x: x["episode"])

    buttons = []
    for r in results[:50]:
        buttons.append([
            InlineKeyboardButton(
                f"Ep {r['episode']}",
                callback_data=str(r["_id"])
            )
        ])

    await update.message.reply_text(
        f"🔍 Found {len(results)} episodes",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ===== BUTTON =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    post_id = query.data

    post = collection.find_one({"_id": ObjectId(post_id)})

    if post:
        await send_post(context.application, post)

# ===== REUPLOAD =====
async def reupload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    today = datetime.now().strftime("%d/%m/%y")

    posts = list(collection.find({"date": today}))

    for post in posts:
        await send_post(context.application, post)
        await asyncio.sleep(2)

    await update.message.reply_text("🔁 Reuploaded today's posts")

# ===== CHK =====
async def chk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("❌ Use: /chk 07/04/26")
        return

    date = context.args[0]

    posts = list(collection.find({"date": date}))

    if not posts:
        await update.message.reply_text("❌ No posts found")
        return

    for post in posts:
        await send_post(context.application, post)
        await asyncio.sleep(2)

    await update.message.reply_text(f"📅 Uploaded posts for {date}")

# ===== STATS =====
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    total_posts = collection.count_documents({})
    total_series = len(collection.distinct("name"))

    await update.message.reply_text(
        f"""📊 Bot Stats

📦 Total Posts: {total_posts}
🎬 Total Series: {total_series}
"""
    )

# ===== MAIN =====
def main():
    threading.Thread(target=run_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("reupload", reupload))
    app.add_handler(CommandHandler("chk", chk))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(button_handler))

    async def on_startup(app):
        asyncio.create_task(auto_update(app))

    app.post_init = on_startup

    print("🔥 Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()
