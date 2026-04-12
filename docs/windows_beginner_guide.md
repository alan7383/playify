# 🌸 The Absolute Beginner's Guide to Running Playify on Windows

Don't know anything about coding, Docker, or Python? **Don't panic!** (つ≧▽≦)つ
This guide is made specially for you. We'll set up the bot step-by-step!

## Step 1: Download Playify

1. Go to the [Playify GitHub page](https://github.com/alan7383/playify).
2. Click the green **"<> Code"** button.
3. Click **"Download ZIP"**.
4. Extract the ZIP file anywhere on your computer (for example, on your Desktop). Open the folder so you can see all the files.

## Step 2: Configure Your Secret Tokens

Playify needs to connect to your Discord bot and Spotify.
1. In the Playify folder, you will see a file named `.env.example`.
2. **Rename** this file to just `.env` (remove the `.example` part).
   > *Note: If you can't see the file extension, click "View" -> "Show" -> "File name extensions" at the top of your Windows file explorer.*
3. Open the `.env` file with **Notepad**.
4. Fill in your tokens. It should look like this:
   ```ini
   DISCORD_TOKEN=your_long_discord_token_here
   SPOTIFY_CLIENT_ID=your_spotify_id_here
   SPOTIFY_CLIENT_SECRET=your_spotify_secret_here
   # GENIUS_TOKEN is optional!
   ```
5. **Save** the file and close Notepad.

## Step 3: Run the Bot!

1. In the Playify folder, find the file named `start.bat` (or just `start` with a gear/batch icon).
2. **Double-click** it!
   - 🪄 *Magic:* If you don't have Python installed, the script will automatically download and install it for you!
   - If that happens, it will ask you to close the window and double-click `start.bat` one more time.
3. The first time you run it, it might take a few minutes to download the needed libraries (like `yt-dlp` and `Playwright`). Just let it do its thing.
4. Once you see **"Logged in as..."**, your bot is online on Discord! 🎉

---

### FAQ / Troubleshooting 🛠️

**Q: The bot says "FFmpeg is not found" when trying to play music!**
A: FFmpeg is needed to process the audio. Download `ffmpeg.exe` and place it **in the exact same folder** as `playify.py`. Windows will automatically find it!
⬇ [Download FFmpeg 6.1.1 here](https://www.videohelp.com/software?d=ffmpeg-6.1.1-full_build.7z) (extract the archive and grab `ffmpeg.exe` from the `bin` folder).

**Q: Do I need to do this every time?**
A: Nope! Next time you want to start the bot, just double-click `start.bat`. That's it!
