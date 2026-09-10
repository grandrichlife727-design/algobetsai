"""
AlgoBets AI - Social Media Scheduler
=====================================
Runs the social media bot at optimal times throughout the day

Recommended Schedule:
- Morning: 9:00 AM ET (before work)
- Lunch: 12:00 PM ET (lunch break)
- Evening: 6:00 PM ET (after work)
- Night: 9:00 PM ET (prime betting time)
"""

import asyncio
import schedule
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from social_media_bot import post_picks_to_social_media

TIMEZONE = "America/New_York"


def job():
    """Wrapper to run async function in sync context"""
    print(f"\n⏰ Scheduled job triggered at {datetime.now(ZoneInfo(TIMEZONE)).strftime('%H:%M:%S %Z')}")
    asyncio.run(post_picks_to_social_media())


def main():
    """Set up schedule and run continuously"""
    print("🤖 AlgoBets AI Social Media Scheduler Starting...")
    print(f"⏰ Timezone: {TIMEZONE}")
    print("\n📅 Posting Schedule:")
    print("   • 9:00 AM ET  - Morning picks")
    print("   • 12:00 PM ET - Lunch update")
    print("   • 6:00 PM ET  - Evening picks")
    print("   • 9:00 PM ET  - Prime time update")
    print("\n" + "="*60 + "\n")
    
    # Schedule posts at optimal times
    schedule.every().day.at("09:00").do(job)  # Morning
    schedule.every().day.at("12:00").do(job)  # Lunch
    schedule.every().day.at("18:00").do(job)  # Evening
    schedule.every().day.at("21:00").do(job)  # Night
    
    # Run immediately on startup
    print("🚀 Running initial post...")
    job()
    
    # Keep running
    print("\n✅ Scheduler is now running. Press Ctrl+C to stop.\n")
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Scheduler stopped by user")
    except Exception as e:
        print(f"\n❌ Scheduler error: {e}")

