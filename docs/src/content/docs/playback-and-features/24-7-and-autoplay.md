---
title: 24/7 & Autoplay
description: How Playify handles infinite queues and non-stop music.
---

Playify comes equipped with powerful features designed to keep the music going forever, perfect for Lo-Fi radios, community hubs, or parties.

## 24/7 Mode (`/24_7`)

Unlike many bots that disconnect after a few minutes of inactivity, Playify allows you to force the bot to stay in the voice channel indefinitely.

There are three modes you can select using the `/24_7 <mode>` command:

1. **`normal`**: 
   The bot stays in the channel and will **loop the current queue infinitely**. When it reaches the end of the queue, it starts from the beginning again. This is ideal if you have a specific playlist you want to run forever.
2. **`auto`**:
   The bot stays in the channel, but instead of looping the queue, it will **dynamically add similar songs** when the queue runs empty. It acts as an endless radio based on the last played song.
3. **`off`**:
   Disables the 24/7 feature. The bot will automatically disconnect if the queue ends and no one adds a new song after a few minutes.

## Autoplay (`/autoplay`)

If you don't want the bot to stay forever but you still want the music to keep flowing during your session, you can use `/autoplay`.

When `/autoplay` is enabled, reaching the end of your manually queued songs will trigger Playify to fetch a "Mix" from YouTube or a "Station" from SoundCloud based on the previous track, guaranteeing you never sit in silence.
