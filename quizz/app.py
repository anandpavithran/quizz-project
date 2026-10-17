from io import BytesIO
import os
import re
import socket
import subprocess
import threading
import time
import pandas as pd
from PIL import Image
import qrcode
import streamlit as st

DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

QUESTIONS_FILE = os.path.join(DATA_DIR, "questions.xlsx")
RESULTS_FILE = os.path.join(DATA_DIR, "quiz_results.xlsx")
BANNER_IMAGE = "banner.jpg"
PORT = 8501

TOTAL_QUESTIONS = 10
MARKS_PER_QUESTION = 10
MAX_TOTAL_SCORE = TOTAL_QUESTIONS * MARKS_PER_QUESTION  # 100 Marks
TOTAL_QUIZ_TIME_SEC = 120  # 2 Minutes
TIME_PER_QUESTION_SEC = TOTAL_QUIZ_TIME_SEC // TOTAL_QUESTIONS  # 12s per question

FILE_LOCK = threading.Lock()

st.set_page_config(
    page_title="Red Hat Assessment Portal", page_icon="⚡", layout="centered"
)

# ==========================================
# CUSTOM RED & BLACK THEME + OPTION COLORS
# ==========================================
st.markdown(
    """
<style>
    .stApp {
        background-color: #0d0d0d;
        color: #ffffff;
        font-family: 'Red Hat Text', 'Segoe UI', Roboto, sans-serif;
    }
    section[data-testid="stSidebar"] {
        background-color: #161616;
        border-right: 2px solid #EE0000;
    }
    h1, h2, h3, h4, h5, h6 {
        color: #ffffff !important;
        font-weight: 700;
    }
    .red-accent {
        color: #EE0000 !important;
        text-shadow: 0 0 10px rgba(238, 0, 0, 0.4);
    }
    .custom-card {
        background-color: #1a1a1a;
        border: 1px solid #333333;
        border-left: 5px solid #EE0000;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    }
    .stButton>button {
        background: linear-gradient(135deg, #EE0000 0%, #b20710 100%) !important;
        color: #ffffff !important;
        border: 1px solid #ff3333 !important;
        border-radius: 6px !important;
        font-weight: 600 !important;
        padding: 10px 24px !important;
        transition: all 0.3s ease !important;
    }
    .stButton>button:hover {
        background: linear-gradient(135deg, #ff1e27 0%, #EE0000 100%) !important;
        box-shadow: 0 4px 16px rgba(238, 0, 0, 0.6) !important;
        transform: translateY(-2px);
    }
    .stTextInput>div>div>input {
        background-color: #222222 !important;
        color: #ffffff !important;
        border: 1px solid #444444 !important;
        border-radius: 6px !important;
    }

    /* --- Custom Colors for Quiz Options --- */
    div[role="radiogroup"] {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }
    div[role="radiogroup"] > label {
        background-color: #202428 !important;
        border: 1.5px solid #3d444d !important;
        border-radius: 8px !important;
        padding: 14px 18px !important;
        margin: 0px !important;
        cursor: pointer;
        transition: all 0.25s ease-in-out;
    }
    div[role="radiogroup"] > label:hover {
        background-color: #2a2f35 !important;
        border-color: #EE0000 !important;
        box-shadow: 0 0 10px rgba(238, 0, 0, 0.35) !important;
        transform: translateX(4px);
    }
    div[role="radiogroup"] label p {
        color: #f1f1f1 !important;
        font-size: 1.05rem !important;
        font-weight: 500 !important;
    }

    .stProgress > div > div > div > div {
        background-color: #EE0000 !important;
    }
    .stAlert {
        background-color: #1a1a1a !important;
        border: 1px solid #EE0000 !important;
        color: #ffffff !important;
    }
    hr {
        border-color: #333333 !important;
    }
</style>
""",
    unsafe_allow_html=True,
)


# --- Network, IP & MAC Utilities ---
def get_wifi_ip():
    env_ip = os.getenv("HOST_IP")
    if env_ip:
        return env_ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_client_ip():
    try:
        headers = st.context.headers
        if headers:
            for key in [
                "X-Forwarded-For",
                "x-forwarded-for",
                "X-Real-IP",
                "x-real-ip",
                "Host",
                "host",
            ]:
                val = headers.get(key)
                if val:
                    candidate = val.split(",")[0].split(":")[0].strip()
                    if candidate and candidate != "localhost":
                        return candidate
    except Exception:
        pass
    return "127.0.0.1"


def get_client_mac(client_ip):
    if client_ip in ("127.0.0.1", "localhost", get_wifi_ip()):
        return "LOCAL_HOST_DEVICE"

    mac_address = "UNKNOWN_MAC"
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", "1", client_ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if os.path.exists("/proc/net/arp"):
            with open("/proc/net/arp", "r") as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4 and parts[0] == client_ip:
                        candidate_mac = parts[3].upper()
                        if candidate_mac != "00:00:00:00:00:00":
                            return candidate_mac

        cmd = (
            ["arp", "-n", client_ip]
            if os.name != "nt"
            else ["arp", "-a", client_ip]
        )
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode(
            "utf-8", errors="ignore"
        )
        match = re.search(r"([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})", output)
        if match:
            mac_address = match.group(0).upper().replace("-", ":")
    except Exception:
        pass
    return mac_address


