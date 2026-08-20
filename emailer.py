import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import config


def format_run_date_for_subject(run_date_str):
    try:
        return datetime.strptime(run_date_str, "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        return run_date_str


def model_short_name(model_name):
    if not model_name:
        return "primary model"
    return model_name.split("/")[-1].replace("-", " ")


def build_email_subject(run_date_str, model_name=None, summary_counts=None, is_comparison=False):
    date_part = format_run_date_for_subject(run_date_str)
    if is_comparison:
        model_part = "model comparison"
    else:
        model_part = model_short_name(model_name)
    total_items = ""
    if summary_counts:
        total = sum(int(summary_counts.get(key, 0)) for key in ["reports", "podcasts", "events"])
        total_items = f" - {total} items"
    return f"Think Tank Scan - {date_part} - {model_part}{total_items}"


def parse_recipients(value):
    if isinstance(value, (list, tuple, set)):
        raw_parts = value
    else:
        raw_parts = str(value or "").replace(";", ",").split(",")
    return [part.strip() for part in raw_parts if part and part.strip()]


def send_report_email(run_date_str, html_content, pdf_path, model_name=None, summary_counts=None, is_comparison=False):
    """
    Sends the generated HTML report and PDF attachment via Gmail SMTP.
    """
    # Verify SMTP configurations are present
    if not config.SMTP_SENDER_EMAIL or not config.SMTP_SENDER_PASSWORD:
        print("[!] Warning: SMTP email configurations not set in .env. Skipping email transmission.")
        print("    Please set SMTP_SENDER_EMAIL and SMTP_SENDER_PASSWORD to send email reports.")
        return False
        
    sender = config.SMTP_SENDER_EMAIL
    password = config.SMTP_SENDER_PASSWORD
    recipients = parse_recipients(config.SMTP_RECEIVER_EMAIL)
    if not recipients:
        print("[!] Warning: no SMTP recipients configured. Skipping email transmission.")
        return False
    recipient_header = ", ".join(recipients)
    
    subject = build_email_subject(run_date_str, model_name, summary_counts, is_comparison)
    
    print(f"[*] Preparing email to {recipient_header}...")
    
    # Create Multipart message
    msg = MIMEMultipart("mixed")
    msg["From"] = sender
    msg["To"] = recipient_header
    msg["Subject"] = subject
    
    # Attach HTML body
    msg_alternative = MIMEMultipart("alternative")
    html_part = MIMEText(html_content, "html", "utf-8")
    msg_alternative.attach(html_part)
    msg.attach(msg_alternative)
    
    # Attach PDF if path is valid and exists
    if pdf_path and os.path.exists(pdf_path):
        print(f"[*] Attaching PDF report: {pdf_path}...")
        try:
            with open(pdf_path, "rb") as f:
                pdf_part = MIMEBase("application", "pdf")
                pdf_part.set_payload(f.read())
                
            encoders.encode_base64(pdf_part)
            pdf_basename = os.path.basename(pdf_path)
            pdf_part.add_header(
                "Content-Disposition",
                f"attachment; filename={pdf_basename}"
            )
            msg.attach(pdf_part)
        except Exception as e:
            print(f"[-] Failed to read or attach PDF: {e}")
            return False
    else:
        print(f"[!] Warning: PDF attachment not found at path {pdf_path}.")
        
    # Send email via Gmail SMTP
    try:
        print("[*] Connecting to Gmail SMTP server (smtp.gmail.com:587)...")
        # Gmail SMTP typically uses TLS on port 587
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls() # Secure connection
        server.ehlo()
        
        print("[*] Logging in to SMTP server...")
        server.login(sender, password)
        
        print(f"[*] Dispatching email to {recipient_header}...")
        server.sendmail(sender, recipients, msg.as_string())
        server.quit()
        print("[+] Email sent successfully!")
        return True
        
    except Exception as e:
        print(f"[-] SMTP Email transmission failed: {e}")
        print("    Ensure you are using a 16-character Gmail App Password if 2-Step Verification is enabled.")
        return False

if __name__ == "__main__":
    # Small test setup
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    config.SMTP_SENDER_EMAIL = os.getenv("SMTP_SENDER_EMAIL")
    config.SMTP_SENDER_PASSWORD = os.getenv("SMTP_SENDER_PASSWORD")
    config.SMTP_RECEIVER_EMAIL = os.getenv("SMTP_RECEIVER_EMAIL", ", ".join(config.DEFAULT_SMTP_RECEIVER_EMAILS))
    
    # Dummy email test
    test_html = "<h1>Test report</h1><p>If you see this, email sending works.</p>"
    send_report_email("TEST-DATE", test_html, None)
