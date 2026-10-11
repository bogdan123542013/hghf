MEGABOT — PUBLIC DEPLOY

1. Upload these files to a Python hosting service:
   megabot.py
   requirements.txt
   render.yaml

2. Set environment variables:
   BOT_TOKEN = your Telegram bot token
   OWNER_ID = your numeric Telegram ID
   PUBLIC_URL = the public https://... address after deployment

3. The website listens on 0.0.0.0 and uses the hosting PORT.

IMPORTANT:
This package is prepared for public hosting. Do not put your bot token into public GitHub repositories.
The web service and Telegram polling are better run as separate services for reliable 24/7 operation.
