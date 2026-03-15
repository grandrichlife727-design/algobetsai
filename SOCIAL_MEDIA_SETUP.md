# AlgoBets AI - Social Media Automation Setup Guide

## 🎯 Overview

This automation system pulls your top picks from the AlgoBets API and automatically posts them to:
- **Twitter** - Engagement-optimized tweets with your best edges
- **Discord** - Rich embeds to Public/Premium/VIP channels

## 📋 Features

✅ **Smart Filtering** - Only posts picks with ≥2% edge and ≥65% confidence  
✅ **Tier-Based Posting** - Different content for Public/Premium/VIP  
✅ **Engagement Optimized** - Professional formatting with emojis and hashtags  
✅ **Scheduled Posting** - 4x daily at optimal times  
✅ **Phase 1 Integration** - Shows ensemble scores, sharp money, public fades  

---

## 🔧 Setup Instructions

### Step 1: Install Dependencies

```bash
pip install httpx schedule python-dotenv
```

### Step 2: Configure Environment Variables

Add these to your `.env` file or environment:

```bash
# API Configuration
API_BASE_URL=https://algobetsai.onrender.com
BACKEND_API_KEY=your_backend_api_key_here

# Twitter API v2 Credentials
# Get these from: https://developer.twitter.com/en/portal/dashboard
TWITTER_BEARER_TOKEN=your_twitter_bearer_token_here

# Discord Bot Credentials
# Get these from: https://discord.com/developers/applications
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_PUBLIC_CHANNEL_ID=your_public_channel_id
DISCORD_PREMIUM_CHANNEL_ID=your_premium_channel_id
DISCORD_VIP_CHANNEL_ID=your_vip_channel_id

# Posting Configuration (optional)
POST_TOP_N_PICKS=3                    # Post top 3 picks
MIN_EDGE_FOR_POSTING=2.0              # Minimum 2% edge
MIN_CONFIDENCE_FOR_POSTING=65.0       # Minimum 65% confidence
APP_TIMEZONE=America/New_York
```

### Step 3: Get Twitter API Credentials

1. Go to [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)
2. Create a new app (or use existing)
3. Navigate to "Keys and tokens"
4. Generate a **Bearer Token** (for API v2)
5. Copy the Bearer Token to your `.env` file

### Step 4: Get Discord Bot Credentials

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to "Bot" section and create a bot
4. Copy the **Bot Token**
5. Enable these **Privileged Gateway Intents**:
   - Message Content Intent
6. Go to OAuth2 → URL Generator:
   - Select scopes: `bot`
   - Select permissions: `Send Messages`, `Embed Links`
7. Copy the generated URL and invite the bot to your server
8. Get channel IDs:
   - Enable Developer Mode in Discord (User Settings → Advanced)
   - Right-click channels → Copy ID

---

## 🚀 Usage

### Test Mode (Preview Without Posting)

```bash
python social_media_bot.py test
```

This shows what would be posted without actually posting.

### Manual Run (Post Once)

```bash
python social_media_bot.py
```

### Scheduled Posting (Recommended)

```bash
python social_scheduler.py
```

This runs continuously and posts at:
- **9:00 AM ET** - Morning picks
- **12:00 PM ET** - Lunch update  
- **6:00 PM ET** - Evening picks
- **9:00 PM ET** - Prime time update

---

## 📱 Example Outputs

### Twitter Format (Single Pick)

```
🔥 EDGE ALERT

Lakers -5.5 (-110)
vs Celtics

📊 Edge: +3.2%
🎯 Confidence: 72%
⚡ Sharp Money Detected

#SportsBetting #NBA
```

### Twitter Format (Multiple Picks)

```
🔥 TODAY'S TOP EDGES

1️⃣ Lakers -5.5 | +3.2% EV
2️⃣ Chiefs ML | +2.8% EV  
3️⃣ Over 225.5 | +2.1% EV

All 65%+ confidence

#SportsBetting
```

### Discord Format (VIP Tier)

```
🔥 NBA Edge Alert

**Lakers vs Celtics**
**Lakers -5.5** (-110)

📊 **Edge:** +3.2%
🎯 **Confidence:** 72%
🔥 **Ensemble Score:** 85/100
👥 **Public:** 68%
⚡ **Sharp Money:** 5.2 cents/min
⏰ **Timing Score:** 78/100

AlgoBets AI • VIP TIER
```

---

## 🎨 Customization

### Adjust Posting Thresholds

Edit these in your `.env`:

```bash
POST_TOP_N_PICKS=5                    # Post top 5 instead of 3
MIN_EDGE_FOR_POSTING=3.0              # Require 3% edge minimum
MIN_CONFIDENCE_FOR_POSTING=70.0       # Require 70% confidence
```

### Change Posting Schedule

Edit `social_scheduler.py`:

```python
# Add more posting times
schedule.every().day.at("15:00").do(job)  # 3 PM
schedule.every().day.at("23:00").do(job)  # 11 PM

# Or post every N hours
schedule.every(2).hours.do(job)
```

### Customize Tweet Templates

Edit the `format_pick_for_twitter()` function in `social_media_bot.py` to change:
- Emojis
- Hashtags
- Layout
- Character limits

