import discord
from discord.ext import commands
import asyncio
import yt_dlp
import os
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from youtube_search import YoutubeSearch
import logging
from dotenv import load_dotenv

# Set up logging for debugging and error tracking
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('music_bot.log'),  # Log to file
        logging.StreamHandler()  # Also log to console
    ]
)
logger = logging.getLogger('music_bot')
logger = logging.getLogger('music_bot')

# Load environment variables for sensitive data like tokens and credentials
load_dotenv()
TOKEN = os.getenv('TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Initialize the bot with specific permissions
intents = discord.Intents.default()
intents.message_content = True  # This is required to read messages
bot = commands.Bot(command_prefix='!', intents=intents)

# YouTube DL options for audio extraction
ydl_opts = {
    'format': 'bestaudio/best',  # Fetch the best audio quality
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',  # Extract audio and convert it
        'preferredcodec': 'mp3',  # Save as MP3 format
        'preferredquality': '192',  # Set quality to 192kbps
    }],
    'noplaylist': True,  # Don't download playlists
    'quiet': True,  # Suppress output from yt-dlp
}

# Set up Spotify client using the provided credentials
try:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    ))
except Exception as e:
    logger.error(f"Failed to initialize Spotify client: {e}")
    sp = None

# Create a dictionary to hold queues for each guild
queues = {}


