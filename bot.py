import os
import requests
from bs4 import BeautifulSoup
import asyncio
import re
from datetime import datetime
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)
from pymongo import MongoClient

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
CHAT_ID = -1003732357781
MONGO_URL = os.getenv("MONGO_URL")
URL = "https://animexin.dev/"

client = MongoClient(MONGO_URL)
db = client["donghua_bot"]
collection = db["posts"]

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

    try:
        await app.bot.send_photo(
            chat_id=CHAT_ID,
            photo=post["image"],
            caption=caption,
            reply_markup=keyboard
        )
    except:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=caption,
            reply_markup=keyboard
        )

# ===== AUTO LOOP =====
async def auto_update(app):
    while True:
        posts = scrape()

        for post in posts:
            if not collection.find_one({"link": post["link"]}):
                await send_post(app, post)
                collection.insert_one(post)

        await asyncio.sleep(300)

# ===== COMMANDS =====
def is_owner(user_id):
    return user_id == OWNER_ID

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

# ===== SEARCH SYSTEM =====
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return

    query = " ".join(context.args).lower()

    results = list(collection.find({"name": {"$regex": query}}))

    if not results:
        await update.message.reply_text("❌ Not found")
        return

    # Sort by episode
    results = sorted(results, key=lambda x: x["episode"])

    buttons = []
    for r in results:
        buttons.append([
            InlineKeyboardButton(
                f"Ep {r['episode']}",
                callback_data=r["link"]
            )
        ])

    keyboard = InlineKeyboardMarkup(buttons[:50])  # limit

    await update.message.reply_text(
        f"🔍 Found {len(results)} episodes",
        reply_markup=keyboard
    )

# ===== BUTTON CLICK =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    link = query.data
    post = collection.find_one({"link": link})

    if post:
        await send_post(context.application, post)

# ===== MAIN =====
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CallbackQueryHandler(button_handler))

    asyncio.create_task(auto_update(app))

    print("🔥 Bot Running...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
