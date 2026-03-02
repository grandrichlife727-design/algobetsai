# AlgoBets AI v2.0 - Navigation & Sitemap

Visual guide to all pages, features, and how to navigate the app.

## Site Structure

```
AlgoBets AI Root
│
├── 📊 Dashboard (/)
│   ├── Overview Stats (4 metrics)
│   ├── Top 5 Picks (by confidence)
│   ├── Trending Bets (sidebar)
│   └── Sport Performance (4-sport grid)
│
├── 🎯 Top Picks (/picks)
│   ├── Search Bar (team/event name)
│   ├── Filters
│   │   ├── Status (Pending/Won/Lost)
│   │   ├── Sport (NFL/NBA/MLB/NHL/CFB/CBB)
│   │   └── Sort By (Confidence/Edge/Kelly)
│   └── Pick Cards (clickable for details)
│
├── 🔔 Alerts (/alerts)
│   ├── Alert Filters
│   │   ├── All Alerts
│   │   ├── Unread Only
│   │   ├── Steam Moves
│   │   └── Reverse Line Moves
│   └── Alert List (by timestamp)
│
├── 🎲 Parlay Builder (/parlay)
│   ├── Pick Selection (left side)
│   │   └── Available Picks list
│   ├── Selected Legs (middle)
│   └── Parlay Summary (right sidebar)
│       ├── Legs count
│       ├── Combined odds
│       ├── Avg confidence
│       ├── Correlation risk
│       ├── Stake amount input
│       ├── Potential profit
│       └── Kelly recommendation
│
├── 📈 Analytics (/analytics)
│   ├── Key Metrics (4 cards)
│   │   ├── Win Rate %
│   │   ├── ROI %
│   │   ├── Sharpe Ratio
│   │   └── CLV Average
│   ├── Charts
│   │   ├── Performance by Sport (bar)
│   │   ├── Confidence Distribution (pie)
│   │   └── Win/Loss by Sport (table)
│   └── Export & Reset Buttons
│
└── ⚙️ Sharp Tools (/sharp)
    ├── Model Configuration (left)
    │   ├── Signal Weights (sliders)
    │   │   ├── CLV Edge (25%)
    │   │   ├── Sharp Money (20%)
    │   │   ├── Line Movement (20%)
    │   │   ├── Consensus (15%)
    │   │   ├── Odds Quality (10%)
    │   │   └── Injury News (10%)
    │   ├── Total Weight Indicator
    │   └── Save Weights Button
    │
    ├── Devig Methods (left)
    │   ├── Standard Vig
    │   ├── Power Vig
    │   ├── Weighted Power Order
    │   └── Bettor Remaining
    │
    └── Sidebar Tools (right)
        ├── API Documentation
        ├── Model Metrics
        ├── User Leaderboard
        └── Opt-in Toggle
```

---

## Navigation Flow

### From Dashboard
```
Dashboard (home)
    ↓
[View all picks] → Top Picks page
    ↓
[Click pick] → Pick Details modal
    ↓
[View Details button] → Modal with breakdown
```

### Building a Parlay
```
Dashboard / Top Picks
    ↓
[Go to Parlay Builder]
    ↓
[Select picks] → Added to legs
    ↓
[Adjust stake] → Calculates payout
    ↓
[Place Parlay] → Confirmation
```

### Tracking Performance
```
Analytics page
    ↓
[View charts] → See visual breakdown
    ↓
[Filter by sport] → Sport-specific stats
    ↓
[Export data] → CSV/JSON download
```

### Customizing Model
```
Sharp Tools
    ↓
[Adjust signal weights] → Real-time total
    ↓
[Choose devig method] → Apply to predictions
    ↓
[Save weights] → Persisted for next session
```

---

## Key Pages Explained

### 📊 Dashboard
**Purpose**: Daily overview and quick insights
**Key Elements**:
- 4 stat cards (total picks, elite picks, avg confidence, ROI)
- Top 5 picks ranked by confidence
- Trending bets sidebar (highest edge picks)
- Sport performance grid (4 major sports)

**When to Use**: 
- Start your day here
- Quick check of today's best picks
- See performance trends

**Call to Action**:
- "View all" → Go to Top Picks page
- "View Details" → Open pick breakdown
- Sport boxes → Click for sport-specific stats