# Downlaod Helper
def download_audio(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'song.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return 'song.mp3', info.get('title', 'Unknown Title')


# MusicPlayer class to manage voice channel connections and queues
class MusicPlayer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.guild_id = ctx.guild.id
        self.queue = []  # Store queued songs
        self.current_song = None  # Track the song that's currently playing
        self.is_playing = False  # Flag to indicate if a song is playing
        self.voice_client = None  # Voice client to manage audio stream

    async def join_voice_channel(self, ctx):
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.move_to(channel)
            else:
                self.voice_client = await channel.connect()
            return True
        else:
            await ctx.send("You need to be in a voice channel to use this command.")
            return False


    async def add_to_queue(self, song_info):
        """Adds a song to the queue and starts playing if nothing is playing."""
        self.queue.append(song_info)
        if not self.is_playing:
            await self.play_next()

    async def play_next(self):
        try:
            # Check if queue is empty
            if not self.queue:
                self.is_playing = False
                self.current_song = None
                await self.ctx.send("Queue is empty. Disconnecting...")
                if self.voice_client and self.voice_client.is_connected():
                    await self.voice_client.disconnect()
                return

            # Check voice client state
            if not self.voice_client or not self.voice_client.is_connected():
                self.is_playing = False
                self.current_song = None
                return

            self.is_playing = True
            self.current_song = self.queue.pop(0)
            
            await self.ctx.send(f"🎵 Now playing: {self.current_song['title']}")

            def after_playing(error):
                if error:
                    logger.error(f"Playback error: {error}")
                
                # Create task in the correct event loop
                coro = self.play_next()
                fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                try:
                    fut.result(timeout=30)  # Add timeout
                except asyncio.TimeoutError:
                    logger.error("play_next took too long to complete")
                except Exception as e:
                    logger.error(f"Error in after_playing coroutine: {e}")

            # More robust FFmpeg options with error handling
            ffmpeg_options = {
                'options': '-vn -loglevel quiet -nostdin',
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -timeout 30'
            }
            
            try:
                source = discord.FFmpegPCMAudio(
                    self.current_song['url'], 
                    executable="ffmpeg",
                    **ffmpeg_options
                )
                
                # Ensure voice client is still valid
                if self.voice_client and self.voice_client.is_connected():
                    # Clear any existing audio buffers
                    if self.voice_client.is_playing():
                        self.voice_client.stop()
                        
                    # Add error correction
                    source = discord.PCMVolumeTransformer(source)
                    self.voice_client.play(source, after=after_playing)
                else:
                    await self.play_next()  # Try next song if disconnected
                    
            except discord.ClientException as e:
                logger.error(f"ClientException: {e}")
                await asyncio.sleep(1)  # Small delay before retrying
                await self.play_next()
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                await self.ctx.send(f"Error playing {self.current_song['title']}")
                await asyncio.sleep(1)
                await self.play_next()
                
        except Exception as e:
            logger.error(f"Critical error in play_next: {e}")
            self.is_playing = False
            self.current_song = None
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()

    async def skip(self):
        """Skips the currently playing song."""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
            await self.ctx.send("Skipped the current song.")
            return True
        return False

    async def pause(self):
        """Pauses the currently playing song."""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            await self.ctx.send("Paused the music.")
            return True
        return False

    async def resume(self):
        """Resumes the currently paused song."""
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            await self.ctx.send("Resumed the music.")
            return True
        return False

    async def stop(self):
        """Stops the music and clears the queue."""
        if self.voice_client:
            self.queue = []
            self.is_playing = False
            self.voice_client.stop()
            await self.voice_client.disconnect()
            await self.ctx.send("Stopped the music and cleared the queue.")
            return True
        return False

    async def current(self):
        """Shows the current song playing."""
        if self.voice_client and self.voice_client.is_playing():
            await self.ctx.send(f"Now playing: {self.current_song['title']}")
            return True
        return False

    async def queue_list(self):
        """Displays the songs in the queue."""
        if self.queue:
            embed = discord.Embed(title="Queue", description="Current queue", color=0x00ff00)
            for idx, song in enumerate(self.queue):
                embed.add_field(name=song['title'], value=f"{idx + 1 }: Added by {song['added_by']}", inline=False)
            await self.ctx.send(embed=embed)
        else:
            await self.ctx.send("The queue is currently empty.")

    async def queue_clear(self):
        """Clears the song queue."""
        self.queue = []
        await self.ctx.send("Cleared the queue.")

    async def delete_from_queue(self, idx: int):
        """Deletes a song from the queue by its index."""
        if 0 <= idx < len(self.queue):
            deleted_song = self.queue.pop(idx-1)
            await self.ctx.send(f"Deleted {deleted_song['title']} from the queue.")
        else:
            await self.ctx.send("Invalid song index.")

# Inisializing the Class
def get_player(ctx):
    guild_id = ctx.guild.id
    if guild_id not in queues:
        queues[guild_id] = MusicPlayer(ctx)
    return queues[guild_id]

# Function to fetch song information from YouTube or Spotify
async def get_song_info(query, ctx):
    # Check if it's a Spotify link
    if 'spotify.com/track' in query and sp:
        try:
            track_id = query.split('/')[-1].split('?')[0]
            track_info = sp.track(track_id)
            search_query = f"{track_info['name']} {' '.join([artist['name'] for artist in track_info['artists']])}"

            # Search for the track on YouTube
            results = YoutubeSearch(search_query, max_results=1).to_dict()
            if results:
                video_id = results[0]['id']
                youtube_url = f"https://www.youtube.com/watch?v={video_id}"

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(youtube_url, download=False)

                # Send message when a song is found (even if not played yet)
                await ctx.send(f"Found the song on YouTube: {info['title']}")
                logger.info(f"Found song: {info['title']}")

                return {
                    'title': track_info['name'],
                    'url': info['url'],
                    'duration': info['duration'],
                    'thumbnail': track_info['album']['images'][0]['url'] if track_info['album']['images'] else None,
                    'source': 'spotify',
                    "added_by": ctx.author.name
                }
        except Exception as e:
            await ctx.send(f"Error processing Spotify link: {e}")
            return None
    
    # Handle YouTube links or search queries
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Check if it's a direct YouTube link
            if 'youtube.com' in query or 'youtu.be' in query:
                info = ydl.extract_info(query, download=False)
            else:
                # Search YouTube for the query
                results = YoutubeSearch(query, max_results=1).to_dict()
                if not results:
                    await ctx.send(f"No results found for: {query}")
                    return None
                
                video_id = results[0]['id']
                youtube_url = f"https://www.youtube.com/watch?v={video_id}"
                info = ydl.extract_info(youtube_url, download=False)

            # Send message when a song is found (even if not played yet)
            await ctx.send(f"Found the song on YouTube: {info['title']}")
            logger.info(f"Found song: {info['title']}")

            return {
                'title': info['title'],
                'url': info['url'],
                'duration': info['duration'],
                'thumbnail': info['thumbnail'],
                'source': 'youtube',
                "added_by": ctx.author.name
            }
    except Exception as e:
        await ctx.send(f"Error processing YouTube query: {e}")
        return None

# Commands for the bot

@bot.event
async def on_ready():
    """Bot startup event."""
    logger.info(f'Bot is ready! Logged in as {bot.user}')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="Ba7a Chikour"))

