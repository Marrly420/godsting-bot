import functools
import discord
from discord.ext import commands
import asyncio
from yt_dlp import YoutubeDL
import time
import json
import os
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET")
    )
)


SPOTIFY_TRACK_REGEX = re.compile(r"open\.spotify\.com/track/([a-zA-Z0-9]+)")
SPOTIFY_PLAYLIST_REGEX = re.compile(
    r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)"
)

def spotify_playlist_to_tracks(text):
    match = SPOTIFY_PLAYLIST_REGEX.search(text)
    if not match:
        return None, None

    playlist_id = match.group(1)

    try:
        results = sp.playlist_items(
            playlist_id,
            additional_types=["track"],
            limit=100
        )

        tracks = []
        artists = set()

        for item in results["items"]:
            track = item.get("track")
            if not track:
                continue

            song = track["name"]
            artist = track["artists"][0]["name"]

            tracks.append(f"{artist} - {song}")
            artists.add(artist)

        return tracks if tracks else None, artists

    except Exception as e:
        print("Spotify playlist error:", e)
        return None, None





# ==================================================
# LOAD/SAVE SETTINGS
# ==================================================
SETTINGS_FILE = "settings.json"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, "r") as f:
        return json.load(f)

def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=4)

settings = load_settings()

guild_music_settings = settings.get("guild_music_settings", {})

# ==================================================
# LICENSE SYSTEM
# ==================================================
ALLOWED_GUILDS = [
    1185013508962779206, 745758488780603402, 1153013837776302162   # ضع هنا السيرفرات المسموح لها
]

OWNER_ID = 622210438062669835  # ضع هنا ID حسابك

class ContactButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="💜 Contact Support",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/users/{OWNER_ID}"
        )
        self.add_item(button)

async def send_license_error(ctx):
    embed = discord.Embed(
        title="💜✨ License Required ✨💜",
        description=(
            "**هذا السيرفر لا يملك عضوية لاستخدام البوت**\n"
            "للمزيد من التفاصيل يرجى مراسلتي في الخاص 💜\n\n"
            "This server does not have a valid license to use this bot.\n"
            "Please contact me via DM for more information 💜"
        ),
        color=0xA64DFF,
    )
    await ctx.send(embed=embed, view=ContactButton())

# ==================================================
# STORAGE
# ==================================================
guild_queues = {}
guild_current = {}
guild_nowplaying_msg = {}
guild_queue_msg = {}
skip_request_msg = {}
skip_pending = {}
loop_enabled = {}
first_run_cleanup = {}
smart_play_enabled = {}
smart_play_seed = {}   
played_video_ids = {}  

MAX_SMART_TRIES = 5
smart_fail_count = {}   # {guild_id: int}




song_start_time = {}
song_duration = {}

PURPLE = 0x6A0DAD

# ==================================================
# YTDL SETTINGS
# ==================================================
def extract_artist(title):
    if "-" in title:
        return title.split("-")[0].strip()
    return title.strip().split(" ")[0]

YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "ignoreerrors": True,
    "default_search": "ytsearch",
    "extract_flat": False,
}




ytdl = YoutubeDL(YTDL_OPTS)

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn"
}


import random

def build_smart_query(gid):
    artists = list(smart_play_seed.get(gid, []))
    if not artists:
        return None

    artist = random.choice(artists)
    year = random.randint(2010, 2024)

    blacklist = (
        "-meme -parody -nightcore -remix -edit "
        "-bass -slowed -sped -tiktok -funny"
    )

    return f"{artist} song {year} {blacklist}"



def build_progress(elapsed, total):
    total = max(total, 1)
    bar_len = 20
    filled = min(max(int((elapsed / total) * bar_len), 0), bar_len)
    empty = bar_len - filled
    return f"[{'■' * filled}{'□' * empty}] {int(elapsed)}/{int(total)}s"


# ==================================================
# QUEUE DISPLAY
# ==================================================
async def update_queue_display(guild):
    gid = guild.id
    queue = guild_queues.get(gid, [])

    inside = "✨ *Queue is empty…*" if not queue else "\n".join(
        [f"✨ {item['query']}" for item in queue]
    )

    boxed = (
        "╔══════════════════════════╗\n"
        f"{inside}\n"
        "╚══════════════════════════╝"
    )

    embed = discord.Embed(title="🎶✨ **QUEUE** ✦", description=boxed, color=PURPLE)

    channel = guild.get_channel(guild_music_settings[gid])

    old = guild_queue_msg.get(gid)
    if old:
        await old.edit(embed=embed)
    else:
        guild_queue_msg[gid] = await channel.send(embed=embed)


