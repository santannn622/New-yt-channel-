# Stickman Finance Shorts Bot (free, runs on GitHub Actions)

## Setup (all doable from your phone)
1. Create a new GitHub repo (public = unlimited free minutes).
2. Add these files to the repo root: generate.py, requirements.txt, topics.txt, done.txt
3. Add make-short.yml at: .github/workflows/make-short.yml
   (Add file > Create new file > type the path with slashes, paste the content)
4. Get a free Gemini key: aistudio.google.com > Get API key.
5. Make a Telegram bot: message @BotFather > /newbot > copy the token.
   Send any message to your new bot, then open
   https://api.telegram.org/bot<TOKEN>/getUpdates and copy the "chat":{"id": number.
6. Repo > Settings > Secrets and variables > Actions > New secret:
   GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
7. Actions tab > Make Short > Run workflow. The video lands in your Telegram.

## Customising
- Topics: edit topics.txt (one per line).
- Look: edit STYLE in generate.py.
- Voice: set VOICE in the workflow env (e.g. en-US-GuyNeural, en-GB-RyanNeural).
- Music: add a royalty-free .mp3 into a folder named music/ (quiet loop is mixed in).
- Schedule: edit the cron line in make-short.yml.

## Channel playbook (built from your notes)
1. **Returning viewers**: same mascot (Stick), same look, same format every day.
   Each video ends with a teaser for tomorrow's topic (the bot reads the next line of
   topics.txt), so people follow. Optional: set CHANNEL_NAME in the workflow env
   (e.g. `CHANNEL_NAME: Stickonomics`) to add "New money lesson every day on ..." to descriptions.
2. **Community feedback council**: pin a comment on each Short: "Which topic should Stick
   explain next?" Every few days, copy the top-liked ideas to the TOP of topics.txt.
   Viewers who get their topic picked come back and tell friends.
3. **Emotional desires**: every script is built around one feeling (fear of staying broke,
   curiosity, anger at being played, hope, relief). The bot rotates them. Check which
   angle your best videos used and add more topics that trigger it.
4. **Goldilocks zone**: 90-110 words (~40 sec), one idea, one simple-number example,
   a new image every ~4 seconds. If a video drops off early, shorten; if people
   rewatch and ask for more, add detail. Change the word range in DRAFT_PROMPT to tune.

## Voice
With the Cloudflare secrets set, voices come from Deepgram Aura (aura-2-en, then aura-1).
Change the speaker with CF_VOICE in the workflow env (e.g. `CF_VOICE: orion`).
If Cloudflare fails it falls back to Microsoft, then Google.