@bot.command(name='join', help='Joins the voice channel you are in')
async def join(ctx):
    """Command for bot to join the user's voice channel."""
    player = get_player(ctx)
    success = await player.join_voice_channel()
    if success:
        await ctx.send(f"Joined {ctx.author.voice.channel.name}")

@bot.command(name='play', help='Plays a song from YouTube or Spotify')
async def play(ctx, *, query):
    """Plays a song either from YouTube or Spotify."""
    player = get_player(ctx)
    success = await player.join_voice_channel(ctx)
    if not success:
        return
    
    # Join voice channel if not already connected
    if not player.voice_client or not player.voice_client.is_connected():
        success = await player.join_voice_channel()
        if not success:
            return
    
    await ctx.send(f"🔍 Searching for: {query}")
    
    song_info = await get_song_info(query, ctx)
    if song_info:
        await player.add_to_queue(song_info)
        if not player.is_playing:
            await ctx.send(f"Added to queue: {song_info['title']}")

@bot.command(name='skip', help='Skips the current song')
async def skip(ctx):
    """Skips the currently playing song."""
    player = get_player(ctx)
    await player.skip()

@bot.command(name='pause', help='Pauses the current song')
async def pause(ctx):
    """Pauses the currently playing song."""
    player = get_player(ctx)
    await player.pause()

@bot.command(name='resume', help='Resumes the paused song')
async def resume(ctx):
    """Resumes the currently paused song."""
    player = get_player(ctx)
    await player.resume()

@bot.command(name='stop', help='Stops playing and clears the queue')
async def stop(ctx):
    """Stops the music and clears the queue."""
    player = get_player(ctx)
    await player.stop()

@bot.command(name='queue', help='Shows the current queue')
async def queue(ctx):
    """Displays the current song queue."""
    player = get_player(ctx)
    await player.queue_list()

@bot.command(name='clear', help='Clears the queue list')
async def clear(ctx):
    """Clears the song queue."""
    player = get_player(ctx)
    await player.queue_clear()

@bot.command(name="delete", help="Delete a song from the queue by ID")
async def delete(ctx, id: int):
    """Deletes a song from the queue by its ID."""
    player = get_player(ctx)
    await player.delete_from_queue(id)


@bot.command(name='negro',help='Tagui beggar many times')
async def negro(ctx,times : int,message : str):
    if times > 10:
        await ctx.send("You can't tagui beggar more than 10 times")
        await ctx.send("Wech l7aj mahoch 7a yhrb negro")
        return
    BeggarId = 565451388507914260
    Beggar = await bot.fetch_user(BeggarId)
    async def Sending(num : int):
        for i in range(num):
            await ctx.send(Beggar.mention + ' ' + message)
    await Sending(times)

@bot.command(name='aoko',help='Tagui aoko many times')
async def aoko(ctx,times : int):
    if times > 10:
        await ctx.send("You can't tagui aoko more than 10 times")
        await ctx.send("Aw ydir f kach therom m3a sa3id")
        return
    AokoId = 615291294926897193
    Aoko = await bot.fetch_user(AokoId)
    async def Sending(num : int):
        for i in range(num):
            await ctx.send(Aoko.mention)
    await Sending(times)

@bot.command(name='ba7a',help='Tagui ba7a many times')
async def ba7a(ctx,times : int):
    if times > 10:
        await ctx.send("You can't tagui beggar more than 10 times")
        await ctx.send("Aw yessena f kach 9bayliya tb3t dok yji")
        return
    Ba7aId = 881975622778384486
    Ba7a = await bot.fetch_user(Ba7aId)
    async def Sending(num : int):
        for i in range(num):
            await ctx.send(Ba7a.mention)
    await Sending(times)

