# Booking Platform — FastAPI Backend

## Project Structure

```
booking_platform/
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── endpoints/      ← one file per feature (auth, agents, appointments...)
│   ├── core/
│   │   ├── config.py           ← all settings from .env
│   │   ├── security.py         ← JWT + password hashing
│   │   ├── logging.py          ← loguru setup
│   │   └── exceptions.py       ← custom errors + handlers
│   ├── db/
│   │   └── session.py          ← database connection + get_db()
│   ├── models/                 ← SQLAlchemy ORM models (your tables)
│   ├── schemas/                ← Pydantic schemas (request/response shapes)
│   ├── services/               ← business logic (one file per feature)
│   ├── utils/                  ← shared helpers
│   └── main.py                 ← FastAPI app factory
├── tests/                      ← pytest tests
├── logs/                       ← log files (auto-created)
├── scripts/                    ← seed scripts, migrations
├── run.py                      ← development server entry point
├── nginx.conf                  ← copy to /etc/nginx/sites-available/
├── booking_platform.service    ← copy to /etc/systemd/system/
├── requirements.txt
├── .env.example                ← copy to .env and fill in values
└── .gitignore
```

---

## Local Development Setup

```bash
# 1. Clone the repo
git clone git@github.com:youruser/booking_platform.git
cd booking_platform

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env with your local database URL and keys

# 5. Run the development server
python run.py
# API available at http://localhost:8000
# Docs available at http://localhost:8000/docs
```

---

## VPS Production Setup

```bash
# 1. SSH into your VPS
ssh youruser@your-vps-ip

# 2. Install system dependencies
sudo apt update && sudo apt install -y python3 python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx

# 3. Clone the repo
git clone git@github.com:youruser/booking_platform.git
cd booking_platform

# 4. Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Set up .env
cp .env.example .env
nano .env                       # fill in production values, set DEBUG=False

# 6. Set up Nginx
sudo cp nginx.conf /etc/nginx/sites-available/booking_platform
sudo ln -s /etc/nginx/sites-available/booking_platform /etc/nginx/sites-enabled/
# Edit the file to replace api.yourdomain.com with your actual domain
sudo nginx -t
sudo systemctl reload nginx

# 7. Get SSL certificate
sudo certbot --nginx -d api.yourdomain.com

# 8. Set up systemd service
sudo cp booking_platform.service /etc/systemd/system/
# Edit to replace youruser and path
sudo systemctl daemon-reload
sudo systemctl start booking_platform
sudo systemctl enable booking_platform

# 9. Check it's running
sudo systemctl status booking_platform
curl https://api.yourdomain.com/health
```

---

## Deploying Updates

```bash
# On your VPS
cd booking_platform
git pull
source venv/bin/activate
pip install -r requirements.txt   # only needed if requirements changed
sudo systemctl restart booking_platform
```

---

## Useful Commands

```bash
# View live logs
sudo journalctl -u booking_platform -f

# Restart the service
sudo systemctl restart booking_platform

# Check nginx logs
sudo tail -f /var/log/nginx/error.log

# Connect to database
psql -U postgres -d booking_platform
```
