# Auction Analyzer

Dutch vehicle & goods auction analysis tool with scraping, price prediction, and a Streamlit dashboard.

## Quick Start (VPS Setup)

```bash
# 1. Clone the repo
git clone https://github.com/amirhshad/auction-analyzer.git
cd auction-analyzer

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers (needed for scraping)
playwright install --with-deps chromium

# 5. Create the data directory and configure environment
mkdir -p data
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY (required for AI features)

# 6. Run the dashboard
python main.py dashboard
```

The dashboard will be available at `http://your-vps-ip:8501`.

## Commands

| Command | Description |
|---|---|
| `python main.py dashboard` | Launch Streamlit dashboard |
| `python main.py scrape --pages 2 --lots 20` | Scrape Troostwijk auctions |
| `python main.py scrape-ovm --url <url>` | Scrape OnlineVeilingmeester |
| `python main.py scrape-goods --url <url>` | Scrape goods auctions |
| `python main.py market --make BMW --model "3-serie"` | Fetch market prices |
| `python main.py list --limit 50` | List vehicles in database |
| `python main.py analyze --id 1` | Full analysis on a vehicle |
| `python main.py analyze-images --id 1` | AI image analysis |

## Environment Variables

See [.env.example](.env.example) for all configuration options. Only `OPENAI_API_KEY` is required for AI-powered features; everything else has sensible defaults.

## Running as a Background Service

To keep the dashboard running after you disconnect from the VPS:

```bash
# Option A: nohup
nohup python main.py dashboard &

# Option B: systemd service
sudo tee /etc/systemd/system/auction-analyzer.service << 'EOF'
[Unit]
Description=Auction Analyzer Dashboard
After=network.target

[Service]
User=your-username
WorkingDirectory=/path/to/auction-analyzer
ExecStart=/path/to/auction-analyzer/.venv/bin/python main.py dashboard
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now auction-analyzer
```
