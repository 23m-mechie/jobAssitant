from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
from pydantic import BaseModel
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import google.generativeai as genai
import json
import os
import base64
import mimetypes
from email.message import EmailMessage
from googleapiclient.discovery import build
from typing import List

load_dotenv()

app = FastAPI(title="Job Application Assistant")

DATA_DIR = "data"
PROFILE_PATH = os.path.join(DATA_DIR, "profile.json")
RESUME_PATH = os.path.join(DATA_DIR, "resume.pdf")
CREDENTIALS_PATH = "credentials.json"
TOKEN_PATH = "token.json"

# Gmail API scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]
w
# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-3.5-flash")

os.makedirs(DATA_DIR, exist_ok=True)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Profile model ──
class ProfileData(BaseModel):
    work_experience: str = ""
    projects: str = ""
    cover_letter: str = ""

class DraftRequest(BaseModel):
    job_title: str
    company: str
    requirements: str

class SendEmailRequest(BaseModel):
    to_email: str
    subject: str
    body: str

class FormQARequest(BaseModel):
    questions: str

# ── Gmail helpers ──
def get_gmail_creds():
    """Load saved credentials, refresh if expired, return None if not authenticated."""
    if not os.path.exists(TOKEN_PATH):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, GMAIL_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        except Exception:
            return None
    return creds if creds and creds.valid else None


def get_gmail_service():
    """Build Gmail API service from valid credentials."""
    creds = get_gmail_creds()
    if not creds:
        return None
    try:
        return build("gmail", "v1", credentials=creds)
    except Exception:
        return None


# ── Routes ──

@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/health")
async def health():
    gmail_ok = get_gmail_creds() is not None
    return {
        "status": "ok",
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY")),
        "gmail_connected": gmail_ok,
    }


@app.get("/api/gmail/status")
async def gmail_status():
    creds = get_gmail_creds()
    if not creds:
        return {"connected": False}
    try:
        # Try to get email using the oauth2 service (requires userinfo.email scope)
        oauth2_service = build("oauth2", "v2", credentials=creds)
        user_info = oauth2_service.userinfo().get().execute()
        return {"connected": True, "email": user_info.get("email", "Connected")}
    except Exception as e:
        # Fallback: If we can't get the email but creds exist, still show as connected
        # unless it's a definite auth error
        if "insufficient authentication scopes" in str(e).lower():
            return {"connected": True, "email": "Connected (re-auth for email)"}
        return {"connected": False, "error": str(e)}


@app.post("/api/gmail/connect")
async def gmail_connect():
    if not os.path.exists(CREDENTIALS_PATH):
        return JSONResponse(status_code=400, content={"error": "credentials.json missing"})
    
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, GMAIL_SCOPES)
    # run_local_server will open the system browser and block until auth is done
    creds = flow.run_local_server(port=0, open_browser=True)
    
    with open(TOKEN_PATH, "w") as token:
        token.write(creds.to_json())
    
    return {"status": "connected"}


@app.post("/api/gmail/disconnect")
async def gmail_disconnect():
    if os.path.exists(TOKEN_PATH):
        os.remove(TOKEN_PATH)
    return {"status": "disconnected"}


@app.post("/api/extract-jd")
async def extract_jd(files: List[UploadFile] = File(...)):
    if not os.getenv("GEMINI_API_KEY"):
        return JSONResponse(status_code=500, content={"error": "GEMINI_API_KEY not set in .env"})

    image_parts = []
    for file in files:
        content = await file.read()
        image_parts.append({
            "mime_type": file.content_type,
            "data": content
        })

    prompt = """
    You are an expert recruitment assistant. I am providing 1-3 screenshots of a job description.
    Please extract the following details accurately:
    1. Job Title
    2. Company Name
    3. Requirements Summary (a concise bulleted list of key skills and experience needed)
    4. Recipient Email (Look for 'apply to', 'contact', or any email address provided for applications. If not found, return an empty string)

    Return the result ONLY as a valid JSON object with these keys:
    "job_title", "company", "requirements", "recipient_email"
    """

    try:
        response = model.generate_content([prompt] + image_parts)
        # Clean potential markdown from response if Gemini wraps it in ```json
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
            
        data = json.loads(text)
        return data
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"AI Extraction failed: {str(e)}"})