# ==================================================
# CLEAR SKIP REQUESTS
# ==================================================
async def clear_skip_requests(guild):
    gid = guild.id
    if skip_request_msg.get(gid):
        for m in skip_request_msg[gid]:
            try:
                await m.delete()
            except:
                pass

    skip_request_msg[gid] = []
    skip_pending[gid] = None


# ==================================================
# MUSIC CONTROLS
# ==================================================
class MusicControls(discord.ui.View):
    def __init__(self, gid):
        super().__init__(timeout=None)
        self.gid = gid

        # 🔄 Sync Smart Play button style
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "Smart Play":
                if smart_play_enabled.get(gid):
                    item.style = discord.ButtonStyle.success

    
    @discord.ui.button(label="Smart Play", style=discord.ButtonStyle.secondary, row=1)
    async def smart_play(self, inter, btn):
        gid = inter.guild.id
        smart_play_enabled[gid] = not smart_play_enabled.get(gid, False)

        if smart_play_enabled[gid]:
            btn.style = discord.ButtonStyle.success
            smart_play_seed[gid] = set()

            # ⬅️ خذ الأغنية الحالية + باقي الكويي فقط
            if guild_current.get(gid):
                feed_smart_seed(gid, guild_current[gid]["query"])

            for item in guild_queues.get(gid, []):
                feed_smart_seed(gid, item["query"])

        else:
            btn.style = discord.ButtonStyle.secondary
            smart_play_seed[gid] = set()

        await inter.message.edit(view=self)
        await inter.response.defer()





    # زر واحد يتحكم بالحالتين
    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume(self, inter, btn):
        vc = inter.guild.voice_client
        if not vc:
            return await inter.response.defer()

        # إذا الأغنية تلعب → Pause
        if vc.is_playing():
            vc.pause()
            btn.label = "Resume"
            btn.style = discord.ButtonStyle.success

        # إذا الأغنية متوقفة → Resume
        elif vc.is_paused():
            vc.resume()
            btn.label = "Pause"
            btn.style = discord.ButtonStyle.secondary

        await inter.message.edit(view=self)
        await inter.response.defer()

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.primary, row=0)
    async def skip(self, inter, btn):

        gid = inter.guild.id

        # CASE 1 – skip request pending
        if skip_pending.get(gid):

            owner = skip_pending[gid]["song_owner_id"]

            if inter.user.id == owner:
                await inter.response.defer()
                await clear_skip_requests(inter.guild)
                await finalize_skip(inter.guild)

                btn.label = "Skip"
                btn.style = discord.ButtonStyle.primary
                await inter.message.edit(view=self)
                return


        # CASE 2 – no pending request
        if not guild_current.get(gid):
            return await inter.response.defer()

        owner = guild_current[gid]["owner_id"]
        requester = inter.user.id

        # ✅ Smart Play song → skip مباشرة
        if owner == bot.user.id:
            await inter.response.defer()
            await clear_skip_requests(inter.guild)
            await finalize_skip(inter.guild)
            return



        if requester == owner:
            await inter.response.defer()
            await clear_skip_requests(inter.guild)
            await finalize_skip(inter.guild)
            return


        skip_pending[gid] = {"song_owner_id": owner, "requester_id": requester}

        owner_member = inter.guild.get_member(owner)
        owner_name = owner_member.display_name

        btn.label = f"Waiting ({owner_name})"
        btn.style = discord.ButtonStyle.danger
        await inter.message.edit(view=self)

        # message
        channel = inter.guild.get_channel(guild_music_settings[gid])
        embed = discord.Embed(
            description=f"💜 Skip request from {inter.user.mention}\nWaiting for {owner_member.mention} to approve ✨",
            color=PURPLE
        )
        m = await channel.send(embed=embed)

        skip_request_msg.setdefault(gid, []).append(m)

        await inter.response.defer()

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, row=0)
    async def loop(self, inter, btn):
        gid = inter.guild.id
        loop_enabled[gid] = not loop_enabled.get(gid, False)
        btn.style = discord.ButtonStyle.success if loop_enabled[gid] else discord.ButtonStyle.secondary
        await inter.message.edit(view=self)
        await inter.response.defer()

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, inter, btn):
        await clear_skip_requests(inter.guild)
        await hard_stop(inter.guild)
        await inter.response.defer()

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refresh(self, inter, btn):
        await clear_skip_requests(inter.guild)
        await soft_refresh(inter.guild)
        await inter.response.defer()


