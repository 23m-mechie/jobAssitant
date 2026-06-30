from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from dotenv import load_dotenv
from pydantic import BaseModel
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
import google.generativeai as genai
import json
import os
import base64
import mimetypes
from email.message import EmailMessage
from googleapiclient.discovery import build
from typing import List, Optional
import jwt
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.cloud import storage

load_dotenv()

app = FastAPI(title="Job Application Assistant")

DATA_DIR = "data"
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key-12345")
CREDENTIALS_PATH = "credentials.json"
security = HTTPBearer()

# Initialize GCS client if bucket is set
project_id = os.getenv("GOOGLE_PROJECT_ID")
storage_client = storage.Client(project=project_id) if GCS_BUCKET_NAME else None

def get_bucket():
    if not storage_client or not GCS_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="GCS_BUCKET_NAME is not configured")
    return storage_client.bucket(GCS_BUCKET_NAME)

def read_gcs(path: str) -> Optional[bytes]:
    try:
        blob = get_bucket().blob(path)
        if blob.exists():
            return blob.download_as_bytes()
    except Exception as e:
        print(f"GCS Read Error ({path}): {e}")
    return None

def write_gcs(path: str, data: bytes, content_type: str = "application/octet-stream"):
    try:
        blob = get_bucket().blob(path)
        blob.upload_from_string(data, content_type=content_type)
    except Exception as e:
        print(f"GCS Write Error ({path}): {e}")

def delete_gcs(path: str):
    try:
        blob = get_bucket().blob(path)
        if blob.exists():
            blob.delete()
    except Exception as e:
        pass

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return email
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# Gmail API scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-3.5-flash")