@app.post("/api/generate-draft")
async def generate_draft(req: DraftRequest):
    if not os.getenv("GEMINI_API_KEY"):
        return JSONResponse(status_code=500, content={"error": "GEMINI_API_KEY not set"})
        
    profile = {"work_experience": "", "projects": "", "cover_letter": ""}
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, "r") as f:
            profile = json.load(f)

    prompt = f"""
    You are an expert career consultant drafting a cold email to apply for a job.
    
    JOB DETAILS:
    Title: {req.job_title}
    Company: {req.company}
    Requirements: {req.requirements}
    
    CANDIDATE PROFILE:
    Experience: {profile.get('work_experience', '')}
    Projects: {profile.get('projects', '')}
    Cover Letter Template (Use this for tone/structure if available): {profile.get('cover_letter', '')}
    
    INSTRUCTIONS:
    1. Write a concise, compelling cold email (subject line and body). 
    2. The Subject line MUST be strictly formatted as exactly: "{req.job_title} Role" (do not add the candidate's name or university).
    3. Match the tone of the provided cover letter template, but customize the content to directly address the Job Requirements using facts from the Candidate Profile.
    4. Keep it crisp, highly professional, and ready to send.
    5. You MUST end the email body with this exact signature, without altering it or making up other details:
       
       Best regards,
       
       Sonu Kumar
       sonu.ku.engg@gmail.com
       +918434011521
       linkedin.com/in/me19b173
       
    6. Return ONLY the raw email text (Subject line first, then a couple blank lines, then the body). Do not wrap in markdown quotes.
    """

    try:
        response = model.generate_content(prompt)
        return {"draft_text": response.text.strip()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/send-email")
async def send_email(req: SendEmailRequest):
    service = get_gmail_service()
    if not service:
        return JSONResponse(status_code=401, content={"error": "Gmail is not connected. Please connect from Profile."})
        
    try:
        message = EmailMessage()
        message.set_content(req.body)
        message["To"] = req.to_email
        message["Subject"] = req.subject
        
        # Determine sender email to populate "From" header
        try:
            profile = service.users().getProfile(userId='me').execute()
            message["From"] = profile.get("emailAddress", "me")
        except:
            message["From"] = "me"
        
        # Attach resume if exists
        if os.path.exists(RESUME_PATH):
            mime_type, _ = mimetypes.guess_type(RESUME_PATH)
            if not mime_type:
                mime_type = "application/pdf"
            main_type, sub_type = mime_type.split("/", 1)
            
            with open(RESUME_PATH, "rb") as f:
                message.add_attachment(
                    f.read(),
                    maintype=main_type,
                    subtype=sub_type,
                    filename="resume.pdf"
                )
        
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        create_message = {"raw": encoded_message}
        
        send_message = service.users().messages().send(
            userId="me",
            body=create_message
        ).execute()
        
        return {"status": "sent", "message_id": send_message["id"]}
    
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/form-qa")
async def generate_form_qa(req: FormQARequest):
    if not os.getenv("GEMINI_API_KEY"):
        return JSONResponse(status_code=500, content={"error": "GEMINI_API_KEY not set"})
        
    profile = {"work_experience": "", "projects": ""}
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, "r") as f:
            profile = json.load(f)

    prompt = f"""
    You are an expert career consultant answering job application forms for a candidate.
    
    CANDIDATE PROFILE:
    Experience: {profile.get('work_experience', '')}
    Projects: {profile.get('projects', '')}
    
    FORM QUESTIONS (Raw Paste):
    {req.questions}
    
    INSTRUCTIONS:
    1. Identify all the distinct questions asked in the raw paste.
    2. Answer each question accurately based ONLY on the Candidate Profile. Give professional and concise answers.
    3. Output the result strictly as a JSON array of objects in this format:
       [
         {{"question": "What is your biggest achievement?", "answer": "- Built X\\n- Scaled Y"}},
         ...
       ]
    4. Do not include markdown formatting like ```json or any conversational text around the JSON array. Output just the raw JSON.
    """

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:-3].strip()
        elif text.startswith("```"):
            text = text[3:-3].strip()
            
        data = json.loads(text)
        return {"answers": data}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/profile")
async def get_profile():
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, "r") as f:
            return json.load(f)
    return {"work_experience": "", "projects": "", "cover_letter": ""}


@app.post("/api/profile")
async def save_profile(profile: ProfileData):
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile.model_dump(), f, indent=2)
    return {"status": "saved"}


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse(status_code=400, content={"error": "Only PDF files are accepted"})
    content = await file.read()
    with open(RESUME_PATH, "wb") as f:
        f.write(content)
    return {"status": "uploaded", "filename": file.filename, "size_bytes": len(content)}


@app.get("/api/resume/status")
async def resume_status():
    if os.path.exists(RESUME_PATH):
        size = os.path.getsize(RESUME_PATH)
        return {"exists": True, "size_bytes": size}
    return {"exists": False}