# ==================================================
# NOW PLAYING UI
# ==================================================
async def update_nowplaying(guild, title, thumbnail):

    gid = guild.id

    elapsed = time.time() - song_start_time.get(gid, 0)
    total = song_duration.get(gid, 1)

    fmt = lambda t: f"{int(t//60)}:{int(t%60):02d}"
    progress = build_progress(elapsed, total)

    queue = guild_queues.get(gid, [])
    up_next_text = ""
    if queue:
        nxt = queue[0]["query"]
        nxt = nxt if len(nxt) < 50 else nxt[:50] + "..."
        up_next_text = f"\n>> **Up Next:** {nxt}"

    owner = guild_current.get(gid, {}).get("owner_id")
    owner_member = guild.get_member(owner)

    embed = discord.Embed(
        title="⋆｡°✩ NOW PLAYING ✩°｡⋆ 💜",
        description=(
            f"💜 Requested by ⋆｡° {owner_member.mention} °｡⋆\n\n"
            f"💫 **{title}** 💫\n"
            f"⏱️ `{fmt(elapsed)} / {fmt(total)}`\n\n"
            f"`{progress}`\n"
            f"━━━ ✦ ━━━"
            f"{up_next_text}"
        ),
        color=PURPLE,
    )

    if thumbnail:
        embed.set_image(url=thumbnail)

    embed.set_footer(text="Created By ｍａｒｒｌｙ４")

    channel = guild.get_channel(guild_music_settings[gid])

    view = MusicControls(gid)

    old = guild_nowplaying_msg.get(gid)
    if old:
        await old.edit(embed=embed, view=view)
    else:
        guild_nowplaying_msg[gid] = await channel.send(embed=embed, view=view)


# ==================================================
# FINALIZE SKIP
# ==================================================
async def finalize_skip(guild):
    vc = guild.voice_client
    if vc and vc.is_playing():
        vc.stop()


# ==================================================
# PLAY MUSIC
# ==================================================
async def play_music(guild, msg=None):
    gid = guild.id
    smart_fail_count.setdefault(gid, 0)
    
    if not first_run_cleanup.get(gid):
        first_run_cleanup[gid] = True

        ch = guild.get_channel(guild_music_settings[gid])
        async for m in ch.history(limit=200):
            if m.author.bot:
                try:
                    await m.delete()
                except:
                    pass

        guild_nowplaying_msg[gid] = None
        guild_queue_msg[gid] = None

    queue = guild_queues.get(gid, [])

    if not queue:
        guild_current[gid] = None

        if smart_play_enabled.get(gid):
            spotify_query = spotify_smart_pick(gid)
            if spotify_query:
                guild_queues.setdefault(gid, []).append({
                    "query": spotify_query,
                    "owner_id": bot.user.id
                })
                return await play_music(guild, msg)

        return






    item = queue.pop(0)
    owner = item["owner_id"]
    query = item["query"]

    guild_current[gid] = {"query": query, "owner_id": owner}
    await clear_skip_requests(guild)

    vc = guild.voice_client

    # 🛑 أوقف الصوت فقط إذا vc موجود
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()




    if not vc:
        if not msg or not msg.author.voice:
            return

        try:
            vc = await msg.author.voice.channel.connect(timeout=10)
        except asyncio.TimeoutError:
            print("❌ Voice connection timeout")
            return


    yt_query = f"ytsearch5:{query}" if not query.startswith("http") else query
    loop = asyncio.get_running_loop()
    try:
        info = await loop.run_in_executor(
            None,
            functools.partial(ytdl.extract_info, yt_query, download=False)
        )
    except Exception as e:
        print("YTDL error:", e)

        # إذا هذه أغنية سمارت بلي (owner_id مالها bot.user.id) نعدّها فشل
        if owner == bot.user.id:
            smart_fail_count[gid] += 1

            if smart_fail_count[gid] >= MAX_SMART_TRIES:
                print("❌ Smart Play stopped: too many failed tries")
                smart_play_enabled[gid] = False
                smart_fail_count.pop(gid, None)


                ch = guild.get_channel(guild_music_settings[gid])
                if ch:
                    await ch.send("⚠️ Smart Play stopped بسبب فشل متكرر من YouTube. شغّل أغنية جديدة يدويًا 💜", delete_after=8)

                guild_current[gid] = None
                return

        # جرّب أغنية ثانية (من الكويي/سمارت)
        return await play_music(guild, msg)


    entries = info["entries"] if "entries" in info else [info]

    played_video_ids.setdefault(gid, set())

    info = None
    for e in entries:
        if not e:
            continue

        vid = e.get("id")
        if not vid:
            continue

        if vid in played_video_ids[gid]:
            continue

        info = e
        played_video_ids[gid].add(vid)
        break


    if not info:
        return await play_music(guild, msg)


    # ✅ نجحنا نجيب فيديو صالح
    smart_fail_count.pop(gid, None)



    url = info["url"]
    title = info.get("title", query)
    thumb = info.get("thumbnail")
    dur = info.get("duration", 120)

    song_start_time[gid] = time.time()
    song_duration[gid] = dur

    src = await discord.FFmpegOpusAudio.from_probe(url, **FFMPEG_OPTIONS)

    async def after_play(_):
        await clear_skip_requests(guild)

        if loop_enabled.get(gid) and owner != bot.user.id:
            guild_queues[gid].insert(0, {"query": query, "owner_id": owner})


        await play_music(guild, msg)

    vc.play(src, after=lambda e: asyncio.run_coroutine_threadsafe(after_play(e), bot.loop))

    await update_nowplaying(guild, title, thumb)
    await update_queue_display(guild)