---

### 🎯 Top Picks
**Purpose**: Browse all predictions with advanced filtering
**Key Elements**:
- Search bar (find specific team/event)
- Status filters (Pending/Won/Lost)
- Sport filters (6 sports available)
- Sort options (Confidence/Edge/Kelly)
- Individual pick cards with confidence gauge

**When to Use**:
- Find picks for specific sport
- Search for a team
- Browse by confidence level
- Sort by your preferred metric

**Card Details** (click to expand):
- Circular confidence gauge (visual)
- Sport emoji + pick name
- Event details
- Current odds
- Implied probability
- Edge percentage
- ¼ Kelly recommendation
- Signal tags (what drove the pick)

**Click "View Details"** to see:
- Full signal breakdown (pie/bars)
- Multi-book odds comparison
- Kelly sizing recommendations
- Historical note/reason

---

### 🔔 Alerts
**Purpose**: Monitor betting market movements
**Types of Alerts**:
1. **Steam Moves** 💨 - Sharp money all one direction
2. **Reverse Line Moves** 📉 - Sharp vs public money
3. **Odds Boosts** ⚡ - Sportsbook promotions
4. **Line Changes** 📈 - Significant movement
5. **Sharp Signals** - Consensus from pros

**When to Use**:
- Find arbitrage opportunities
- Catch steam before odds move
- Get alerts on your picks
- Monitor sharp action flow

**Filters**:
- All Alerts (everything)
- Unread (recent alerts)
- Steam Moves only
- Reverse Line only

**Alert Details**:
- Sport + event
- Percent change (odds movement)
- Time of alert
- Description of what happened
- Unread indicator (blue dot)

---

### 🎲 Parlay Builder
**Purpose**: Combine picks with correlation intelligence
**Left Side** - Pick Selection:
- All available picks shown
- Select 2+ to build parlay
- Shows confidence & odds for each

**Middle** - Selected Legs:
- Each selected pick displayed
- Can remove picks here
- Shows odds for each leg

**Right Sidebar** - Summary:
- Legs count
- Combined odds multiplier
- Average confidence
- Correlation risk (low/medium/high)
- Stake amount input slider
- **Potential Profit** display (highlighted)
- **Kelly Recommendation** (how much to wager)

**When to Use**:
- Combine high-confidence picks
- Check correlation risk before wagering
- Size stakes with Kelly criterion
- See expected payout before placing

**Correlation Risk Meaning**:
- **Low** (green) - Picks from different sports/leagues
- **Medium** (yellow) - Some picks from same sport
- **High** (red) - Many picks from same sport (risky)

---

### 📈 Analytics
**Purpose**: Comprehensive performance review
**Top Metrics** (4 cards):
1. **Win Rate %** - Percentage of winning picks
2. **ROI %** - Return on Investment
3. **Sharpe Ratio** - Risk-adjusted performance
4. **CLV Average** - Closing Line Value (edge quality)

**Charts**:
1. **Performance by Sport** (bar chart)
   - Shows wins vs losses per sport
   - Compare which sports you're best in

2. **Confidence Distribution** (pie chart)
   - How picks spread across confidence buckets
   - See if you're picking too high/low confidence

3. **Detailed Sport Table**
   - All sports with complete stats
   - Win count, loss count, win rate %
   - Click to sort by any column

**When to Use**:
- Review monthly/quarterly performance
- Identify best and worst sports
- Check confidence calibration
- Export data for external analysis

**Export Data**:
- CSV format (open in Excel)
- Includes all picks with results
- Use for personal tracking

---

### ⚙️ Sharp Tools
**Purpose**: Professional customization and monitoring
**Left Side - Model Configuration**:
1. **Signal Weights** (adjust importance)
   - CLV Edge: Closing Line Value importance (25%)
   - Sharp Money: Professional action weight (20%)
   - Line Movement: Odds shift significance (20%)
   - Consensus: Multiple sources agreement (15%)
   - Odds Quality: Best odds availability (10%)
   - Injury News: Real-time event impact (10%)
   - **Total must equal 100%**

2. **Devig Methods** (choose odds calculation)
   - **Standard Vig**: Even vig removal
   - **Power Vig**: Proportional by odds
   - **Weighted Power Order**: Advanced algorithm
   - **Bettor Remaining**: Based on volume

