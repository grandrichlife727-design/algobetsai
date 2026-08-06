"""
AlgoBets AI - Social Media Automation Bot
==========================================
Automatically posts top picks to Twitter and Discord

Features:
- Pulls picks from your API
- Formats for Twitter (280 char limit)
- Posts to Discord channels (VIP/Premium/Public)
- Scheduled posting at optimal times
- Engagement-optimized templates
"""

import os
import asyncio
import httpx
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# API Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "https://algobetsai.onrender.com")
API_KEY = os.getenv("BACKEND_API_KEY", "")

# Twitter Configuration (using Twitter API v2)
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

# Discord Configuration
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_PUBLIC_CHANNEL_ID = os.getenv("DISCORD_PUBLIC_CHANNEL_ID", "")
DISCORD_PREMIUM_CHANNEL_ID = os.getenv("DISCORD_PREMIUM_CHANNEL_ID", "")
DISCORD_VIP_CHANNEL_ID = os.getenv("DISCORD_VIP_CHANNEL_ID", "")

# Posting Configuration
TIMEZONE = os.getenv("APP_TIMEZONE", "America/New_York")
POST_TOP_N_PICKS = int(os.getenv("POST_TOP_N_PICKS", "3"))  # Post top 3 picks
MIN_EDGE_FOR_POSTING = float(os.getenv("MIN_EDGE_FOR_POSTING", "2.0"))  # Minimum 2% edge
MIN_CONFIDENCE_FOR_POSTING = float(os.getenv("MIN_CONFIDENCE_FOR_POSTING", "65.0"))  # Minimum 65% confidence