# ==================================================
# HARD STOP
# ==================================================
async def hard_stop(guild):
    gid = guild.id
    smart_fail_count.pop(gid, None)



    # 1️⃣ إيقاف الصوت
    vc = guild.voice_client
    if vc:
        await vc.disconnect(force=True)

    # 2️⃣ حذف كل رسائل البوت من القناة
    ch = guild.get_channel(guild_music_settings[gid])
    async for m in ch.history(limit=200):
        try:
            await m.delete()
        except:
            pass

    # 3️⃣ تصفير كل الحالات
    guild_queues[gid] = []
    guild_current[gid] = None
    skip_pending[gid] = None
    smart_play_enabled[gid] = False
    smart_play_seed[gid] = set()
    played_video_ids[gid] = set()

    guild_nowplaying_msg[gid] = None
    guild_queue_msg[gid] = None

    # 4️⃣ نرجع البوت كأنه جديد
    first_run_cleanup[gid] = False




# ==================================================
# SOFT REFRESH
# ==================================================
async def soft_refresh(guild):
    gid = guild.id

    vc = guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()  # ⬅️ هذا السطر المهم

    ch = guild.get_channel(guild_music_settings[gid])
    if ch:
        async for m in ch.history(limit=200):
            if m.author.bot:
                try:
                    await m.delete()
                except:
                    pass

    guild_nowplaying_msg[gid] = None
    guild_queue_msg[gid] = None
    guild_current[gid] = None
    guild_queues[gid] = []
    skip_pending[gid] = None

    smart_play_enabled[gid] = False
    smart_play_seed[gid] = set()
    played_video_ids[gid] = set()
    smart_fail_count.pop(gid, None)




# ==================================================
# MESSAGE LISTENER
# ==================================================


def spotify_to_title(text):
    try:
        match = SPOTIFY_TRACK_REGEX.search(text)
        if not match:
            return None

        track_id = match.group(1)
        track = sp.track(track_id)

        song = track["name"]
        artist = track["artists"][0]["name"]

        return f"{artist} - {song}"
    except Exception as e:
        print("Spotify error:", e)
        return None

