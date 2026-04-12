# 🌸 The Absolute Beginner's Guide to Running Playify on Windows

Don't know anything about coding, Docker, or Python? **Don't panic!** (つ≧▽≦)つ
This guide is made specially for you. We'll set up the bot step-by-step!

## Step 1: Download Playify

1. Go to the [Playify GitHub page](https://github.com/alan7383/playify).
2. Click the green **"<> Code"** button.
3. Click **"Download ZIP"**.
4. Extract the ZIP file anywhere on your computer (for example, on your Desktop). Open the folder so you can see all the files.
   ⚠️ *CRITICAL: Make sure you extracted the ZIP! Do not try to run files from inside the ZIP directly.*

## Step 2: Run the Bot!

1. In the Playify folder, find the file named `start.bat` (or just `start` with a gear/batch icon).
2. **Double-click** it!
   - 🪄 *Magic:* If you don't have Python installed, the script will automatically download and install it for you!
   - If that happens, it will ask you to close the window and double-click `start.bat` one more time.
3. The setup script will open a black window and ask you for your Discord Token and Spotify keys. Just **copy them and right-click in the window to paste them in**.
4. Press Enter. The script will set up everything for you and download the required libraries!
5. Once you see **"Logged in as..."**, your bot is online on Discord! 🎉

---

### FAQ / Troubleshooting 🛠️

**Q: The bot says "FFmpeg is not found" when trying to play music!**
A: FFmpeg is needed to process the audio. Download `ffmpeg.exe` and place it **in the exact same folder** as `start.bat`. Windows will automatically find it!
⬇ [Download FFmpeg 6.1.1 here](https://www.videohelp.com/software?d=ffmpeg-6.1.1-full_build.7z) (extract the archive and grab `ffmpeg.exe` from the `bin` folder).

**Q: Do I need to enter my tokens every time?**
A: Nope! The next time you want to start the bot, just double-click `start.bat`. It will instantly start the bot without asking for tokens or installing anything again!
