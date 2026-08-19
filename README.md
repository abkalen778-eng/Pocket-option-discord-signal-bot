# Pocket Option Discord Signal Scanner

A Discord bot that scans real-market FX pairs and posts short-horizon CALL/PUT analysis signals.

It does not place trades.

## Discord destination

Configured for channel `1539566165846921226`.

## Required Railway variables

- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID=1539566165846921226`
- `TWELVE_DATA_API_KEY`
- `SCAN_INTERVAL_SECONDS=90`
- `MIN_SIGNAL_SCORE=74`
- `ALERT_COOLDOWN_MINUTES=12`
- `SYMBOLS=EUR/USD,GBP/USD,USD/JPY,AUD/USD,USD/CAD,EUR/JPY`

The setup score is a strategy score, not a guaranteed win probability. OTC pairs are intentionally excluded because an external market feed may not match Pocket Option OTC pricing.