@bot.command(name='weed',help='Tagui weed many times')
async def weed(ctx,times : int):
    if times > 10:
        await ctx.send("You can't tagui beggar more than 10 times")
        await ctx.send("Aw ycordini khalih")
        return
    WeedId = 818155557865127956
    Weed = await bot.fetch_user(WeedId)
    async def Sending(num : int):
        for i in range(num):
            await ctx.send(Weed.mention)
    await Sending(times)

@bot.command(name="racist" , help="Tagui racist many times")
async def racist(ctx, times : int):
    if times > 10:
        await ctx.send("You can't tagui racist more than 10 times")
        await ctx.send("Aw ya7gar f kach negro")
        return
    RacistId = 460184827119927326
    Racist = await bot.fetch_user(RacistId)
    async def Sending(num : int):
        for i in range(num):
            await ctx.send(Racist.mention)
    await Sending(times)


@bot.command(name='peaktic',help='Tagui hadok 4 m9awdin')
async def peaktic(ctx):
    BeggarId = 565451388507914260
    Beggar = await bot.fetch_user(BeggarId)
    AokoId = 615291294926897193
    Aoko = await bot.fetch_user(AokoId)
    Ba7aId = 881975622778384486
    Ba7a = await bot.fetch_user(Ba7aId)
    WeedId = 818155557865127956
    Weed = await bot.fetch_user(WeedId)
    await ctx.send(Beggar.mention)
    await ctx.send(Aoko.mention)
    await ctx.send(Ba7a.mention)
    await ctx.send(Weed.mention)
    await ctx.send("PEAKTIC !!!!!!!!!!!")
    return

@bot.command(name="current", help="Displays current playing song with details")
async def current(ctx):
    player = get_player(ctx)
    current_song = player.current_song
    
    if not current_song or not player.is_playing:
        await ctx.send("No song is currently playing.")
        return

    # Create embed
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"[{current_song['title']}]({current_song['url']})",
        color=discord.Color.blurple()  # You can change this color
    )
    
    # Add fields
    minutes, seconds = divmod(current_song['duration'], 60)
    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}", inline=True)
    embed.add_field(name="Requested by", value=current_song['added_by'], inline=True)
    
    # Add thumbnail if available
    if current_song.get('thumbnail'):
        embed.set_thumbnail(url=current_song['thumbnail'])
    
    # Add footer with playback status
    embed.set_footer(text=f"Playing in {ctx.author.voice.channel.name if ctx.author.voice else 'unknown'} channel")
    
    await ctx.send(embed=embed)

@bot.event
async def on_message(message):
    # Make sure the bot doesn't respond to its own messages
    if await bot.process_commands(message):
        return
    if message.author == bot.user:
        return
    # Check if the message contains "BB"
    if "9bayliya" in message.content.lower():
        await message.channel.send("👀 Did someone say *9bayliyat*? 🎉")
    if "tp"  in message.content.lower():
        await message.channel.send("C'est un Pointeur")
    if "beggar" in message.content.lower():
        BeggarId = 565451388507914260
        Beggar = await bot.fetch_user(BeggarId)
        await message.channel.send(f"ya {Beggar.mention} am y3ytolk")
    if any(aoko in message.content.lower() for aoko in ["aoko", "ahmed", "a7med"]):
        AokoId = 615291294926897193
        Aoko = await bot.fetch_user(AokoId)
        await message.channel.send(f"ya {Aoko.mention} am y3ytolk")
    if any(ba7a in message.content.lower() for ba7a in ["ba7a", "baha", "bahaa","back"]):
        BahaId = 881975622778384486
        Baha = await bot.fetch_user(BahaId)
        await message.channel.send(f"ya {Baha.mention} am y3ytolk")
    if any(weed in message.content.lower() for weed in ["weed", "walid"]):
        WalidId =  818155557865127956
        Walid = await bot.fetch_user(WalidId)
        await message.channel.send(f"ya {Walid.mention} am y3ytolk")
    if any(karim in message.content.lower() for karim in ["data",f" ai "]):
        channel = 1358519628040638802
        if message.channel.id == channel:
            return
        KarimId =   568161566625890334
        Karim = await bot.fetch_user(KarimId)
        await message.channel.send(f" {Karim.mention} LA DATA MENTIONEED")





# Run the bot
bot.run(TOKEN)