def spotify_smart_pick(gid):
    seeds = list(smart_play_seed.get(gid, []))
    if not seeds:
        return None

    artist_name = random.choice(seeds)

    try:
        # 1️⃣ نحاول recommendations أولاً
        result = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
        if not result["artists"]["items"]:
            raise Exception("Artist not found on Spotify")

        artist_id = result["artists"]["items"][0]["id"]

        recs = sp.recommendations(
            seed_artists=[artist_id],
            limit=20,
            min_popularity=30
        )

        tracks = recs.get("tracks", [])
        if tracks:
            track = random.choice(tracks)
            return f"{track['artists'][0]['name']} - {track['name']}"

        raise Exception("Empty recommendations")

    except Exception as e:
        print("Spotify Smart fallback:", e)

        # 2️⃣ 🔄 Fallback: search أغاني للفنان
        try:
            res = sp.search(q=artist_name, type="track", limit=20)
            items = res["tracks"]["items"]
            if not items:
                return None

            track = random.choice(items)
            return f"{track['artists'][0]['name']} - {track['name']}"

        except Exception as e:
            print("Spotify fallback failed:", e)
            return None


async def safe_delete(msg):
    try:
        await msg.delete()
    except (discord.NotFound, discord.Forbidden):
        pass



def feed_smart_seed(gid, query):
    # نخليها تضيف seeds دائماً (حتى لو السمارت مطفي)
    # لان انت تريد لمن تفعله لاحقاً يكون عنده تاريخ
    artist = extract_artist(query)
    if not artist:
        return
    smart_play_seed.setdefault(gid, set()).add(artist)





@bot.event
async def on_message(msg):
    if msg.author.bot or not msg.guild:
        return

    gid = msg.guild.id

    # ✅ هذا التعديل المهم
    if gid not in guild_music_settings:
        await bot.process_commands(msg)
        return

    raw = msg.content.strip()

    
    # 🚨 إذا الرسالة أمر، لا نتدخل
    if raw.startswith(bot.command_prefix):
        await bot.process_commands(msg)
        return



    # ===== Spotify PLAYLIST =====
    playlist_tracks, playlist_artists = spotify_playlist_to_tracks(raw)
    if playlist_tracks:
        await safe_delete(msg)

        smart_play_seed.setdefault(gid, set()).update(playlist_artists)

        first = playlist_tracks[0]
        guild_queues.setdefault(gid, []).append({
            "query": first,
            "owner_id": msg.author.id
        })
        feed_smart_seed(gid, first)

        for song in playlist_tracks[1:]:
            guild_queues[gid].append({
                "query": song,
                "owner_id": msg.author.id
            })
            feed_smart_seed(gid, song)

        if not guild_current.get(gid):
            await play_music(msg.guild, msg)
        else:
            await update_queue_display(msg.guild)

        return

    # ===== Spotify TRACK =====
    spotify_title = spotify_to_title(raw)

    if "open.spotify.com/track" in raw and not spotify_title:
        await safe_delete(msg)
        await msg.channel.send(
            "⚠️ Spotify is slow right now, try again 💚",
            delete_after=5
        )
        return

    query = spotify_title if spotify_title else raw

    guild_queues.setdefault(gid, []).append({
        "query": query,
        "owner_id": msg.author.id
    })
    feed_smart_seed(gid, query)

    await safe_delete(msg)

    if not guild_current.get(gid):
        await play_music(msg.guild, msg)
    else:
        await update_queue_display(msg.guild)

    await bot.process_commands(msg)
    return






# ==================================================
# SETUP COMMAND (SAVES CHANNEL)
# ==================================================
@bot.command()
async def setup(ctx):

    if ctx.guild.id not in ALLOWED_GUILDS:
        return await send_license_error(ctx)

    opts = [
        discord.SelectOption(label=ch.name, value=str(ch.id))
        for ch in ctx.guild.text_channels
    ]

    class Pick(discord.ui.Select):
        async def callback(self, inter):
            cid = int(self.values[0])
            guild_music_settings[ctx.guild.id] = cid

            settings["guild_music_settings"] = guild_music_settings
            save_settings(settings)

            await inter.response.send_message("Music channel saved ✓", ephemeral=True)

    view = discord.ui.View()
    view.add_item(Pick(placeholder="Select music channel", options=opts))

    await ctx.send("🎶 Choose the music channel:", view=view)


# ==================================================
# READY EVENT
# ==================================================
@bot.event
async def on_ready():
    print(f"🔥 Logged in as {bot.user}")


# ==================================================
# RUN BOT
# ==================================================

bot.run(os.getenv("DISCORD_TOKEN"))

