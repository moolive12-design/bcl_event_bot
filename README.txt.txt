BCL Event Bot - quick deploy (Render)

Files
- main.py
- events.json
- requirements.txt
- render.yaml (optional)

Steps to deploy on Render
1) Create a GitHub repo and add these files (or upload zip).
2) Sign in to httpsrender.com and choose New - Web Service.
3) Connect your GitHub repo and pick branch.
4) In Environment settings
   - Set Environment to Python.
   - Build Command pip install -r requirements.txt
   - Start Command python main.py
5) Set Environment Variables (on Render dashboard - Environment)
   - BOT_TOKEN = your_bot_token
   - CHAT_ID = -1003207645424
   - THREAD_ID = 10
   - PUBLIC_URL = httpsyour-render-name.onrender.com  (recommended)
6) Deploy. Watch logs — you should see Starting bot main and Flask keep-alive started.
7) Configure UptimeRobot only if you want external monitoring; not required on Render.

Notes
- events.json supports addingremoving events; bot will auto-detect file changes and reload schedule.
- times are in UTC. days use three-letter English abbreviations Mon, Tue, Wed, Thu, Fri, Sat, Sun.
- Adjust WINDOW_MINUTES or SELF_PING_INTERVAL via environment variables if needed.
