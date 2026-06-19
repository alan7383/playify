---
title: YouTube Cookies
description: How to bypass YouTube's 403 Forbidden errors and Age Restrictions.
---

Because YouTube aggressively targets bots, you may eventually run into `403 Forbidden` or `Sign in to confirm your age` errors when trying to play YouTube tracks.

Playify has an advanced, native cookie rotation system built right into its core engine to bypass this.

## How it works

Playify looks for 5 specific files in its root folder:
- `cookies_1.txt`
- `cookies_2.txt`
- `cookies_3.txt`
- `cookies_4.txt`
- `cookies_5.txt`

When you play a song, the bot will randomly pick one of these files and use it to authenticate the request as if it were a real browser. By rotating between multiple accounts (if you provide multiple files), you drastically reduce the chance of getting IP-banned or flagged by YouTube.

## Extracting your Cookies

To provide these cookies to Playify, follow these steps:

1. Open your web browser (Chrome, Firefox, or Edge).
2. Install a "Get cookies.txt" extension (for example, **Get cookies.txt LOCALLY** on [Chrome](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) or [Firefox](https://addons.mozilla.org/en-US/firefox/addon/get-cookies-txt-locally/)).
3. Go to [YouTube](https://youtube.com) and make sure you are logged in.
4. Click on the extension and export the cookies for YouTube.
5. Save the downloaded file to your Playify root folder (the folder containing `start.bat`) and rename it to `cookies_1.txt`.

> [!WARNING]
> Do **not** share your `cookies_X.txt` files with anyone! They contain your active session tokens. Anyone with these files can access your YouTube account.

If you have multiple YouTube accounts, you can repeat this process for each account and save them as `cookies_2.txt`, `cookies_3.txt`, etc.

## Does it work immediately?

Yes! You don't need to restart the bot or change any `.env` settings. The bot scans for `AVAILABLE_COOKIES` dynamically. The next time you type `/play`, it will automatically inject the cookies.
