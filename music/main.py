import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from collections import deque
import logging

# logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MusicBot')

# config
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='h!', intents=intents)

YTDL_OPTIONS = {
    'format': '140/141/251/bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',  # Fallback cụ thể: m4a AAC (140/141) + webm Opus (251) → luôn có trên 99% video
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',  # Giữ nếu có, giúp unlock formats ẩn

    # Fix signature + player 2025
    'retries': 10,
    'fragment_retries': 10,
    'http_headers': {'Connection': 'close'},
    'extractor_args': {
        'youtube': {
            'player_client': ['all'],  # Lấy hết client (web/android/ios) → fix missing formats (issue #11783)
            'skip': ['translated_subs', 'live_chat'],
        }
    },
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
    'options': '-vn -b:a 192k'  # Giảm bitrate cho ổn định
}

# Class queue 
class MusicQueue:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.voice_client = None
    
    def add(self, song):
        self.queue.append(song)
        logger.info(f"Added to queue: {song['title']}")
    
    def get_next(self):
        if self.queue:
            return self.queue.popleft()
        return None
    
    def clear(self):
        self.queue.clear()
        logger.info("Queue cleared")

# Dictionary save queue 
music_queues = {}

def get_queue(guild_id):
    if guild_id not in music_queues:
        music_queues[guild_id] = MusicQueue()
    return music_queues[guild_id]

# idk
async def get_song_info(url):
    try:
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False))
            
            if 'entries' in info:
                info = info['entries'][0]
            
            return {
                'url': info['url'],
                'title': info['title'],
                'duration': info.get('duration', 0),
                'webpage_url': info['webpage_url']
            }
    except Exception as e:
        logger.error(f"Error extracting info: {e}")
        raise

# play
async def play_next(ctx):
    queue = get_queue(ctx.guild.id)
    
    if not queue.queue:
        queue.current = None
        logger.info(f"Queue empty in {ctx.guild.name}")
        return
    
    try:
        song = queue.get_next()
        queue.current = song
        
        source = discord.FFmpegPCMAudio(song['url'], **FFMPEG_OPTIONS)
        
        def after_playing(error):
            if error:
                logger.error(f"Player error: {error}")
            asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        
        queue.voice_client.play(source, after=after_playing)
        
        embed = discord.Embed(
            title="🎵 Đang phát",
            description=f"[{song['title']}]({song['webpage_url']})",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"Now playing: {song['title']}")
        
    except Exception as e:
        logger.error(f"Error in play_next: {e}")
        await ctx.send(f"❌ Lỗi khi phát nhạc: {str(e)}")
        await play_next(ctx)

@bot.event
async def on_ready():
    logger.info(f'{bot.user} đã sẵn sàng!')
    print(f'Bot đã đăng nhập: {bot.user.name}')
    print(f'Prefix: h!')

@bot.command(name='join')
async def join(ctx):
    """Cho bot vào voice channel"""
    try:
        if not ctx.author.voice:
            await ctx.send("❌ Bạn phải vào voice channel trước!")
            return
        
        channel = ctx.author.voice.channel
        queue = get_queue(ctx.guild.id)
        
        if queue.voice_client and queue.voice_client.is_connected():
            await queue.voice_client.move_to(channel)
        else:
            queue.voice_client = await channel.connect()
        
        await ctx.send(f"✅ Đã vào **{channel.name}**")
        logger.info(f"Joined {channel.name} in {ctx.guild.name}")
        
    except Exception as e:
        logger.error(f"Error in join command: {e}")
        await ctx.send(f"❌ Lỗi: {str(e)}")

@bot.command(name='play')
async def play(ctx, *, query):
    """Phát nhạc từ YouTube"""
    try:
        queue = get_queue(ctx.guild.id)
        
        # Kiểm tra voice connection
        if not queue.voice_client or not queue.voice_client.is_connected():
            if ctx.author.voice:
                queue.voice_client = await ctx.author.voice.channel.connect()
            else:
                await ctx.send("❌ Bạn phải vào voice channel trước!")
                return
        
        async with ctx.typing():
            # Tải thông tin bài hát
            if not query.startswith('http'):
                query = f"ytsearch:{query}"
            
            song_info = await get_song_info(query)
            queue.add(song_info)
            
            # Nếu không có gì đang phát, bắt đầu phát
            if not queue.voice_client.is_playing():
                await play_next(ctx)
            else:
                embed = discord.Embed(
                    title="➕ Đã thêm vào hàng đợi",
                    description=f"[{song_info['title']}]({song_info['webpage_url']})",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in play command: {e}")
        await ctx.send(f"❌ Lỗi: {str(e)}")

@bot.command(name='skip')
async def skip(ctx):
    """Bỏ qua bài hát hiện tại"""
    try:
        queue = get_queue(ctx.guild.id)
        
        if not queue.voice_client or not queue.voice_client.is_playing():
            await ctx.send("❌ Không có bài hát nào đang phát!")
            return
        
        queue.voice_client.stop()
        await ctx.send("⏭️ Đã bỏ qua bài hát")
        logger.info(f"Skipped song in {ctx.guild.name}")
        
    except Exception as e:
        logger.error(f"Error in skip command: {e}")
        await ctx.send(f"❌ Lỗi: {str(e)}")

@bot.command(name='leave')
async def leave(ctx):
    """Ngắt kết nối bot khỏi voice channel"""
    try:
        queue = get_queue(ctx.guild.id)
        
        if not queue.voice_client:
            await ctx.send("❌ Bot không ở trong voice channel!")
            return
        
        queue.clear()
        await queue.voice_client.disconnect()
        queue.voice_client = None
        
        await ctx.send("👋 Đã rời voice channel")
        logger.info(f"Left voice channel in {ctx.guild.name}")
        
    except Exception as e:
        logger.error(f"Error in leave command: {e}")
        await ctx.send(f"❌ Lỗi: {str(e)}")

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Thiếu tham số! Sử dụng: `h!{ctx.command} <query>`")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"❌ Đã xảy ra lỗi: {str(error)}")

# Chạy bot
if __name__ == "__main__":
    TOKEN = '' # <<<<<<edit token
    
    if not os.path.exists('cookies.txt'):
        logger.warning("cookies.txt not found! Bot will work but may have limitations.")
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
