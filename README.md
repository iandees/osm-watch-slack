# osm-watch-slack

Slack bot that watches OpenStreetMap changes and sends notifications. Register watches using a compact text DSL, and get notified when matching changes appear in the minutely augmented diffs.

## DSL

```
<type>[<id>]?[<tag-filter>]*(new|changed|deleted)?(bbox:s,w,n,e)?
```

At least one of element ID, tag filter, or bbox is required.

### Examples

| Watch | What it does |
|---|---|
| `relation(12345)[name]` | Any name change on relation 12345 |
| `node[amenity=hospital](new)(bbox:40.7,-74.0,40.8,-73.9)` | New hospitals in a bounding box |
| `way[highway](deleted)` | Any highway deletion globally |
| `node[amenity=restaurant][cuisine=pizza]` | Pizza restaurant changes |

### Slash commands

```
/osmwatch <filter> [expires:<duration>]   Create a watch (default: 1 week)
/osmwatch list                            List active watches in this channel
/osmwatch cancel <id>                     Cancel a watch you created
/osmwatch help                            Show DSL syntax help
```

Duration suffixes: `m` (minutes), `h` (hours), `d` (days), `w` (weeks). Max 6 months.

## Setup

### 1. Create a Slack app

- Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
- Enable **Socket Mode** (Settings → Socket Mode) and save the app-level token (`xapp-...`)
- Add slash command `/osmwatch` (Features → Slash Commands)
- Enable **Interactivity** (Features → Interactivity & Shortcuts)
- Add bot scopes `commands` and `chat:write` (Features → OAuth & Permissions)
- Install to your workspace and save the bot token (`xoxb-...`)

### 2. Configure

```bash
cp .env.example .env
```

Fill in `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN`.

### 3. Run with Docker

```bash
docker compose up --build
```

Or pull the pre-built image:

```yaml
# docker-compose.yml
services:
  osm-watch-slack:
    image: ghcr.io/iandees/osm-watch-slack:main
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
```

### 4. Invite the bot

In any channel where you want notifications: `/invite @OSM Watch`

## Configuration

| Variable | Default | Description |
|---|---|---|
| `SLACK_BOT_TOKEN` | (required) | Bot user OAuth token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | (required) | App-level token for Socket Mode (`xapp-...`) |
| `DATABASE_PATH` | `data/watches.db` | SQLite database path |
| `STATE_PATH` | `data/state.txt` | Replication sequence state file |
| `OVERPASS_BASE_URL` | `https://overpass-api.de` | Overpass API base URL |
| `LOG_LEVEL` | `INFO` | Logging level |
| `USER_WATCH_CAP` | `20` | Max active watches per user |
| `CHANNEL_WATCH_CAP` | `50` | Max active watches per channel |
| `DIGEST_THRESHOLD` | `5` | Messages per watch per minute before digest mode |

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check src/ tests/
```
