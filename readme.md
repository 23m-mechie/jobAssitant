# apply.ai — Job Application Assistant

A local web app that reads job description screenshots, drafts tailored cold emails, sends them via Gmail with your resume attached, and answers Google Form application questions — all powered by Gemini AI.

---

## What it does

| Tab | What happens |
|---|---|
| **Profile** | Store your experience, projects, cover letter + upload resume PDF once |
| **New Application** | Upload JD screenshots → AI extracts details → drafts email → you approve → Gmail sends it with resume attached |
| **Form Q&A** | Paste Google Form questions → AI answers them using your profile → copy and fill |

---

## Prerequisites

Before you start, make sure you have:

- Python 3.11+
- A [Google AI Studio](https://aistudio.google.com) account (for Gemini API key)
- A Google account (for Gmail sending)
- A [Google Cloud Console](https://console.cloud.google.com) account (free, for Gmail OAuth)

---

## Step 1 — Get your Gemini API Key

1. Go to [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Click **Create API Key**
3. Copy the key — you'll need it in Step 4

---

## Step 2 — Set up Gmail OAuth (Google Cloud Console)

This allows the app to send emails from your Gmail account.

### 2a. Create a project
1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Click the project dropdown (top left) → **New Project**
3. Name it anything (e.g. `job-assistant`) → **Create**

### 2b. Enable the Gmail API
1. In your new project, go to **APIs & Services → Library**
2. Search for **Gmail API** → click it → click **Enable**

### 2c. Configure OAuth consent screen
1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** → **Create**
3. Fill in:
   - App name: `Job Assistant`
   - User support email: your Gmail
   - Developer contact: your Gmail
4. Click **Save and Continue** through all steps (no need to add scopes manually here)
5. On the final screen, click **Back to Dashboard**
6. Click **Publish App** → **Confirm**
   > ⚠️ If you skip publishing, you'll hit a "This app is blocked" error during Gmail login

### 2d. Create OAuth credentials
1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Application type: **Desktop app**
4. Name: `Job Assistant Desktop`
5. Click **Create**
6. Click **Download JSON** on the popup
7. Rename the downloaded file to `credentials.json`
8. Place it in the root of the project folder (same level as `main.py`)

---

## Step 3 — Install dependencies

```bash
# Clone or copy the project folder, then:
cd job-assistant

# Create a virtual environment (recommended)
python -m venv venv

# Activate it
# On Mac/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## Step 4 — Configure environment variables

Open the `.env` file in the project root and fill in your Gemini API key:

```
GEMINI_API_KEY=your_gemini_api_key_here
```

Save the file.

---

## Step 5 — Run the app

```bash
uvicorn main:app --reload
```

Then open your browser and go to:

```
http://localhost:8000
```

---

## Step 6 — First-time setup in the app

### 6a. Connect Gmail
1. Go to the **Profile** tab
2. Scroll to **Gmail Connection**
3. Click **Connect** — your browser will open a Google login popup
4. Sign in and grant permission
5. The app saves a `token.json` file locally — you won't need to log in again

### 6b. Fill your profile
1. Paste your **Work Experience**, **Projects**, and **Cover Letter** in the textareas
2. Upload your **Resume PDF**
3. Click **Save Profile**

That's it — you're ready to send applications.

---

## How to use

### Sending a job application
1. Take 1–3 screenshots of a LinkedIn/Indeed/any job posting
2. Go to **New Application** tab
3. Upload the screenshots
4. Click **Extract Details with AI** — Gemini reads the JD and fills in job title, company, requirements, and email (if found)
5. Review and correct the extracted fields
6. Click **Next: Generate Draft** — Gemini writes a tailored cold email
7. Review and edit the draft
8. Confirm the recipient email
9. Click **Send Application** — Gmail sends it with your resume attached

### Answering a Google Form
1. Copy-paste the form questions as plain text
2. Go to **Form Q&A** tab
3. Paste the questions and click **Generate Answers**
4. Review each answer → click **Copy** → paste into the form

---

## Project structure

```
job-assistant/
├── main.py              # FastAPI backend — all API routes
├── requirements.txt     # Python dependencies
├── dockerfile           # For deployment
├── .env                 # Your Gemini API key (never commit this)
├── credentials.json     # Gmail OAuth client secret (never commit this)
├── token.json           # Gmail auth token — auto-created on first login (never commit this)
├── .gitignore           # Excludes all sensitive files
├── data/
│   ├── profile.json     # Your saved profile — auto-created on first save
│   └── resume.pdf       # Your uploaded resume — auto-created on first upload
└── static/
    └── index.html       # Full frontend — single page, 3 tabs
```

---

## Sensitive files — never commit these

| File | Why |
|---|---|
| `.env` | Contains your Gemini API key |
| `credentials.json` | Gmail OAuth client secret |
| `token.json` | Your Gmail access token |
| `data/profile.json` | Your personal career info |
| `data/resume.pdf` | Your resume |

All of these are already in `.gitignore`.

---

## Known issues / things to fix before v1

- `main.py` line 43 has a stray `w` character — delete it or the app will crash
- Model name `gemini-3.5-flash` does not exist — replace with `gemini-2.0-flash` or `gemini-2.5-flash` in `main.py`
- Local file storage (`data/`) won't persist on serverless platforms like Cloud Run — migrate to Cloud Storage before deploying

---

## Troubleshooting

**"This app is blocked" during Gmail login**
→ Go to Google Cloud Console → OAuth consent screen → Publish the app

**"credentials.json missing" error**
→ Download OAuth credentials from Google Cloud Console and place in project root (see Step 2d)

**App crashes on startup**
→ Check for the stray `w` on line 43 of `main.py` and delete it

**Gemini API errors**
→ Verify your `GEMINI_API_KEY` in `.env` is correct and the model name in `main.py` is valid

**Gmail not sending**
→ Delete `token.json` and reconnect Gmail from the Profile tab

**Port already in use**
→ Run on a different port: `uvicorn main:app --reload --port 8001`