def generate_qr(url):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# Sidebar QR Code & Rules
quiz_url = f"http://{get_wifi_ip()}:{PORT}"
with st.sidebar:
    if os.path.exists(BANNER_IMAGE) and os.path.getsize(BANNER_IMAGE) > 0:
        try:
            st.image(Image.open(BANNER_IMAGE), use_container_width=True)
        except Exception:
            pass

    st.markdown(
        "### 📲 <span class='red-accent'>Scan to Join</span>",
        unsafe_allow_html=True,
    )
    st.image(generate_qr(quiz_url), caption="Scan on your mobile camera")
    st.code(quiz_url, language="text")
    st.markdown("---")
    st.markdown("#### **Rules & Details**")
    st.markdown(f"- Questions: **{TOTAL_QUESTIONS}** *(Randomized)*")
    st.markdown(f"- Total Marks: **{MAX_TOTAL_SCORE}** (10 Marks / Q)")
    st.markdown(f"- Total Time: **2 Minutes**")
    st.markdown(f"- Time per Question: **{TIME_PER_QUESTION_SEC}s**")
    st.markdown("- **Mode:** Strict Forward Only")


# --- Data Operations ---
def load_all_questions():
    if not os.path.exists(QUESTIONS_FILE):
        return None
    return pd.read_excel(QUESTIONS_FILE)