os.makedirs(DATA_DIR, exist_ok=True)

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Profile model ──
class ProfileData(BaseModel):
    name: str = ""
    phone: str = ""
    linkedin: str = ""
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
def get_gmail_creds(email: str):
    """Load saved credentials from GCS, refresh if expired."""
    token_path = f"users/{email}/token.json"
    token_data = read_gcs(token_path)
    if not token_data:
        return None
    
    try:
        creds_dict = json.loads(token_data.decode('utf-8'))
        creds = Credentials.from_authorized_user_info(creds_dict, GMAIL_SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            write_gcs(token_path, creds.to_json().encode('utf-8'), "application/json")
        return creds if creds and creds.valid else None
    except Exception:
        return None


def get_gmail_service(email: str):
    """Build Gmail API service from valid credentials."""
    creds = get_gmail_creds(email)
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
    return {
        "status": "ok",
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY")),
        "gcs_bucket_set": bool(GCS_BUCKET_NAME)
    }


@app.get("/api/gmail/status")
async def gmail_status(email: str = Depends(get_current_user)):
    creds = get_gmail_creds(email)
    if not creds:
        return {"connected": False}
    return {"connected": True, "email": email}


class AuthCodeRequest(BaseModel):
    code: str
    redirect_uri: str
    code_verifier: str

@app.get("/api/gmail/auth-url")
async def gmail_auth_url(redirect_uri: str):
    if not os.path.exists(CREDENTIALS_PATH):
        return JSONResponse(status_code=400, content={"error": "credentials.json missing on server"})
    flow = Flow.from_client_secrets_file(CREDENTIALS_PATH, scopes=GMAIL_SCOPES)
    flow.redirect_uri = redirect_uri
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
    return {"auth_url": auth_url, "code_verifier": flow.code_verifier}


@app.post("/api/gmail/connect")
async def gmail_connect(req: AuthCodeRequest):
    if not os.path.exists(CREDENTIALS_PATH):
        return JSONResponse(status_code=400, content={"error": "credentials.json missing on server"})
    
    try:
        flow = Flow.from_client_secrets_file(CREDENTIALS_PATH, scopes=GMAIL_SCOPES)
        flow.redirect_uri = req.redirect_uri
        flow.fetch_token(code=req.code, code_verifier=req.code_verifier)
        creds = flow.credentials
        
        # Get user email
        oauth2_service = build("oauth2", "v2", credentials=creds)
        user_info = oauth2_service.userinfo().get().execute()
        email = user_info.get("email")
        
        if not email:
            return JSONResponse(status_code=400, content={"error": "Could not retrieve email from Google"})
            
        # Save token to GCS
        write_gcs(f"users/{email}/token.json", creds.to_json().encode('utf-8'), "application/json")
        
        # Create session JWT
        token = jwt.encode({"email": email}, JWT_SECRET, algorithm="HS256")
        
        return {"status": "connected", "token": token, "email": email}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/gmail/disconnect")
async def gmail_disconnect(email: str = Depends(get_current_user)):
    delete_gcs(f"users/{email}/token.json")
    return {"status": "disconnected"}


@app.post("/api/extract-jd")
async def extract_jd(files: List[UploadFile] = File(...), email: str = Depends(get_current_user)):
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
async def generate_draft(req: DraftRequest, email: str = Depends(get_current_user)):
    if not os.getenv("GEMINI_API_KEY"):
        return JSONResponse(status_code=500, content={"error": "GEMINI_API_KEY not set"})
        
    profile = {"name": "", "phone": "", "linkedin": "", "work_experience": "", "projects": "", "cover_letter": ""}
    profile_data = read_gcs(f"users/{email}/profile.json")
    if profile_data:
        profile = json.loads(profile_data.decode('utf-8'))

    # Build signature dynamically from the logged-in user's profile
    sender_name = profile.get("name") or email.split("@")[0]
    sender_phone = profile.get("phone", "")
    sender_linkedin = profile.get("linkedin", "")
    signature_lines = [f"\nBest regards,\n\n{sender_name}", email]
    if sender_phone:
        signature_lines.append(sender_phone)
    if sender_linkedin:
        signature_lines.append(sender_linkedin)
    signature = "\n".join(signature_lines)

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
    5. CRITICAL FORMATTING RULE: Do NOT use any markdown formatting. No **bold**, no *italic*, no bullet dashes with asterisks. Use plain text only. For bullet points, use a simple hyphen followed by a space (e.g., "- Point one").
    6. You MUST end the email body with EXACTLY this signature block and nothing else after it:
{signature}
    7. Return ONLY the raw email text (Subject line first, then a blank line, then the body). Do not wrap in markdown quotes.
    """

    try:
        response = model.generate_content(prompt)
        return {"draft_text": response.text.strip()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/send-email")
async def send_email(req: SendEmailRequest, email: str = Depends(get_current_user)):
    service = get_gmail_service(email)
    if not service:
        return JSONResponse(status_code=401, content={"error": "Gmail is not connected. Please connect from Profile."})
        
    try:
        import email.mime.multipart as mime_mp
        import email.mime.text as mime_text
        import email.mime.base as mime_base
        import email.mime.application as mime_app
        from email import encoders as email_encoders

        # Build multipart message (plain + HTML) for proper Gmail rendering
        message = mime_mp.MIMEMultipart("mixed")
        message["To"] = req.to_email
        message["Subject"] = req.subject
        message["From"] = email

        # Convert plain text body to HTML (preserves line breaks, full-width layout)
        html_body = "<html><body style=\"font-family: Arial, sans-serif; font-size: 15px; line-height: 1.6; color: #222;\">"
        for line in req.body.splitlines():
            stripped = line.strip()
            if stripped:
                html_body += f"<p style=\"margin: 0 0 8px 0;\">{stripped}</p>"
            else:
                html_body += "<br>"
        html_body += "</body></html>"

        # Attach plain text and HTML alternatives
        alt_part = mime_mp.MIMEMultipart("alternative")
        alt_part.attach(mime_text.MIMEText(req.body, "plain"))
        alt_part.attach(mime_text.MIMEText(html_body, "html"))
        message.attach(alt_part)

        # Attach resume if exists
        resume_data = read_gcs(f"users/{email}/resume.pdf")
        if resume_data:
            pdf_part = mime_base.MIMEBase("application", "pdf")
            pdf_part.set_payload(resume_data)
            email_encoders.encode_base64(pdf_part)
            pdf_part.add_header("Content-Disposition", "attachment", filename="resume.pdf")
            message.attach(pdf_part)

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
async def generate_form_qa(req: FormQARequest, email: str = Depends(get_current_user)):
    if not os.getenv("GEMINI_API_KEY"):
        return JSONResponse(status_code=500, content={"error": "GEMINI_API_KEY not set"})
        
    profile = {"work_experience": "", "projects": ""}
    profile_data = read_gcs(f"users/{email}/profile.json")
    if profile_data:
        profile = json.loads(profile_data.decode('utf-8'))

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
async def get_profile(email: str = Depends(get_current_user)):
    profile_data = read_gcs(f"users/{email}/profile.json")
    if profile_data:
        return json.loads(profile_data.decode('utf-8'))
    return {"work_experience": "", "projects": "", "cover_letter": ""}


@app.post("/api/profile")
async def save_profile(profile: ProfileData, email: str = Depends(get_current_user)):
    write_gcs(f"users/{email}/profile.json", json.dumps(profile.model_dump(), indent=2).encode('utf-8'), "application/json")
    return {"status": "saved"}


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...), email: str = Depends(get_current_user)):
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse(status_code=400, content={"error": "Only PDF files are accepted"})
    content = await file.read()
    write_gcs(f"users/{email}/resume.pdf", content, "application/pdf")
    return {"status": "uploaded", "filename": file.filename, "size_bytes": len(content)}


@app.get("/api/resume/status")
async def resume_status(email: str = Depends(get_current_user)):
    data = read_gcs(f"users/{email}/resume.pdf")
    if data:
        return {"exists": True, "size_bytes": len(data)}
    return {"exists": False}