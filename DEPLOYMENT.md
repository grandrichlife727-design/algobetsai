# AlgoBets AI v2.0 - Deployment Guide

Complete step-by-step guide to deploying AlgoBets AI to production.

## Architecture Overview

```
Frontend (Next.js v2.0)
       ↓
Vercel / Docker / Self-Hosted
       ↓
    ↓HTTP↓
Backend (FastAPI v4.0)
       ↓
  Render / Docker
       ↓
SQLite + External APIs
(ESPN, Action Network, Pinnacle, Weather)
```

---

## Option 1: Vercel (Recommended)

Fastest, simplest deployment with zero ops.

### Prerequisites
- GitHub repository connected
- Vercel account (free or pro)
- Backend already deployed and accessible

### Steps

#### 1. Push to GitHub

```bash
git add .
git commit -m "feat: algobets v2.0 frontend"
git push origin main
```

#### 2. Create Vercel Project

Visit [vercel.com](https://vercel.com):
1. Click "New Project"
2. Select your GitHub repo
3. Click "Import"

#### 3. Configure Environment

In Vercel dashboard → Project Settings → Environment Variables:

```
NEXT_PUBLIC_API_URL = https://algobetsai.onrender.com
```

**Note**: `NEXT_PUBLIC_` prefix makes it available to browser. Keep sensitive keys server-side only.

#### 4. Deploy

```bash
# Automatic on git push, or manually via Vercel dashboard
git push origin main
```

**Result**: `https://your-project.vercel.app`

#### 5. Custom Domain (Optional)

Vercel Settings → Domains:
- Add your domain (e.g., `algobetsai.com`)
- Update DNS records as instructed
- Auto SSL certificate

#### 6. Monitor

Vercel Dashboard → Deployments:
- View build logs
- Rollback previous versions
- Environment variable changes

---

## Option 2: Docker + Render

Full control over deployment environment.

### Prerequisites
- Docker installed locally
- Render account (free or paid)
- Docker Hub account (for image registry)

### Local Testing

#### 1. Build Docker Image

```bash
docker build -t algobets-ai:latest .
```

#### 2. Run Locally

```bash
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=https://algobetsai.onrender.com \
  algobets-ai:latest
```

Visit `http://localhost:3000` and verify it works.

#### 3. Push to Docker Hub

```bash
# Login
docker login

# Tag image
docker tag algobets-ai:latest yourname/algobets-ai:latest

# Push
docker push yourname/algobets-ai:latest
```

### Deploy to Render

#### 1. Create Render Service

[render.com](https://render.com) → New → Web Service:
1. Select "Docker" as Runtime
2. Enter image URL: `yourname/algobets-ai:latest`
3. Set name: `algobets-ai-frontend`
4. Select plan (Starter $7/mo)

#### 2. Environment Variables

Environment tab → Add environment variables:

```
NEXT_PUBLIC_API_URL = https://algobetsai.onrender.com
NODE_ENV = production
```

#### 3. Deploy

```bash
# Automatic or manual via Render dashboard
# Takes ~3-5 minutes first time
```

**Result**: `https://algobets-ai-frontend.onrender.com`

#### 4. Custom Domain

Settings → Custom Domains:
- Add your domain
- Point CNAME to Render subdomain
- SSL auto-configured

---

## Option 3: Self-Hosted (VPS)

Full control, requires server ops knowledge.

### Prerequisites
- VPS (DigitalOcean, Linode, AWS EC2, etc.)
- SSH access to server
- Node.js 18+ installed
- PM2 for process management (recommended)
- Nginx for reverse proxy (optional)

### Setup Steps

#### 1. Provision Server

```bash
# On your VPS, update system
apt update && apt upgrade -y

# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
apt install -y nodejs

# Install pnpm (optional but recommended)
npm install -g pnpm pm2
```

#### 2. Clone & Install

```bash
# Clone repo
git clone <your-repo-url> /opt/algobets-ai
cd /opt/algobets-ai

# Install dependencies
pnpm install --frozen-lockfile
```

#### 3. Build Application

```bash
# Build Next.js app
pnpm build

# Verify build succeeded
ls -la .next/
```

#### 4. Configure Environment

```bash
# Create .env.production
cat > .env.production << EOF
NEXT_PUBLIC_API_URL=https://algobetsai.onrender.com
NODE_ENV=production
EOF
```

#### 5. Setup PM2

```bash
# Start app with PM2
pm2 start "pnpm start" --name algobets-ai

# Auto-restart on reboot
pm2 startup
pm2 save

# Monitor
pm2 status
pm2 logs algobets-ai
```

#### 6. Configure Nginx (Optional Reverse Proxy)

```bash
# Install nginx
apt install -y nginx

# Create config
cat > /etc/nginx/sites-available/algobets-ai << 'EOF'
server {
    listen 80;
    server_name algobetsai.com www.algobetsai.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
EOF

# Enable site
ln -s /etc/nginx/sites-available/algobets-ai /etc/nginx/sites-enabled/

# Test config
nginx -t

# Start nginx
systemctl restart nginx
```

#### 7. SSL Certificate (Let's Encrypt)

```bash
# Install certbot
apt install -y certbot python3-certbot-nginx

# Request certificate
certbot --nginx -d algobetsai.com -d www.algobetsai.com

# Auto-renewal (automatic with certbot)
systemctl enable certbot.timer
```

#### 8. Keep Updated

```bash
# Create update script
cat > /opt/update.sh << 'EOF'
#!/bin/bash
cd /opt/algobets-ai
git pull origin main
pnpm install --frozen-lockfile
pnpm build
pm2 restart algobets-ai
EOF

chmod +x /opt/update.sh

# Add to crontab for daily updates
crontab -e
# Add: 0 2 * * * /opt/update.sh
```

---

## Monitoring & Maintenance

### Health Checks

#### Vercel
- Dashboard → Deployments → Status
- Real User Monitoring (RUM) available on Pro plan

#### Render
- Dashboard → Metrics
- Auto-restart on failure
- Logs available in UI

#### Self-Hosted
```bash
# Check service status
pm2 status

# View logs
pm2 logs algobets-ai

# Monitor CPU/memory
pm2 monit
```

### Performance Monitoring

#### Vercel Analytics
```javascript
// Built-in, no setup needed
// Dashboard → Analytics
```

#### Self-Hosted with New Relic (Optional)
```bash
# Install
npm install newrelic

# Configure in server.js
require('newrelic');
```

### Uptime Monitoring

Use third-party services:
- **UptimeRobot** (free)
- **Pingdom** (paid)
- **Datadog** (enterprise)

```bash
# Example UptimeRobot webhook
Monitor URL: https://algobetsai.com/health
Check interval: 5 minutes
```

---

## Debugging Issues

### 500 Errors

```bash
# Check server logs
# Vercel: Dashboard → Function logs
# Render: Dashboard → Logs
# Self-hosted: pm2 logs algobets-ai

# Check API connectivity
curl https://algobetsai.onrender.com/health

# Verify environment variables are set
# NEXT_PUBLIC_API_URL must be accessible from browser
```

### Slow Performance

```bash
# Check build size
npm run build
# Analyze: npm run analyze (with @next/bundle-analyzer)

# Check database queries
# Backend logs for slow responses

# CDN caching
# Vercel handles automatically
# Self-hosted: Configure nginx caching
```

### API Connection Failures

```bash
# Test backend connectivity
curl -v https://algobetsai.onrender.com/health

# Check CORS headers
# Response should include Access-Control-Allow-Origin

# Check browser console for CORS errors
# If issue: verify backend allows frontend domain
```

### Memory Issues

```bash
# Self-hosted: Monitor RAM
free -h

# Increase Node.js memory if needed
pm2 start "node --max-old-space-size=2048 server.js" --name algobets-ai

# Check for memory leaks
# Use Node.js profiler or clinic.js
```

---

## Scaling

### Vercel
- Scales automatically
- Serverless: pay per request
- No configuration needed

### Render
- **Free tier**: Limited resources, auto-pauses
- **Starter**: $7/month, always running
- **Standard**: $12/month, 1GB RAM
- Scale by upgrading plan

### Self-Hosted
- Horizontal scaling: Load balance multiple instances
- Nginx upstream:
```nginx
upstream algobets_backend {
    server localhost:3000;
    server localhost:3001;
    server localhost:3002;
}
```

---

## Backup & Recovery

### Data Backup

```bash
# Self-hosted: SQLite backup
cp /data/picks.db /backup/picks.db.$(date +%s)

# Automated daily backup
0 3 * * * cp /data/picks.db /backup/picks.db.$(date +%s) && \
           find /backup -name "picks.db.*" -mtime +30 -delete
```

### Code Backup

```bash
# GitHub is your backup
# All code is version controlled

# Optional: Backup .env files (ENCRYPTED)
# Never commit secrets!
```

### Disaster Recovery

```bash
# Verify backup works
tar -tzf backup.tar.gz

# Test restore on staging environment
# Before restoring to production
```

---

## Security Checklist

- [ ] API keys stored only in environment variables
- [ ] `.env.local` added to `.gitignore`
- [ ] HTTPS enabled on all domains
- [ ] CORS configured correctly on backend
- [ ] Rate limiting enabled on backend
- [ ] DDoS protection active (Vercel has this)
- [ ] Regular security updates (`npm audit`)
- [ ] Backend API protected with API keys
- [ ] User data encrypted in transit (HTTPS)
- [ ] Database backups encrypted

---

## Rollback Procedure

### Vercel
1. Dashboard → Deployments
2. Click previous version
3. Click "Promote to Production"
4. Done (instant)

### Render
1. Dashboard → Deployments
2. Select previous deployment
3. Click "Redeploy"
4. Wait for deployment to complete

### Self-Hosted
```bash
# Using git
git revert HEAD
git push origin main
pm2 restart algobets-ai

# Or restore from PM2 logs
pm2 save  # Current state
pm2 delete algobets-ai
pm2 start "pnpm start" --name algobets-ai
```

---

## Cost Summary

| Hosting | Cost | Best For |
|---------|------|----------|
| **Vercel** | Free-$20/mo | Best performance, team projects |
| **Render** | $7-50/mo | Simple deployment, side projects |
| **Self-Hosted** | $5-50/mo | Full control, large scale |
| **AWS/GCP/Azure** | Variable | Enterprise, custom needs |

---

## Post-Deployment Checklist

- [ ] Test all pages load correctly
- [ ] API connectivity verified
- [ ] Mock data fallback working
- [ ] All charts rendering
- [ ] Responsive design tested on mobile
- [ ] Dark theme looks good
- [ ] Performance metrics acceptable
- [ ] Error boundaries working
- [ ] Analytics tracking (if enabled)
- [ ] Team notified of production URL
- [ ] Status page bookmarked
- [ ] Monitoring/alerts configured

---

## Support

**Having issues?**
1. Check logs: Vercel/Render dashboard or PM2
2. Test API: `curl https://algobetsai.onrender.com/health`
3. Check `.env`: `NEXT_PUBLIC_API_URL` must be valid
4. Clear browser cache: Hard refresh (Cmd+Shift+R)
5. Inspect browser console: Network errors?

**Still stuck?**
- Check FRONTEND.md troubleshooting section
- Review backend README.md for API issues
- Open GitHub issue with error details

---

**Last Updated**: March 2026  
**Version**: v2.0.0  
**Status**: Production Ready