### Customize Discord Embeds

Edit the `format_pick_for_discord()` function to change:
- Colors
- Fields shown
- Tier-specific details

---

## 🔄 Deployment Options

### Option 1: Run on Your Server

```bash
# Install as systemd service (Linux)
sudo nano /etc/systemd/system/algobets-social.service
```

```ini
[Unit]
Description=AlgoBets AI Social Media Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/algobetsai
ExecStart=/usr/bin/python3 social_scheduler.py
Restart=always
EnvironmentFile=/path/to/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable algobets-social
sudo systemctl start algobets-social
```

### Option 2: Run on Render.com

Add to your `render.yaml`:

```yaml
services:
  - type: worker
    name: algobets-social-bot
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: python social_scheduler.py
    envVars:
      - key: TWITTER_BEARER_TOKEN
        sync: false
      - key: DISCORD_BOT_TOKEN
        sync: false
```

### Option 3: Run on Heroku

```bash
# Create Procfile
echo "worker: python social_scheduler.py" > Procfile

# Deploy
heroku create algobets-social-bot
git push heroku main
heroku ps:scale worker=1
```

### Option 4: GitHub Actions (Free)

Create `.github/workflows/social-posts.yml`:

```yaml
name: Social Media Posts

on:
  schedule:
    - cron: '0 13,16,22,1 * * *'  # 9am, 12pm, 6pm, 9pm ET
  workflow_dispatch:

jobs:
  post:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install httpx
      - run: python social_media_bot.py
        env:
          API_BASE_URL: ${{ secrets.API_BASE_URL }}
          TWITTER_BEARER_TOKEN: ${{ secrets.TWITTER_BEARER_TOKEN }}
          DISCORD_BOT_TOKEN: ${{ secrets.DISCORD_BOT_TOKEN }}
          DISCORD_VIP_CHANNEL_ID: ${{ secrets.DISCORD_VIP_CHANNEL_ID }}
```

---

## 🛡️ Best Practices

### Rate Limiting

- Twitter: 50 tweets per 24 hours (free tier)
- Discord: 5 messages per 5 seconds per channel
- The bot includes 1-second delays between Discord posts

### Content Strategy

**Public Channel (Twitter + Discord Public):**
- Only post highest edge picks (≥4%)
- Build credibility and attract free users

**Premium Channel:**
- Post good edge picks (≥2.5%)
- Show value of Premium tier

**VIP Channel:**
- Post all quality picks with full details
- Include Phase 1 algorithm insights
- Justify VIP pricing

### Monitoring

Check logs regularly:

```bash
# If running as systemd service
sudo journalctl -u algobets-social -f

# If running manually
python social_scheduler.py 2>&1 | tee social_bot.log
```

---

## 🐛 Troubleshooting

### "Twitter credentials not configured"

- Verify `TWITTER_BEARER_TOKEN` is set in environment
- Check token is valid at [Twitter Developer Portal](https://developer.twitter.com/en/portal/dashboard)

### "Discord credentials not configured"

- Verify `DISCORD_BOT_TOKEN` is set
- Ensure bot is invited to your server
- Check channel IDs are correct (right-click → Copy ID)

### "No picks meet posting criteria"

- Lower `MIN_EDGE_FOR_POSTING` or `MIN_CONFIDENCE_FOR_POSTING`
- Check your API is returning picks: `curl https://algobetsai.onrender.com/picks`

### "Error fetching picks"

- Verify `API_BASE_URL` is correct
- Check `BACKEND_API_KEY` if required
- Test API manually: `curl https://algobetsai.onrender.com/picks`

---

## 📊 Analytics & Tracking

### Track Engagement

Add UTM parameters to links in tweets:

```python
# In format_pick_for_twitter()
lines.append(f"\n🔗 algobets.ai/app?utm_source=twitter&utm_campaign=daily_picks")
```

### Monitor Performance

Create a simple analytics dashboard:

```python
# Track posts in a database
import sqlite3

def log_post(platform, pick_id, edge, timestamp):
    conn = sqlite3.connect('social_analytics.db')
    conn.execute('''
        INSERT INTO posts (platform, pick_id, edge, timestamp)
        VALUES (?, ?, ?, ?)
    ''', (platform, pick_id, edge, timestamp))
    conn.commit()
```

---

## 🎯 Next Steps

1. **Test in test mode** first: `python social_media_bot.py test`
2. **Do a manual run** to verify: `python social_media_bot.py`
3. **Set up scheduled posting**: `python social_scheduler.py`
4. **Monitor for 24 hours** to ensure stability
5. **Deploy to production** using one of the deployment options

---

## 💡 Pro Tips

- **Vary your content** - Don't post the same format every time
- **Engage with replies** - Respond to comments on Twitter/Discord
- **Track what works** - Monitor which picks get the most engagement
- **A/B test formats** - Try different emoji combinations and hashtags
- **Cross-promote** - Mention Discord in tweets, Twitter in Discord
- **Show results** - Post follow-ups when picks hit

---

## 📞 Support

If you need help:
1. Check the troubleshooting section above
2. Review the example outputs
3. Test in test mode first
4. Check API logs for errors

---

**Built with ❤️ for AlgoBets AI**