# ═══════════════════════════════════════════════════════════════════════════════
# PICK FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_picks() -> list[dict]:
    """Fetch picks from the AlgoBets API"""
    async with httpx.AsyncClient() as client:
        try:
            headers = {}
            if API_KEY:
                headers["Authorization"] = f"Bearer {API_KEY}"
            
            response = await client.get(
                f"{API_BASE_URL}/picks",
                headers=headers,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()
            return data.get("picks", [])
        except Exception as e:
            print(f"❌ Error fetching picks: {e}")
            return []


def filter_picks_for_posting(picks: list[dict]) -> list[dict]:
    """Filter picks based on quality thresholds"""
    filtered = []
    for pick in picks:
        ev = float(pick.get("ev", 0) or 0)
        confidence = float(pick.get("confidence", 0) or 0)
        
        if ev >= MIN_EDGE_FOR_POSTING and confidence >= MIN_CONFIDENCE_FOR_POSTING:
            filtered.append(pick)
    
    # Sort by edge (highest first)
    filtered.sort(key=lambda p: float(p.get("ev", 0) or 0), reverse=True)
    
    # Return top N
    return filtered[:POST_TOP_N_PICKS]


# ═══════════════════════════════════════════════════════════════════════════════
# TWITTER FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def format_pick_for_twitter(pick: dict, include_cta: bool = True) -> str:
    """
    Format a pick for Twitter (280 character limit)
    
    Example output:
    🔥 EDGE ALERT
    
    Lakers -5.5 (-110)
    vs Celtics
    
    📊 Edge: +3.2%
    🎯 Confidence: 72%
    ⚡ Sharp Money Detected
    
    #SportsBetting #NBA
    """
    bet = pick.get("bet", "")
    game = pick.get("game", "")
    odds = pick.get("odds", "")
    ev = float(pick.get("ev", 0) or 0)
    confidence = float(pick.get("confidence", 0) or 0)
    
    # Determine emoji based on edge strength
    if ev >= 5.0:
        emoji = "🔥🔥"
    elif ev >= 3.0:
        emoji = "🔥"
    else:
        emoji = "📊"
    
    # Build tweet
    lines = [
        f"{emoji} EDGE ALERT",
        "",
        f"{bet} {odds}",
    ]
    
    if game:
        lines.append(f"{game}")
    
    lines.extend([
        "",
        f"📊 Edge: +{ev:.1f}%",
        f"🎯 Confidence: {confidence:.0f}%",
    ])
    
    # Add special indicators
    if pick.get("public_pct"):
        public_pct = float(pick.get("public_pct", 0) or 0)
        if public_pct > 70 or public_pct < 30:
            lines.append(f"👥 Fading {public_pct:.0f}% Public")
    
    if pick.get("line_velocity"):
        velocity = float(pick.get("line_velocity", 0) or 0)
        if velocity > 3.0:
            lines.append("⚡ Sharp Money Detected")
    
    # Add hashtags
    sport = pick.get("sport", "").upper()
    if sport:
        lines.extend(["", f"#SportsBetting #{sport}"])
    
    tweet = "\n".join(lines)
    
    # Ensure under 280 characters
    if len(tweet) > 280:
        # Truncate game info if needed
        tweet = tweet[:277] + "..."
    
    return tweet


def format_multiple_picks_for_twitter(picks: list[dict]) -> str:
    """
    Format multiple picks into a single thread-worthy tweet
    
    Example:
    🔥 TODAY'S TOP EDGES
    
    1️⃣ Lakers -5.5 | +3.2% EV
    2️⃣ Chiefs ML | +2.8% EV  
    3️⃣ Over 225.5 | +2.1% EV
    
    All 65%+ confidence
    
    #SportsBetting
    """
    if not picks:
        return ""
    
    lines = ["🔥 TODAY'S TOP EDGES", ""]
    
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    
    for i, pick in enumerate(picks[:5]):
        bet = pick.get("bet", "")
        ev = float(pick.get("ev", 0) or 0)
        emoji = emojis[i] if i < len(emojis) else "▪️"
        lines.append(f"{emoji} {bet} | +{ev:.1f}% EV")
    
    lines.extend([
        "",
        f"All {MIN_CONFIDENCE_FOR_POSTING:.0f}%+ confidence",
        "",
        "#SportsBetting #BettingPicks"
    ])
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# DISCORD FORMATTING
# ═══════════════════════════════════════════════════════════════════════════════

def format_pick_for_discord(pick: dict, tier: str = "public") -> dict:
    """
    Format a pick for Discord using embeds
    
    Returns a Discord embed object
    """
    bet = pick.get("bet", "Unknown Bet")
    game = pick.get("game", "")
    odds = pick.get("odds", "")
    ev = float(pick.get("ev", 0) or 0)
    confidence = float(pick.get("confidence", 0) or 0)
    sport = pick.get("sport", "").upper()
    
    # Determine color based on edge
    if ev >= 5.0:
        color = 0x00E5A0  # Green (VIP color)
    elif ev >= 3.0:
        color = 0x00D4FF  # Cyan (Premium color)
    else:
        color = 0x3B82F6  # Blue
    
    # Build description
    description_parts = []
    
    if game:
        description_parts.append(f"**{game}**")
    
    description_parts.append(f"**{bet}** {odds}")
    description_parts.append("")
    description_parts.append(f"📊 **Edge:** +{ev:.1f}%")
    description_parts.append(f"🎯 **Confidence:** {confidence:.0f}%")
    
    # Add tier-specific details
    if tier in ["premium", "vip"]:
        if pick.get("ensemble_score"):
            score = float(pick.get("ensemble_score", 0) or 0)
            description_parts.append(f"🔥 **Ensemble Score:** {score:.0f}/100")
    
    if tier == "vip":
        if pick.get("public_pct"):
            public_pct = float(pick.get("public_pct", 0) or 0)
            description_parts.append(f"👥 **Public:** {public_pct:.0f}%")
        
        if pick.get("line_velocity"):
            velocity = float(pick.get("line_velocity", 0) or 0)
            if velocity > 3.0:
                description_parts.append(f"⚡ **Sharp Money:** {velocity:.1f} cents/min")
        
        if pick.get("timing_score"):
            timing = float(pick.get("timing_score", 0) or 0)
            description_parts.append(f"⏰ **Timing Score:** {timing:.0f}/100")
    
    embed = {
        "title": f"🔥 {sport} Edge Alert" if sport else "🔥 Edge Alert",
        "description": "\n".join(description_parts),
        "color": color,
        "footer": {
            "text": f"AlgoBets AI • {tier.upper()} Tier"
        },
        "timestamp": datetime.now(ZoneInfo(TIMEZONE)).isoformat()
    }
    
    return embed


# ═══════════════════════════════════════════════════════════════════════════════
# TWITTER POSTING
# ═══════════════════════════════════════════════════════════════════════════════

async def post_to_twitter(text: str) -> bool:
    """Post a tweet using Twitter API v2"""
    if not TWITTER_BEARER_TOKEN:
        print("⚠️  Twitter credentials not configured")
        return False
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.twitter.com/2/tweets",
                headers={
                    "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"text": text},
                timeout=30.0
            )
            response.raise_for_status()
            print(f"✅ Posted to Twitter: {text[:50]}...")
            return True
        except Exception as e:
            print(f"❌ Error posting to Twitter: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# DISCORD POSTING
# ═══════════════════════════════════════════════════════════════════════════════

async def post_to_discord(channel_id: str, embed: dict) -> bool:
    """Post an embed to a Discord channel"""
    if not DISCORD_BOT_TOKEN or not channel_id:
        print("⚠️  Discord credentials not configured")
        return False
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={
                    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"embeds": [embed]},
                timeout=30.0
            )
            response.raise_for_status()
            print(f"✅ Posted to Discord channel {channel_id}")
            return True
        except Exception as e:
            print(f"❌ Error posting to Discord: {e}")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN AUTOMATION LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

async def post_picks_to_social_media():
    """Main function to fetch picks and post to social media"""
    print(f"\n{'='*60}")
    print(f"🚀 AlgoBets AI Social Media Bot")
    print(f"{'='*60}")
    print(f"⏰ {datetime.now(ZoneInfo(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S %Z')}\n")
    
    # Fetch picks
    print("📥 Fetching picks from API...")
    picks = await fetch_picks()
    print(f"   Found {len(picks)} total picks")
    
    # Filter picks
    filtered_picks = filter_picks_for_posting(picks)
    print(f"   Filtered to {len(filtered_picks)} quality picks (EV≥{MIN_EDGE_FOR_POSTING}%, Conf≥{MIN_CONFIDENCE_FOR_POSTING}%)")
    
    if not filtered_picks:
        print("\n⚠️  No picks meet posting criteria. Skipping.")
        return
    
    # Post to Twitter
    print(f"\n📱 Posting to Twitter...")
    if len(filtered_picks) == 1:
        # Single pick - detailed format
        tweet = format_pick_for_twitter(filtered_picks[0])
        await post_to_twitter(tweet)
    else:
        # Multiple picks - summary format
        tweet = format_multiple_picks_for_twitter(filtered_picks)
        await post_to_twitter(tweet)
    
    # Post to Discord channels
    print(f"\n💬 Posting to Discord...")
    
    for pick in filtered_picks:
        ev = float(pick.get("ev", 0) or 0)
        
        # Public channel - only highest edge picks
        if ev >= 4.0 and DISCORD_PUBLIC_CHANNEL_ID:
            embed = format_pick_for_discord(pick, tier="public")
            await post_to_discord(DISCORD_PUBLIC_CHANNEL_ID, embed)
            await asyncio.sleep(1)  # Rate limit protection
        
        # Premium channel - good edge picks
        if ev >= 2.5 and DISCORD_PREMIUM_CHANNEL_ID:
            embed = format_pick_for_discord(pick, tier="premium")
            await post_to_discord(DISCORD_PREMIUM_CHANNEL_ID, embed)
            await asyncio.sleep(1)
        
        # VIP channel - all filtered picks with full details
        if DISCORD_VIP_CHANNEL_ID:
            embed = format_pick_for_discord(pick, tier="vip")
            await post_to_discord(DISCORD_VIP_CHANNEL_ID, embed)
            await asyncio.sleep(1)
    
    print(f"\n✅ Social media posting complete!")
    print(f"{'='*60}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test mode - show what would be posted without actually posting
        print("🧪 TEST MODE - No actual posting will occur\n")
        
        async def test_mode():
            picks = await fetch_picks()
            filtered = filter_picks_for_posting(picks)
            
            print(f"Found {len(filtered)} picks to post:\n")
            
            for i, pick in enumerate(filtered, 1):
                print(f"\n{'─'*60}")
                print(f"PICK #{i}")
                print(f"{'─'*60}")
                
                print("\n📱 TWITTER FORMAT:")
                print(format_pick_for_twitter(pick))
                
                print("\n💬 DISCORD FORMAT (VIP):")
                embed = format_pick_for_discord(pick, tier="vip")
                print(f"Title: {embed['title']}")
                print(f"Description:\n{embed['description']}")
        
        asyncio.run(test_mode())
    else:
        # Production mode - actually post
        asyncio.run(post_picks_to_social_media())