def save_final_results_threadsafe(shuffled_df):
    user = st.session_state.user_data
    responses = st.session_state.user_responses
    timings = st.session_state.question_timings

    marks_obtained = 0
    for idx, row in shuffled_df.iterrows():
        q_pos = idx + 1
        correct_col = str(row["CorrectAnswer"]).strip()
        correct_val = row[correct_col]
        if responses.get(f"Q{q_pos}_Response") == correct_val:
            marks_obtained += MARKS_PER_QUESTION

    total_time_seconds = round(sum(timings.values()), 1)
    mins, secs = divmod(int(total_time_seconds), 60)
    formatted_total_time = f"{mins:02d}:{secs:02d}"

    submission_entry = {
        "Timestamp": [pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")],
        "Name": [user["name"]],
        "Email": [user["email"]],
        "Mobile": [user["mobile"]],
        "IP_Address": [user["ip"]],
        "MAC_Address": [user["mac"]],
        "Marks_Obtained": [marks_obtained],
        "Total_Marks": [MAX_TOTAL_SCORE],
        "Percentage": [f"{(marks_obtained / MAX_TOTAL_SCORE) * 100:.1f}%"],
        "Total_Time_Seconds": [total_time_seconds],
        "Total_Time_Formatted": [formatted_total_time],
    }

    for idx, row in shuffled_df.iterrows():
        q_pos = idx + 1
        submission_entry[f"Q{q_pos}_Question"] = [row["Question"]]
        submission_entry[f"Q{q_pos}_Response"] = [
            responses.get(f"Q{q_pos}_Response", "No Answer")
        ]
        submission_entry[f"Q{q_pos}_Time_Sec"] = [
            timings.get(f"Q{q_pos}_Time", 0.0)
        ]

    new_df = pd.DataFrame(submission_entry)

    with FILE_LOCK:
        if os.path.exists(RESULTS_FILE):
            try:
                existing_df = pd.read_excel(RESULTS_FILE)
                updated_df = pd.concat([existing_df, new_df], ignore_index=True)
            except Exception:
                time.sleep(0.2)
                existing_df = pd.read_excel(RESULTS_FILE)
                updated_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            updated_df = new_df

        updated_df.to_excel(RESULTS_FILE, index=False)


# --- Session State Initialization ---
if "stage" not in st.session_state:
    st.session_state.stage = "register"
if "current_q_idx" not in st.session_state:
    st.session_state.current_q_idx = 0
if "user_responses" not in st.session_state:
    st.session_state.user_responses = {}
if "question_timings" not in st.session_state:
    st.session_state.question_timings = {}
if "q_start_time" not in st.session_state:
    st.session_state.q_start_time = None
if "candidate_questions" not in st.session_state:
    st.session_state.candidate_questions = None

df_all = load_all_questions()
client_ip = get_client_ip()
client_mac = get_client_mac(client_ip)

if os.path.exists(BANNER_IMAGE) and os.path.getsize(BANNER_IMAGE) > 0:
    try:
        _, col, _ = st.columns([1, 4, 1])
        with col:
            st.image(Image.open(BANNER_IMAGE), use_container_width=True)
    except Exception:
        pass

# ==========================================
# STEP 1: Registration Form
# ==========================================
if st.session_state.stage == "register":
    st.markdown(
        "## 🎯 <span class='red-accent'>Candidate Registration</span>",
        unsafe_allow_html=True,
    )
    st.caption("Fill in your details. Timer initiates immediately once you proceed.")

    with st.form("registration_form"):
        name = st.text_input("Full Name *")
        email = st.text_input("Email Address *")
        mobile = st.text_input("Mobile Number *")
        start_btn = st.form_submit_button("🚀 Start Assessment")

    if start_btn:
        clean_name = name.strip()
        clean_email = email.strip().lower()
        clean_mobile = mobile.strip()

        if not clean_name or not clean_email or not clean_mobile:
            st.error("All fields are mandatory.")
        elif not re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", clean_email):
            st.error("Please enter a valid email format.")
        elif not (clean_mobile.isdigit() and len(clean_mobile) >= 10):
            st.error("Enter a valid 10-digit mobile number.")
        elif df_all is None or df_all.empty:
            st.error(f"Cannot load questions file '{QUESTIONS_FILE}'.")
        else:
            sample_size = min(TOTAL_QUESTIONS, len(df_all))
            shuffled_user_df = (
                df_all.sample(n=sample_size).reset_index(drop=True)
            )
            st.session_state.candidate_questions = shuffled_user_df

            st.session_state.user_data = {
                "name": clean_name,
                "email": clean_email,
                "mobile": clean_mobile,
                "ip": client_ip,
                "mac": client_mac,
            }
            st.session_state.stage = "quiz"
            st.session_state.current_q_idx = 0
            st.session_state.q_start_time = time.time()
            st.rerun()

# ==========================================
# STEP 2: Timed Question View (No Backtrack)
# ==========================================
elif st.session_state.stage == "quiz":
    df_questions = st.session_state.candidate_questions
    q_idx = st.session_state.current_q_idx

    if q_idx >= len(df_questions):
        save_final_results_threadsafe(df_questions)
        st.session_state.stage = "completed"
        st.rerun()

    row = df_questions.iloc[q_idx]
    q_num = q_idx + 1

    st.markdown(
        f"### ⚡ Question <span class='red-accent'>{q_num}</span> of {len(df_questions)}",
        unsafe_allow_html=True,
    )
    st.progress(q_num / len(df_questions))

    @st.fragment(run_every=1)
    def render_live_timer():
        elapsed = time.time() - st.session_state.q_start_time
        remaining = max(0, int(TIME_PER_QUESTION_SEC - elapsed))

        if remaining <= 3:
            st.error(f"🚨 Time Left: **{remaining}s** (Auto-submitting!)")
        else:
            st.markdown(
                f"⏱️ **Time Remaining:** <span class='red-accent' style='font-size:1.2rem; font-weight:bold;'>{remaining}s</span>",
                unsafe_allow_html=True,
            )

        if remaining == 0:
            spent_time = round(time.time() - st.session_state.q_start_time, 1)
            current_choice = st.session_state.get(
                f"radio_{q_idx}", "Timed Out / No Answer"
            )
            st.session_state.user_responses[f"Q{q_num}_Response"] = (
                current_choice
            )
            st.session_state.question_timings[f"Q{q_num}_Time"] = min(
                spent_time, float(TIME_PER_QUESTION_SEC)
            )

            st.session_state.current_q_idx += 1
            st.session_state.q_start_time = time.time()
            st.rerun()

    render_live_timer()

    st.markdown(
        f"<div class='custom-card'><h4>{row['Question']}</h4></div>",
        unsafe_allow_html=True,
    )

    options = [row["OptionA"], row["OptionB"], row["OptionC"], row["OptionD"]]
    valid_options = [opt for opt in options if pd.notna(opt)]

    selected_option = st.radio(
        "Choose your answer:",
        valid_options,
        index=None,
        key=f"radio_{q_idx}",
    )

    col1, col2 = st.columns([3, 2])
    with col2:
        if st.button("Submit & Next ➡️", use_container_width=True):
            spent_time = round(time.time() - st.session_state.q_start_time, 1)
            st.session_state.user_responses[f"Q{q_num}_Response"] = (
                selected_option if selected_option is not None else "No Answer"
            )
            st.session_state.question_timings[f"Q{q_num}_Time"] = min(
                spent_time, float(TIME_PER_QUESTION_SEC)
            )

            st.session_state.current_q_idx += 1
            st.session_state.q_start_time = time.time()
            st.rerun()

# ==========================================
# STEP 3: Submission Confirmation Only
# ==========================================
elif st.session_state.stage == "completed":
    st.balloons()
    st.markdown(
        """
        <div class='custom-card' style='text-align: center; padding: 40px 20px; border-left: 5px solid #EE0000;'>
            <h2 style='color: #ffffff; margin-bottom: 15px;'>✅ Done.</h2>
            <h4 style='color: #cccccc;'>Thanks for attending!</h4>
        </div>
        """,
        unsafe_allow_html=True,
    )