**Right Side - Professional Features**:
1. **API Documentation**
   - Endpoints for integration
   - Authentication details
   - Rate limits

2. **Model Metrics**
   - Accuracy percentage
   - Precision (of picks)
   - Recall rate
   - F1 Score

3. **Leaderboard**
   - Top 3 users
   - Win rate comparison
   - Opt-in toggle to show your stats

**When to Use**:
- Customize prediction model
- Test different signal weights
- Integrate via API
- Compare with other users
- Advanced analytics

---

## Keyboard Shortcuts & Tips

### Navigation
- **Sidebar visible?** Click hamburger icon (mobile) or always visible (desktop)
- **Back to home?** Click "AlgoBets" logo in sidebar header
- **Close modal?** Press ESC or click outside
- **Search?** Cmd/Ctrl + F works in Top Picks page

### Filtering
- **Quick filter**: Use sport buttons - click multiple to combine
- **Clear filters**: Reload page or click "All" status
- **Reset sorts**: Reload page to reset to default

### Mobile Tips
- **Sidebar**: Swipe right to open (mobile)
- **Cards**: Tap to expand details
- **Charts**: Pinch to zoom on analytics
- **Alerts**: Swipe left to dismiss read alerts

---

## Information Architecture

### Data Hierarchy

**Highest Level**: Dashboard overview
↓
**Mid Level**: Detailed pages (Picks, Alerts, Analytics)
↓
**Detailed Level**: Modal breakdowns (click a pick)
↓
**Lowest Level**: Individual signal/algorithm details

### Navigation Paths

**Quick Access Path**:
Home → Top Picks → Pick Details → Place Bet

**Analysis Path**:
Home → Analytics → View Sport Stats → Export Data

**Builder Path**:
Home → Parlay Builder → Select Legs → Kelly Sizing → Place Parlay

**Customization Path**:
Home → Sharp Tools → Adjust Weights → Apply Model → Test Results

---

## Responsive Design

### Mobile (< 640px)
- Single column layout
- Sidebar slides in from left
- Full-width cards
- Stacked stat cards
- Charts responsive to screen width

### Tablet (640px - 1024px)
- Sidebar visible on larger tablets
- 2-column layouts possible
- Medium card sizes
- Charts with legends

### Desktop (> 1024px)
- Sidebar always visible (left)
- Main content flows right
- Multi-column grids
- Full feature visibility
- Hover states on interactive elements

---

## Color Coding Guide

### Status Indicators
- 🟢 **Green (Success)**: Won bets, positive ROI, low risk
- 🟡 **Yellow (Warning)**: Pending bets, medium confidence, medium risk
- 🔴 **Red (Danger)**: Lost bets, high risk, needs attention
- 🔵 **Cyan (Primary)**: Interactive elements, calls to action
- ⚫ **Slate (Muted)**: Disabled, secondary, borders

### Signal Colors
- **80%+**: Green (strong signal)
- **70-79%**: Cyan (good signal)
- **60-69%**: Yellow (moderate signal)
- **<60%**: Red (weak signal)

---

## Getting Help

### From Each Page

**Dashboard**: 
- "View all" → See more picks
- "View Details" → Deep dive on pick

**Top Picks**:
- Use filters for what you need
- Search for specific teams
- Click any pick for breakdown

**Alerts**:
- Filter by alert type
- Click to mark as read
- Review past alerts

**Parlay Builder**:
- Scroll for available picks
- Click to select/deselect
- Right sidebar shows calculation

**Analytics**:
- Hover charts for values
- Click sport in table for sorting
- "Export" saves your data

**Sharp Tools**:
- Sliders show current weight
- Total updates in real-time
- Can't save if total ≠ 100%

---

## Feature Quick Reference

| Need | Go To | Look For |
|------|-------|----------|
| Overview | Dashboard | 4 metric cards |
| All picks | Top Picks | Searchable list |
| Market moves | Alerts | Alert list |
| Combine picks | Parlay Builder | Leg selector |
| Your stats | Analytics | Charts + table |
| Customize | Sharp Tools | Weight sliders |
| Navigate | Sidebar | Link buttons |

---

**Last Updated**: March 2026  
**Version**: 2.0.0  
**Status**: Production Ready
